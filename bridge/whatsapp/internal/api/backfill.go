package api

// On-demand history backfill (JAR-272, PROTOCOL.md section 3a). While a backfill runs, the bridge asks the phone,
// one readable chat at a time, for BackfillBatch messages before the oldest message it knows, and repeats with the
// oldest answered message as the new anchor until an answer brings nothing older (no age limit unless `since`).
// Answered messages of readable chats go to a bounded in-memory buffer ONLY: never the store, never the sealed
// snapshot. Core drains it with GET /history/backfill?cursor=N; each read drops every buffered message up to N.
// Stop, kill, mode off, keep-nothing and unlink clear the buffer. Logs carry counts only.

import (
	"context"
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
	backfillSend   = 15 * time.Second
	// backfillRetries: a truncated answer that buffered nothing new is asked again this many times; then the chat
	// moves on and its overflow is reported as lost (progress "lost", "chats_incomplete"), never silently.
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
	retries   int                  // truncated answers of the current chat that buffered nothing new
	needDrain bool                 // a truncated answer waits for Core to free buffer space before it is asked again
	seen      map[string]bool      // IDs buffered from the current anchor: a retried answer adds only the missing ones
	accepted  int                  // new messages buffered from the answer that is out
	reqDrop   int                  // messages of the answer that is out that did not fit
	orphans   int                  // timed-out requests whose answer may still arrive (they carry no request id)
	dropped   int                  // messages that did not fit the buffer (their answer is asked again)
	lost      int                  // overflow given up after backfillRetries answers without progress
	partial   int                  // chats that moved on incomplete: lost messages, unanswered, or left the read scope
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
	s.bf.buf, s.bf.queue, s.bf.waiting, s.bf.bytes, s.bf.seen = nil, nil, false, 0, nil
	s.bf.gen++
	if s.bf.state == "running" || state != "stopped" {
		s.bf.state = state
	}
}

// bfPruneLocked applies a read-policy change at once: buffered messages of chats that left the read scope are
// wiped from memory and such chats are never asked again (savePolicyLocked calls it on every policy write).
func (s *Server) bfPruneLocked() {
	b := &s.bf
	keep := make([]bfMsg, 0, len(b.buf))
	for _, x := range b.buf {
		if s.readableLocked(x.m.Chat) {
			keep = append(keep, x)
		} else {
			b.bytes -= msgSize(x.m)
		}
	}
	if len(keep) < len(b.buf) {
		b.needDrain = false
	}
	wipe(b.buf)
	b.buf = keep
}

func (s *Server) bfStop() {
	s.bfGate.Lock() // waits for a dispatch in progress: after a stop returns no request goes out
	defer s.bfGate.Unlock()
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

// bfNext sends the request for the current chat when nothing is out and the buffer has room. Called without s.mu
// and without bfGate; the gate's read lock is held from the scope check through the send.
func (s *Server) bfNext() {
	s.bfGate.RLock()
	defer s.bfGate.RUnlock()
	s.mu.Lock()
	b := &s.bf
	if b.state != "running" || b.waiting || b.needDrain || len(b.buf)+BackfillBatch > s.bfBufMax() || b.bytes >= s.bfBytesMax() {
		s.mu.Unlock()
		return
	}
	for len(b.queue) > 0 && !s.readableLocked(b.queue[0].chat) { // left the read scope: never ask for it again
		b.queue, b.retries, b.seen = b.queue[1:], 0, nil
		b.done++
		b.partial++ // its history was not collected: reported incomplete, not as a normal finish
	}
	if len(b.queue) == 0 {
		b.state = "done"
		f := F{"state": "done", "chats": b.done, "received": b.received}
		s.mu.Unlock()
		s.log.Info("WA_BACKFILL", "History backfill finished.", f)
		return
	}
	c := b.queue[0]
	b.waiting, b.older, b.truncated, b.reqAnchor, b.accepted, b.reqDrop = true, false, false, c.anchor, 0, 0
	b.gen++
	gen := b.gen
	s.mu.Unlock()
	ctx, cancel := context.WithTimeout(s.ctx, backfillSend) // bounds how long the gate holds policy writes
	err := s.eng.RequestHistory(ctx, c.chat, c.anchor, BackfillBatch)
	cancel()
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
	s.cfg.AfterFunc(wait, func() { s.bfAnswered(gen, true) }) // the phone may never answer
}

// bfMessage buffers one on-demand history message (onEvent, without s.mu).
func (s *Server) bfMessage(m msgstore.Message) {
	s.mu.Lock()
	defer s.mu.Unlock()
	b := &s.bf
	// only the answer to the request that is out: its chat, older than its anchor, readable now; nothing unsolicited
	if b.state != "running" || !b.waiting || len(b.queue) == 0 || b.queue[0].chat != m.Chat ||
		m.TS > b.reqAnchor.TS || m.ID == b.reqAnchor.ID ||
		!s.readableLocked(m.Chat) || s.pol.KeepNothing || (b.since > 0 && m.TS < b.since) {
		return
	}
	if m.TS < b.queue[0].anchor.TS {
		b.queue[0].anchor = engine.HistoryAnchor{ID: m.ID, FromMe: m.FromMe, TS: m.TS}
		b.older = true
	}
	if b.seen[m.ID] { // buffered by an earlier, truncated answer from this anchor
		return
	}
	size := msgSize(m)
	if len(b.buf) >= s.bfBufMax() || b.bytes+size > s.bfBytesMax() { // hard bounds, whatever the phone sends
		b.truncated = true
		b.dropped++
		b.reqDrop++
		return
	}
	if b.seen == nil {
		b.seen = map[string]bool{}
	}
	b.seen[m.ID] = true
	b.accepted++
	b.seq++
	b.received++
	b.bytes += size
	b.buf = append(b.buf, bfMsg{seq: b.seq, m: m})
}

// bfAnswer handles the end of one on-demand answer.
func (s *Server) bfAnswer(chats []string) {
	s.mu.Lock()
	b := &s.bf
	gen, ok := b.gen, b.waiting && len(b.queue) > 0
	if ok && len(chats) > 0 {
		ok = false
		for _, c := range chats {
			ok = ok || c == b.queue[0].chat
		}
	}
	// An answer that brought nothing usable while an earlier request is still unanswered is attributed to that
	// earlier request (a late answer): it must not finish the request that is out. Worst case, the real answer
	// was this one and the request times out and is asked again; history is never silently given up.
	if ok && b.orphans > 0 && b.accepted == 0 && !b.older && !b.truncated {
		b.orphans--
		ok = false
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
	var lost F
	if timeout {
		b.orphans++ // its answer may still arrive later
	}
	if timeout || (b.truncated && b.accepted == 0) {
		b.retries++ // no progress: a message that never fits, or a phone that never answers, must not loop forever
	}
	switch {
	case (timeout || b.truncated) && b.retries <= backfillRetries:
		// unanswered, or part of the answer did not fit: ask again from the same anchor (after Core has drained
		// when truncated); `seen` skips what is already buffered, so the retry adds exactly the missing messages
		b.queue[0].anchor = b.reqAnchor
		b.needDrain = b.truncated && len(b.buf) > 0 // an empty buffer has nothing to drain: waiting would stall
	case timeout || b.truncated: // explicit, reported loss: the chat is incomplete, never silently "finished"
		b.lost += b.reqDrop
		b.partial++
		b.queue, b.retries, b.seen = b.queue[1:], 0, nil
		b.done++
		lost = F{"lost": b.reqDrop, "unanswered": timeout}
	case !b.older || (b.since > 0 && b.queue[0].anchor.TS < b.since):
		b.queue, b.retries, b.seen = b.queue[1:], 0, nil
		b.done++
	default: // the anchor moved older: the next window shares no message with this one
		b.retries, b.seen = 0, nil
	}
	s.mu.Unlock()
	if lost != nil {
		s.log.Warn("WA_BACKFILL", "History chat incomplete (unanswered or did not fit the buffer).", lost)
	}
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
		"received": b.received, "buffered": len(b.buf), "dropped": b.dropped, "lost": b.lost, "chats_incomplete": b.partial, "error": ec, "batch": BackfillBatch}
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
	// re-checked under the lock that commits the start: a kill, keep-nothing, mode off or concurrent start that
	// happened during the chat scan above wins
	code, status := "", 0
	switch {
	case s.killed:
		code, status = "WA_KILLED", 423
	case s.pol.KeepNothing:
		code, status = "WA_KEEP_NOTHING", 409
	case s.pol.Mode == "off":
		code, status = "WA_MODE_OFF", 409
	case s.bf.state == "running":
		code, status = "WA_BACKFILL_RUNNING", 409
	}
	if code != "" {
		s.mu.Unlock()
		writeErr(w, status, code, nil)
		return
	}
	nb.gen = s.bf.gen + 1
	wipe(s.bf.buf) // undrained text of an earlier backfill must not linger
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
		if x.seq > cursor {
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
	s.bfPruneLocked() // out of read scope: dropped from memory too
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
