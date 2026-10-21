package api

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/mdp/qrterminal/v3"

	"github.com/igalgerman/jarves/bridge/whatsapp/internal/engine"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/policy"
)

// MaxBody is the request body limit (section 2).
const MaxBody = 64 << 10

type role int

const (
	roleNone role = iota
	roleUser
	roleAdmin
)

type route struct {
	admin bool
	h     func(http.ResponseWriter, *http.Request)
}

// fixed English messages per code (never contain user data)
var messages = map[string]string{
	"WA_UNAUTHORIZED":      "Missing or wrong bearer token.",
	"WA_FORBIDDEN":         "This endpoint needs the admin token.",
	"WA_BAD_HOST":          "Host header refused.",
	"WA_BAD_REQUEST":       "The request is malformed.",
	"WA_NOT_FOUND":         "Unknown endpoint.",
	"WA_METHOD":            "Method not allowed on this endpoint.",
	"WA_MODE_OFF":          "WhatsApp access is off.",
	"WA_MODE_READ":         "The bridge is in read-only mode.",
	"WA_KILLED":            "The kill switch is on.",
	"WA_EMPTY":             "The message is empty.",
	"WA_TOO_LONG":          "The message is longer than 1500 characters.",
	"WA_GROUP_REFUSED":     "Sending to groups is not allowed.",
	"WA_BAD_RECIPIENT":     "The recipient is not a person.",
	"WA_NOT_ALLOWED":       "The recipient is not on the send allow-list.",
	"WA_NO_HISTORY":        "There is no prior chat with this recipient.",
	"WA_APPROVAL_MISSING":  "An approval is required.",
	"WA_APPROVAL_INVALID":  "The approval is invalid.",
	"WA_APPROVAL_EXPIRED":  "The approval has expired.",
	"WA_APPROVAL_MISMATCH": "The approval was given for another recipient or text.",
	"WA_APPROVAL_REUSED":   "The approval was already used.",
	"WA_QUIET_HOURS":       "Quiet hours: sending needs a night approval.",
	"WA_RATE_LIMITED":      "Sending rate limit reached.",
	"WA_NOT_CONNECTED":     "WhatsApp is not connected.",
	"WA_SEND_FAILED":       "WhatsApp did not accept the message.",
	"WA_WATCH_WINDOW":      "Send mode needs 14 days in read mode without warnings.",
	"WA_CHAT_NOT_FOUND":    "No readable chat with this id.",
	"WA_ALREADY_LINKED":    "A device is already linked.",
	"WA_STORE_LOCKED":      "The encrypted store cannot be opened by this Windows user.",
	"WA_NO_ENGINE":         "No WhatsApp engine is available.",
	"WA_LINK_FAILED":       "Linking could not start.",
	"WA_INTERNAL":          "Internal bridge error.",
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status int, code string, extra map[string]any) {
	e := map[string]any{"code": code, "message": messages[code]}
	for k, v := range extra {
		e[k] = v
	}
	writeJSON(w, status, map[string]any{"error": e})
}

// Handler returns the HTTP handler (Host check, auth, routing).
func (s *Server) Handler() http.Handler {
	routes := map[string]route{
		"GET /status":      {false, s.hStatus},
		"GET /chats":       {false, s.hChats},
		"GET /messages":    {false, s.hMessages},
		"GET /search":      {false, s.hSearch},
		"POST /seen":       {false, s.hSeen},
		"POST /send":       {false, s.hSend},
		"GET /events":      {false, s.handleEvents},
		"POST /mode":       {true, s.hMode},
		"POST /allow":      {true, s.hAllow},
		"POST /read_scope": {true, s.hReadScope},
		"POST /kill":       {true, s.hKill},
		"POST /link":       {true, s.hLinkStart},
		"GET /link":        {true, s.hLinkGet},
		"POST /unlink":     {true, s.hUnlink},
		"POST /retention":  {true, s.hRetention},
		"POST /purge":      {true, s.hPurge},
		"GET /audit":       {true, s.hAudit},
		"GET /policy":      {true, s.hPolicy},
		"POST /shutdown":   {true, s.hShutdown},
	}
	paths := map[string]bool{}
	for k := range routes {
		paths[strings.SplitN(k, " ", 2)[1]] = true
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		port := strconv.Itoa(s.port)
		s.mu.Unlock()
		if r.Host != "127.0.0.1:"+port && r.Host != "localhost:"+port {
			writeErr(w, http.StatusForbidden, "WA_BAD_HOST", nil)
			return
		}
		ro := s.auth(r)
		if ro == roleNone {
			writeErr(w, http.StatusUnauthorized, "WA_UNAUTHORIZED", nil)
			return
		}
		rt, ok := routes[r.Method+" "+r.URL.Path]
		if !ok {
			if paths[r.URL.Path] {
				writeErr(w, http.StatusMethodNotAllowed, "WA_METHOD", nil)
			} else {
				writeErr(w, http.StatusNotFound, "WA_NOT_FOUND", nil)
			}
			return
		}
		if rt.admin && ro != roleAdmin {
			writeErr(w, http.StatusForbidden, "WA_FORBIDDEN", nil)
			return
		}
		rt.h(w, r)
	})
}

// auth compares the bearer token with both tokens in constant time (admin is a superset of user).
func (s *Server) auth(r *http.Request) role {
	h := r.Header.Get("Authorization")
	if !strings.HasPrefix(h, "Bearer ") {
		return roleNone
	}
	tok := []byte(strings.TrimPrefix(h, "Bearer "))
	isUser := subtle.ConstantTimeCompare(tok, []byte(s.userTok)) == 1
	isAdmin := subtle.ConstantTimeCompare(tok, []byte(s.adminTok)) == 1
	switch {
	case isAdmin:
		return roleAdmin
	case isUser:
		return roleUser
	}
	return roleNone
}

// readBody decodes a JSON object body (<= 64 KiB). Returns false after writing the error.
func readBody(w http.ResponseWriter, r *http.Request, v any) bool {
	b, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBody))
	if err != nil {
		var mbe *http.MaxBytesError
		if errors.As(err, &mbe) {
			writeErr(w, http.StatusRequestEntityTooLarge, "WA_BAD_REQUEST", nil)
		} else {
			writeErr(w, http.StatusBadRequest, "WA_BAD_REQUEST", nil)
		}
		return false
	}
	if len(bytes.TrimSpace(b)) == 0 {
		b = []byte("{}")
	}
	if !utf8.Valid(b) || json.Unmarshal(b, v) != nil {
		writeErr(w, http.StatusBadRequest, "WA_BAD_REQUEST", nil)
		return false
	}
	return true
}

func queryFloat(r *http.Request, k string, def float64) (float64, bool) {
	v := r.URL.Query().Get(k)
	if v == "" {
		return def, true
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
		return 0, false
	}
	return f, true
}

func queryLimit(r *http.Request, def, max int) (int, bool) {
	v := r.URL.Query().Get("limit")
	if v == "" {
		return def, true
	}
	n, err := strconv.Atoi(v)
	if err != nil || n < 1 {
		return 0, false
	}
	if n > max {
		n = max
	}
	return n, true
}

// readGuard refuses reads in mode off.
func (s *Server) readGuard(w http.ResponseWriter) bool {
	s.mu.Lock()
	off := s.pol.Mode == "off"
	s.mu.Unlock()
	if off {
		writeErr(w, http.StatusConflict, "WA_MODE_OFF", nil)
		return false
	}
	return true
}

func (s *Server) hStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, s.status(r.Context()))
}

func (s *Server) hChats(w http.ResponseWriter, r *http.Request) {
	if !s.readGuard(w) {
		return
	}
	since, ok1 := queryFloat(r, "since", 0)
	limit, ok2 := queryLimit(r, 50, 500)
	if !ok1 || !ok2 {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	all, err := s.st.AllChats(r.Context())
	if err != nil {
		writeErr(w, 500, "WA_INTERNAL", nil)
		return
	}
	out := []map[string]any{}
	for _, c := range all {
		if len(out) >= limit {
			break
		}
		if c.LastTS > since && s.readable(c.ID) {
			out = append(out, s.chatJSON(c))
		}
	}
	writeJSON(w, 200, map[string]any{"chats": out})
}

func (s *Server) hMessages(w http.ResponseWriter, r *http.Request) {
	if !s.readGuard(w) {
		return
	}
	chat, okc := normalizeChat(r.URL.Query().Get("chat"))
	since, ok1 := queryFloat(r, "since", 0)
	limit, ok2 := queryLimit(r, 50, 500)
	if !okc || !ok1 || !ok2 {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	c, found, err := s.st.GetChat(r.Context(), chat)
	if err != nil {
		writeErr(w, 500, "WA_INTERNAL", nil)
		return
	}
	if !found || !s.readable(chat) {
		writeErr(w, 404, "WA_CHAT_NOT_FOUND", nil)
		return
	}
	s.mu.Lock()
	minTS := s.retentionMinTSLocked()
	s.mu.Unlock()
	msgs, err := s.st.Messages(r.Context(), chat, since, minTS, limit)
	if err != nil {
		writeErr(w, 500, "WA_INTERNAL", nil)
		return
	}
	writeJSON(w, 200, map[string]any{"chat": s.chatJSON(c), "messages": msgs})
}

func (s *Server) hSearch(w http.ResponseWriter, r *http.Request) {
	if !s.readGuard(w) {
		return
	}
	q := strings.TrimSpace(r.URL.Query().Get("q"))
	limit, ok := queryLimit(r, 20, 200)
	if q == "" || !ok || utf8.RuneCountInString(q) > 200 {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	s.mu.Lock()
	minTS := s.retentionMinTSLocked()
	s.mu.Unlock()
	msgs, err := s.st.Search(r.Context(), q, s.readable, minTS, limit)
	if err != nil {
		writeErr(w, 500, "WA_INTERNAL", nil)
		return
	}
	writeJSON(w, 200, map[string]any{"messages": msgs})
}

func (s *Server) hSeen(w http.ResponseWriter, r *http.Request) {
	var b struct {
		Chat string `json:"chat"`
	}
	if !readBody(w, r, &b) || !s.readGuard(w) {
		return
	}
	chat, ok := normalizeChat(b.Chat)
	if !ok {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	if _, found, _ := s.st.GetChat(r.Context(), chat); !found || !s.readable(chat) {
		writeErr(w, 404, "WA_CHAT_NOT_FOUND", nil)
		return
	}
	// local counter only: NEVER a read receipt to WhatsApp
	if err := s.st.Seen(r.Context(), chat); err != nil {
		writeErr(w, 500, "WA_INTERNAL", nil)
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (s *Server) hSend(w http.ResponseWriter, r *http.Request) {
	var req policy.Request
	if !readBody(w, r, &req) {
		return
	}
	s.sendMu.Lock()
	defer s.sendMu.Unlock()
	now := s.now()
	nowTS := ts(now)
	s.mu.Lock()
	allow := map[string]bool{}
	for _, j := range s.pol.SendAllow {
		allow[j] = true
	}
	used := make(map[string]float64, len(s.used))
	for n, e := range s.used {
		used[n] = e
	}
	st := policy.State{
		Killed: s.killed, Mode: policy.Mode(s.pol.Mode), LocalHour: s.cfg.LocalHour(now), SendAllow: allow,
		HasHistory: func(j string) bool { return s.st.HasHistory(r.Context(), j) },
		Sent:       append([]policy.Sent(nil), s.sent...),
		UsedNonce: func(n string) bool {
			exp, ok := used[n]
			return ok && exp >= nowTS
		},
		Connected:    s.eng != nil && s.eng.IsConnected(),
		RecipientKey: ChatHash,
	}
	s.mu.Unlock()
	d := policy.Check(nowTS, s.key, st, req)
	line := AuditLine{TS: nowTS, ChatHash: ChatHash(req.To), TextSHA256: sha256Hex(req.Text),
		Chars: utf8.RuneCountInString(req.Text)}
	if !d.OK {
		line.Result = d.Code
		_ = appendAudit(s.path(FileAudit), line)
		s.log.Info("WA_SEND_REFUSED", "Send refused by policy.", F{"code": d.Code})
		var extra map[string]any
		if d.Code == "WA_RATE_LIMITED" {
			extra = map[string]any{"limit": d.Limit, "retry_after_s": d.RetryAfterS}
		}
		writeErr(w, d.HTTP, d.Code, extra)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	id, sentTS, err := s.eng.SendText(ctx, req.To, req.Text)
	// the nonce is consumed by the attempt (a send that failed late may still have reached WhatsApp)
	s.mu.Lock()
	s.used[d.Approval.N] = d.Approval.Exp
	_ = saveNonces(s.path(FileNonces), s.used)
	if err == nil {
		s.sent = append(s.sent, policy.Sent{TS: nowTS, To: ChatHash(req.To)})
		s.ownSent[id] = true
	}
	s.mu.Unlock()
	if err != nil {
		line.Result = "WA_SEND_FAILED"
		_ = appendAudit(s.path(FileAudit), line)
		s.log.Warn("WA_SEND_FAILED", "Engine send failed.", nil)
		if errors.Is(err, engine.ErrNotConnected) {
			writeErr(w, 503, "WA_NOT_CONNECTED", nil)
		} else {
			writeErr(w, 502, "WA_SEND_FAILED", nil)
		}
		return
	}
	line.Result = "sent"
	_ = appendAudit(s.path(FileAudit), line)
	if sentTS == 0 {
		sentTS = nowTS
	}
	s.onEvent(engine.MessageEvent{Msg: msgMine(req.To, id, sentTS, req.Text)})
	s.log.Info("WA_SENT", "Message sent.", F{"chars": line.Chars})
	writeJSON(w, 200, map[string]any{"ok": true, "id": id, "ts": sentTS})
}

func (s *Server) hMode(w http.ResponseWriter, r *http.Request) {
	var b struct {
		Mode          string `json:"mode"`
		OverrideWatch bool   `json:"override_watch"`
	}
	if !readBody(w, r, &b) {
		return
	}
	if !policy.ValidMode(b.Mode) {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	now := s.nowTS()
	s.mu.Lock()
	prev := s.pol.Mode
	if b.Mode == "send" && prev != "send" && !b.OverrideWatch {
		if left := s.watchDaysLeftLocked(); left > 0 {
			s.mu.Unlock()
			writeErr(w, 409, "WA_WATCH_WINDOW", map[string]any{"days_left": left})
			return
		}
	}
	s.pol.Mode = b.Mode
	switch {
	case b.Mode == "off":
		s.pol.ReadSince = nil
	case b.Mode == "read" && (prev == "off" || s.pol.ReadSince == nil):
		s.pol.ReadSince = &now
		s.pol.WarningsSinceRead = 0
	}
	s.savePolicyLocked()
	s.mu.Unlock()
	if prev != b.Mode {
		s.log.Info("WA_MODE", "Mode changed.", F{"mode": b.Mode, "override_watch": b.OverrideWatch})
		s.hub.broadcast("", map[string]any{"type": "mode.changed", "mode": b.Mode})
		s.requestReconcile()
	}
	writeJSON(w, 200, map[string]any{"ok": true, "mode": b.Mode})
}

func (s *Server) hAllow(w http.ResponseWriter, r *http.Request) {
	var b struct {
		List string `json:"list"`
		Op   string `json:"op"`
		Chat string `json:"chat"`
	}
	if !readBody(w, r, &b) {
		return
	}
	chat, ok := normalizeChat(b.Chat)
	if !ok || (b.List != "read" && b.List != "send") || (b.Op != "add" && b.Op != "remove") {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	if b.List == "send" && b.Op == "add" && policy.IsGroup(chat) {
		writeErr(w, 403, "WA_GROUP_REFUSED", nil)
		return
	}
	s.mu.Lock()
	l := &s.pol.ReadAllow
	if b.List == "send" {
		l = &s.pol.SendAllow
	}
	if b.Op == "add" {
		*l = sortedUnique(append(*l, chat))
	} else {
		out := []string{}
		for _, x := range *l {
			if x != chat {
				out = append(out, x)
			}
		}
		*l = out
	}
	n := len(*l)
	s.savePolicyLocked()
	s.mu.Unlock()
	s.log.Info("WA_ALLOW", "Allow-list changed.", F{"list": b.List, "op": b.Op, "count": n})
	writeJSON(w, 200, map[string]any{"ok": true, "list": b.List, "count": n})
}

func (s *Server) hReadScope(w http.ResponseWriter, r *http.Request) {
	var b struct {
		AllDirect *bool `json:"all_direct"`
		Groups    *bool `json:"groups"`
	}
	if !readBody(w, r, &b) {
		return
	}
	if b.AllDirect == nil && b.Groups == nil {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	s.mu.Lock()
	if b.AllDirect != nil {
		s.pol.ReadScope.AllDirect = *b.AllDirect
	}
	if b.Groups != nil {
		s.pol.ReadScope.Groups = *b.Groups
	}
	sc := s.pol.ReadScope
	s.savePolicyLocked()
	s.mu.Unlock()
	writeJSON(w, 200, map[string]any{"ok": true, "read_scope": sc})
}

func (s *Server) hKill(w http.ResponseWriter, r *http.Request) {
	var b struct {
		On     *bool `json:"on"`
		Logout bool  `json:"logout"`
	}
	if !readBody(w, r, &b) {
		return
	}
	if b.On == nil {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	if *b.On {
		s.selfKill("admin")
		if b.Logout {
			s.unlink(r.Context())
		}
	} else {
		if err := os.Remove(s.path(FileKill)); err != nil && !os.IsNotExist(err) {
			writeErr(w, 500, "WA_INTERNAL", nil)
			return
		}
		s.applyKill(false, "")
	}
	s.mu.Lock()
	k := s.killed
	s.mu.Unlock()
	writeJSON(w, 200, map[string]any{"ok": true, "killed": k})
}

func (s *Server) hLinkStart(w http.ResponseWriter, r *http.Request) {
	var b struct {
		Phone string `json:"phone"`
	}
	if !readBody(w, r, &b) {
		return
	}
	if b.Phone != "" {
		d := strings.TrimPrefix(b.Phone, "+")
		if !strings.HasPrefix(b.Phone, "+") || len(d) < 6 || len(d) > 15 || strings.Trim(d, "0123456789") != "" {
			writeErr(w, 400, "WA_BAD_REQUEST", nil)
			return
		}
	}
	s.mu.Lock()
	killed := s.killed
	s.mu.Unlock()
	switch {
	case s.cfg.StoreLocked:
		writeErr(w, 409, "WA_STORE_LOCKED", nil)
		return
	case s.eng == nil:
		writeErr(w, 503, "WA_NO_ENGINE", nil)
		return
	case killed:
		writeErr(w, 423, "WA_KILLED", nil)
		return
	case s.eng.IsLoggedIn():
		writeErr(w, 409, "WA_ALREADY_LINKED", nil)
		return
	}
	s.mu.Lock()
	s.link = linkState{State: engine.LinkWaiting}
	s.mu.Unlock()
	ctx := s.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	if err := s.eng.StartLink(ctx, b.Phone); err != nil {
		s.mu.Lock()
		s.link = linkState{State: engine.LinkError}
		s.mu.Unlock()
		if errors.Is(err, engine.ErrAlreadyLinked) {
			writeErr(w, 409, "WA_ALREADY_LINKED", nil)
		} else {
			writeErr(w, 502, "WA_LINK_FAILED", nil)
		}
		return
	}
	s.hLinkGet(w, r)
}

func (s *Server) hLinkGet(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	l := s.link
	s.mu.Unlock()
	writeJSON(w, 200, l)
}

// unlink logs the device out on WhatsApp's side (if possible), wipes chats/messages and seals at once.
func (s *Server) unlink(ctx context.Context) {
	if s.eng != nil {
		if err := s.eng.Logout(ctx); err != nil {
			s.log.Warn("WA_LOGOUT_FAILED", "Server-side logout failed; local keys were deleted.", nil)
		}
	}
	_ = s.st.Wipe(ctx)
	s.mu.Lock()
	s.link = linkState{State: engine.LinkIdle}
	s.mu.Unlock()
	s.seal(true)
	s.log.Warn("WA_UNLINKED", "Device unlinked and store wiped.", nil)
}

func (s *Server) hUnlink(w http.ResponseWriter, r *http.Request) {
	s.unlink(r.Context())
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (s *Server) hRetention(w http.ResponseWriter, r *http.Request) {
	var b struct {
		Days        *int `json:"days"`
		KeepNothing bool `json:"keep_nothing"`
	}
	if !readBody(w, r, &b) {
		return
	}
	if b.Days == nil || *b.Days < 1 || *b.Days > 90 {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	s.mu.Lock()
	s.pol.RetentionDays, s.pol.KeepNothing = *b.Days, b.KeepNothing
	s.savePolicyLocked()
	s.mu.Unlock()
	s.purgeRetention()
	writeJSON(w, 200, map[string]any{"ok": true, "retention_days": *b.Days, "keep_nothing": b.KeepNothing})
}

func (s *Server) hPurge(w http.ResponseWriter, r *http.Request) {
	var b struct {
		OlderThanDays *float64 `json:"older_than_days"`
	}
	if !readBody(w, r, &b) {
		return
	}
	var before *float64
	if b.OlderThanDays != nil {
		if *b.OlderThanDays < 0 || *b.OlderThanDays != math.Trunc(*b.OlderThanDays) {
			writeErr(w, 400, "WA_BAD_REQUEST", nil)
			return
		}
		t := s.nowTS() - *b.OlderThanDays*86400
		before = &t
	}
	n, err := s.st.Purge(r.Context(), before)
	if err != nil {
		writeErr(w, 500, "WA_INTERNAL", nil)
		return
	}
	s.log.Info("WA_PURGE", "Messages purged.", F{"deleted": n})
	writeJSON(w, 200, map[string]any{"deleted": n})
}

func (s *Server) hAudit(w http.ResponseWriter, r *http.Request) {
	limit, ok := queryLimit(r, 100, 1000)
	if !ok {
		writeErr(w, 400, "WA_BAD_REQUEST", nil)
		return
	}
	lines := readAudit(s.path(FileAudit))
	if len(lines) > limit {
		lines = lines[len(lines)-limit:]
	}
	if lines == nil {
		lines = []AuditLine{}
	}
	writeJSON(w, 200, map[string]any{"lines": lines})
}

func (s *Server) hPolicy(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	p := s.pol
	s.mu.Unlock()
	writeJSON(w, 200, p)
}

func (s *Server) hShutdown(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"ok": true})
	if f, ok := w.(http.Flusher); ok {
		f.Flush()
	}
	s.log.Info("WA_SHUTDOWN", "Shutdown requested.", nil)
	if s.cancel != nil {
		go s.cancel()
	}
}

func renderQR(code string) string {
	var buf bytes.Buffer
	qrterminal.GenerateHalfBlock(code, qrterminal.L, &buf)
	return buf.String()
}
