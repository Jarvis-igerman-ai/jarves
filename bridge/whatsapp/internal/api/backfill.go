package api

// On-demand history backfill (JAR-272, PROTOCOL.md section 3a). While a backfill runs, the bridge asks the phone,
// one readable chat at a time, for BackfillBatch messages before the oldest message it knows, and repeats with the
// oldest answered message as the new anchor until an answer brings nothing older (no age limit unless `since`).
// Answered messages of readable chats go to a bounded in-memory buffer ONLY: never the store, never the sealed
// snapshot. Core drains it with GET /history/backfill?cursor=N; each read drops every buffered message up to N.
// Stop, kill, mode off, keep-nothing and unlink clear the buffer. Logs carry counts only.

import (
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/engine"
	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/msgstore"
)

const (
	BackfillBatch  = 50       // messages per on-demand request (whatsmeow's recommendation)
	backfillBufMax = 2000     // default buffer bound in messages; requests pause until Core drains
	backfillBytes  = 16 << 20 // default buffer bound in bytes (texts can be long)
	backfillWait   = 60 * time.Second
	// backfillRetries: a truncated answer is asked again from the same anchor (after Core drains) this many times
	// before the chat moves on and the overflow counts as dropped.
	backfillRetries = 3
)

type bfChat struct {
	chat   string
	anchor engine.HistoryAnchor
}

type bfMsg struct {
	seq int
	m   msgstore.Message
}

// backfill is guarded by Server.mu.
type backfill struct {
	state     string // idle | running | done | stopped | failed
	queue     []bfChat
	since     float64
	waiting   bool                 // a request for queue[0] is out
	gen       int                  // invalidates the timer of an earlier request
	older     bool                 // the current answer brought a message older than the anchor
	reqAnchor engine.HistoryAnchor // the anchor of the request that is out
	truncated bool                 // the current answer did not fit the buffer
	retries   int                  // truncated answers of the current chat asked again
	needDrain bool                 // a truncated answer waits for Core to free buffer space before it is asked again
	dropped   int                  // messages that did not fit the buffer (their answer is asked again)
	total     int
	done      int
	skipped   int // readable chats without a stored message to anchor on
	received  int
	buf       []bfMsg
	bytes     int // approximate size of buf
	seq       int
	errorCode string
}

func (s *Server) bfBytesMax() int {
	if s.cfg.BackfillBytes > 0 {
		return s.cfg.BackfillBytes
	}
	return backfillBytes
}

// msgSize approximates the memory a buffered message holds.
func msgSize(m msgstore.Message) int {
	n := 64 + len(m.ID) + len(m.Chat) + len(m.SenderName) + len(m.Kind) + len(m.Text)
	if m.Media != nil {
		n += len(m.Media.Mime) + len(m.Media.Caption)
	}
	return n
}

// bfBufMax is never below one batch: a smaller bound would pause the backfill forever.
func (s *Server) bfBufMax() int {
	if s.cfg.BackfillBuffer > 0 {
		return max(s.cfg.BackfillBuffer, BackfillBatch)
	}
	return backfillBufMax
}

// bfClearLocked ends a backfill and drops the buffer.
func (s *Server) bfClearLocked(state string) {
	if s.bf.state == "" || s.bf.state == "idle" {
		return
	}
	wipe(s.bf.buf)
	s.bf.buf, s.bf.queue, s.bf.waiting, s.bf.bytes = nil, nil, false, 0
	s.bf.gen++
	if s.bf.state == "running" || state != "stopped" {
		s.bf.state = state
	}
}

func (s *Server) bfStop() {
	s.mu.Lock()
	running := s.bf.state == "running"
	s.bfClearLocked("stopped")
	s.mu.Unlock()
	if running {
		s.log.Info("WA_BACKFILL", "History backfill stopped.", F{"state": "stopped"})
	}
}

// wipe zeroes buffered messages so their text does not linger in a slice's backing array.
func wipe(l []bfMsg) {
	for i := range l {
		l[i] = bfMsg{}
	}
}

// bfNext sends the request for the current chat when nothing is out and the buffer has room. Called without s.mu.
func (s *Server) bfNext() {
	s.mu.Lock()
	b := &s.bf
	if b.state != "running" || b.waiting || b.needDrain || len(b.buf)+BackfillBatch > s.bfBufMax() || b.bytes >= s.bfBytesMax() {
		s.mu.Unlock()
		return
	}
	for len(b.queue) > 0 && !s.readableLocked(b.queue[0].chat) { // left the read scope: never ask for it again
		b.queue, b.retries = b.queue[1:], 0
		b.done++
	}
	if len(b.queue) == 0 {
		b.state = "done"
		f := F{"state": "done", "chats": b.done, "received": b.received}
		s.mu.Unlock()
		s.log.Info("WA_BACKFILL", "History backfill finished.", f)
		return
	}
	c := b.queue[0]
	b.waiting, b.older, b.truncated, b.reqAnchor = true, false, false, c.anchor
	b.gen++
	gen := b.gen
	s.mu.Unlock()
	err := s.eng.RequestHistory(s.ctx, c.chat, c.anchor, BackfillBatch)
	s.mu.Lock()
	if gen != b.gen {
		s.mu.Unlock()
		return
	}
	if err != nil {
		b.waiting, b.state, b.errorCode = false, "failed", "WA_SEND_FAILED"
		if errors.Is(err, engine.ErrNotConnected) {
			b.errorCode = "WA_NOT_CONNECTED"
		}
		s.mu.Unlock()
		s.log.Warn("WA_BACKFILL", "History request failed.", F{"state": "failed"})
		return
	}
	wait := s.cfg.BackfillWait
	if wait <= 0 {
		wait = backfillWait
	}
	s.mu.Unlock()
	time.AfterFunc(wait, func() { s.bfAnswered(gen, true) }) // the phone may never answer
}

// bfMessage buffers one on-demand history message (onEvent, without s.mu).
func (s *Server) bfMessage(m msgstore.Message) {
	s.mu.Lock()
	defer s.mu.Unlock()
	b := &s.bf
	// only the answer to the request that is out, for a readable chat; nothing unsolicited
	if b.state != "running" || !b.waiting || len(b.queue) == 0 || b.queue[0].chat != m.Chat ||
		!s.readableLocked(m.Chat) || s.pol.KeepNothing || (b.since > 0 && m.TS < b.since) {
		return
	}
	size := msgSize(m)
	if len(b.buf) >= s.bfBufMax() || b.bytes+size > s.bfBytesMax() { // hard bounds, whatever the phone sends
		b.truncated = true
		b.dropped++
		return
	}
	if m.TS < b.queue[0].anchor.TS {
		b.queue[0].anchor = engine.HistoryAnchor{ID: m.ID, FromMe: m.FromMe, TS: m.TS}
		b.older = true
	}
	b.seq++
	b.received++
	b.bytes += size
	b.buf = append(b.buf, bfMsg{seq: b.seq, m: m})
}

// bfAnswer handles the end of one on-demand answer.
func (s *Server) bfAnswer(chats []string) {
	s.mu.Lock()
	gen, ok := s.bf.gen, s.bf.waiting && len(s.bf.queue) > 0
	if ok && len(chats) > 0 {
		ok = false
		for _, c := range chats {
			ok = ok || c == s.bf.queue[0].chat
		}
	}
	s.mu.Unlock()
	if ok {
		s.bfAnswered(gen, false)
	}
}

// bfAnswered finishes the request gen: the chat is done when the answer brought nothing older (or timed out).
func (s *Server) bfAnswered(gen int, timeout bool) {
	s.mu.Lock()
	b := &s.bf
	if gen != b.gen || !b.waiting {
		s.mu.Unlock()
		return
	}
	b.waiting = false
	if timeout && (s.eng == nil || !s.eng.IsConnected()) { // the phone could not answer: the chat is not finished
		b.state, b.errorCode = "failed", "WA_NOT_CONNECTED"
		s.mu.Unlock()
		s.log.Warn("WA_BACKFILL", "History request unanswered while offline.", F{"state": "failed"})
		return
	}
	switch {
	case b.truncated && !timeout && b.retries < backfillRetries:
		// part of the answer did not fit: ask again from the same anchor once Core has drained (no loss)
		b.queue[0].anchor = b.reqAnchor
		b.retries++
		b.needDrain = true
	case timeout || !b.older || (b.since > 0 && b.queue[0].anchor.TS < b.since):
		b.queue, b.retries = b.queue[1:], 0
		b.done++
	}
	s.mu.Unlock()
	s.bfNext()
}

func (s *Server) bfProgressLocked() map[string]any {
	b := &s.bf
	st := b.state
	if st == "" {
		st = "idle"
	}
	var ec any
	if b.errorCode != "" {
		ec = b.errorCode
	}
	return map[string]any{"state": st, "chats_total": b.total, "chats_done": b.done, "chats_skipped": b.skipped,
		"received": b.received, "buffered": len(b.buf), "dropped": b.dropped, "error": ec, "batch": BackfillBatch}
}

// hBackfillStart: POST /history/backfill {"chat"?, "since"?} (ADMIN).
func (s *Server) hBackfillStart(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Chat  string  `json:"chat"`
		Since float64 `json:"since"`
	}
	if !readBody(w, r, &req) || !s.readGuard(w) {
		return
	}
	only, ok := "", req.Since >= 0
	if req.Chat != "" {
		only, ok = normalizeChat(req.Chat)
		ok = ok && req.Since >= 0
	}
	if !ok {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	s.mu.Lock()
	killed, keepNothing, running := s.killed, s.pol.KeepNothing, s.bf.state == "running"
	s.mu.Unlock()
	switch {
	case s.cfg.StoreLocked:
		writeErr(w, 409, "WA_STORE_LOCKED", nil)
		return
	case killed:
		writeErr(w, 423, "WA_KILLED", nil)
		return
	case keepNothing:
		writeErr(w, 409, "WA_KEEP_NOTHING", nil)
		return
	case running:
		writeErr(w, 409, "WA_BACKFILL_RUNNING", nil)
		return
	case s.eng == nil || !s.eng.IsConnected():
		writeErr(w, 503, "WA_NOT_CONNECTED", nil)
		return
	}
	all, err := s.st.AllChats(r.Context())
	if err != nil {
		writeErr(w, 500, "WA_INTERNAL", nil)
		return
	}
	nb := backfill{state: "running", since: req.Since}
	for _, c := range all {
		if (only != "" && c.ID != only) || !s.readable(c.ID) {
			continue
		}
		nb.total++
		if m, ok := s.st.Oldest(r.Context(), c.ID); ok {
			nb.queue = append(nb.queue, bfChat{chat: c.ID, anchor: engine.HistoryAnchor{ID: m.ID, FromMe: m.FromMe, TS: m.TS}})
		} else {
			nb.skipped++
			nb.done++
		}
	}
	if only != "" && nb.total == 0 {
		writeErr(w, 404, "WA_CHAT_NOT_FOUND", nil)
		return
	}
	s.mu.Lock()
	if s.bf.state == "running" { // a concurrent start won the race
		s.mu.Unlock()
		writeErr(w, 409, "WA_BACKFILL_RUNNING", nil)
		return
	}
	nb.gen = s.bf.gen + 1
	s.bf = nb
	s.mu.Unlock()
	s.log.Info("WA_BACKFILL", "History backfill started.", F{"chats": nb.total, "skipped": nb.skipped})
	s.bfNext()
	s.mu.Lock()
	p := s.bfProgressLocked()
	s.mu.Unlock()
	writeJSON(w, 200, p)
}

// hBackfillGet: GET /history/backfill?cursor=N&limit= (ADMIN): drops buffered messages up to N, returns progress and
// the next readable messages after N; next_cursor acknowledges them on the following read.
func (s *Server) hBackfillGet(w http.ResponseWriter, r *http.Request) {
	cursor, err := strconv.Atoi(r.URL.Query().Get("cursor"))
	limit, ok := queryLimit(r, 500, 500)
	if r.URL.Query().Get("cursor") == "" {
		cursor, err = 0, nil
	}
	if err != nil || cursor < 0 || !ok {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	if !s.readGuard(w) {
		return
	}
	s.mu.Lock()
	b := &s.bf
	keep := make([]bfMsg, 0, len(b.buf))
	for _, x := range b.buf {
		if x.seq > cursor && s.readableLocked(x.m.Chat) { // out of read scope: dropped from memory too
			keep = append(keep, x)
		} else {
			b.bytes -= msgSize(x.m)
		}
	}
	if len(keep) < len(b.buf) {
		b.needDrain = false
	}
	wipe(b.buf) // acknowledged text must not linger in the old backing array
	b.buf = keep
	s.mu.Unlock()
	s.bfNext() // room again: resume a paused backfill
	s.mu.Lock()
	out, next := []msgstore.Message{}, cursor
	for _, x := range b.buf {
		if len(out) >= limit {
			break
		}
		next = x.seq
		if s.readableLocked(x.m.Chat) {
			out = append(out, x.m)
		}
	}
	p := s.bfProgressLocked()
	s.mu.Unlock()
	p["messages"], p["next_cursor"] = out, next
	writeJSON(w, 200, p)
}

// hBackfillStop: DELETE /history/backfill (ADMIN).
func (s *Server) hBackfillStop(w http.ResponseWriter, r *http.Request) {
	s.bfStop()
	s.mu.Lock()
	p := s.bfProgressLocked()
	s.mu.Unlock()
	writeJSON(w, 200, p)
}
