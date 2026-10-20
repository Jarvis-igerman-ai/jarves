// Package api is the BridgeProtocol v1 HTTP/WebSocket server (PROTOCOL.md sections 2-5, 7) and the bridge's state
// machine: tokens, policy.json, kill switch, watch window, retention, audit, rate limits and single-use approvals.
package api

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/igalgerman/jarves/bridge/whatsapp/internal/engine"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/msgstore"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/policy"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/securestore"
)

// Config wires a Server.
type Config struct {
	Dir           string // <state_dir>/whatsapp_bridge
	Version       string
	Engine        engine.Engine
	Store         *msgstore.Store
	Changes       func(context.Context) (int64, error) // dirty counter of the database (nil = always dirty)
	Seal          func(context.Context) error          // seals the database to store.enc (nil = never, e.g. locked)
	StoreLocked   bool
	KeyProtection string
	Log           *Logger
	Now           func() time.Time
	LocalHour     func(time.Time) int
	KillPoll      time.Duration // default 250 ms
	SealEvery     time.Duration // default 5 s
	RetentionTick time.Duration // default 1 h
}

// Server is the bridge.
type Server struct {
	cfg       Config
	eng       engine.Engine
	st        *msgstore.Store
	log       *Logger
	userTok   string
	adminTok  string
	key       []byte
	startedAt float64

	mu         sync.Mutex // guards everything below
	pol        Policy
	killed     bool
	killReason string
	used       map[string]float64 // nonce -> exp
	sent       []policy.Sent      // really sent, To = chat hash
	ownSent    map[string]bool    // ids sent by the bridge (not the phone)
	link       linkState
	port       int
	connecting bool

	sendMu sync.Mutex // serializes /send so rate and nonce checks are atomic

	hub        *hub
	lastSealed int64
	sealMu     sync.Mutex
	ctx        context.Context
	cancel     context.CancelFunc
	reconcileC chan struct{}
	done       chan struct{}
	httpSrv    *http.Server
}

type linkState struct {
	State       string   `json:"state"`
	QR          string   `json:"qr"`
	QRText      string   `json:"qr_text"`
	PairingCode string   `json:"pairing_code"`
	ExpiresAt   *float64 `json:"expires_at"`
}

func ts(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

func (s *Server) now() time.Time { return s.cfg.Now() }
func (s *Server) nowTS() float64 { return ts(s.cfg.Now()) }
func (s *Server) path(name string) string {
	return filepath.Join(s.cfg.Dir, name)
}

// New loads or creates the state files and returns a stopped server.
func New(cfg Config) (*Server, error) {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.LocalHour == nil {
		cfg.LocalHour = func(t time.Time) int { return t.Local().Hour() }
	}
	if cfg.KillPoll <= 0 {
		cfg.KillPoll = 250 * time.Millisecond
	}
	if cfg.SealEvery <= 0 {
		cfg.SealEvery = 5 * time.Second
	}
	if cfg.RetentionTick <= 0 {
		cfg.RetentionTick = time.Hour
	}
	if cfg.KeyProtection == "" {
		cfg.KeyProtection = securestore.Protection()
	}
	if cfg.Log == nil {
		cfg.Log = NewLogger(nil)
	}
	if err := securestore.RestrictDir(cfg.Dir); err != nil {
		return nil, fmt.Errorf("state dir: %w", err)
	}
	s := &Server{cfg: cfg, eng: cfg.Engine, st: cfg.Store, log: cfg.Log, ownSent: map[string]bool{},
		reconcileC: make(chan struct{}, 1), done: make(chan struct{}), link: linkState{State: engine.LinkIdle}}
	s.hub = newHub()
	var created bool
	var err error
	if s.userTok, created, err = loadOrCreate(s.path(FileToken), reUserToken, newToken("wab_")); err != nil {
		return nil, err
	} else if created {
		s.log.Info("WA_TOKEN_CREATED", "User token created.", nil)
	}
	if s.adminTok, created, err = loadOrCreate(s.path(FileAdmin), reAdminToken, newToken("waa_")); err != nil {
		return nil, err
	} else if created {
		s.log.Info("WA_ADMIN_TOKEN_CREATED", "Admin token created.", nil)
	}
	keyHex, _, err := loadOrCreate(s.path(FileApproval), reKeyHex, func() string { return hex.EncodeToString(random32()) })
	if err != nil {
		return nil, err
	}
	s.key, _ = hex.DecodeString(keyHex)
	if s.pol, err = loadPolicy(s.path(FilePolicy)); err != nil {
		s.log.Warn("WA_POLICY_RESET", "policy.json was unreadable; defaults loaded.", nil)
	}
	now := s.nowTS()
	s.startedAt = now
	s.used = loadNonces(s.path(FileNonces), now)
	for _, l := range readAudit(s.path(FileAudit)) {
		if l.Result == "sent" && now-l.TS < policy.WindowDay {
			s.sent = append(s.sent, policy.Sent{TS: l.TS, To: l.ChatHash})
		}
	}
	s.killed, s.killReason = readKill(s.path(FileKill))
	if s.eng != nil {
		s.eng.SetHandler(s.onEvent)
	}
	return s, nil
}

// SetPort tells the server its listening port (Host-header check) when the caller listens itself (tests).
func (s *Server) SetPort(p int) { s.mu.Lock(); s.port = p; s.mu.Unlock() }

// Tokens returns (user, admin) for tests and for main to never print.
func (s *Server) Tokens() (string, string) { return s.userTok, s.adminTok }

// ApprovalKey returns the HMAC key (tests).
func (s *Server) ApprovalKey() []byte { return s.key }

// Start begins the background loops (kill polling, sealing, retention, engine reconcile). Run Stop to end them.
func (s *Server) Start(ctx context.Context) {
	s.ctx, s.cancel = context.WithCancel(ctx)
	s.purgeRetention()
	go s.loops()
	s.requestReconcile()
}

// ListenAndServe listens on 127.0.0.1:port (0 = random), writes endpoint.json and serves until ctx ends or
// POST /shutdown. It seals the store and removes endpoint.json before returning.
func (s *Server) ListenAndServe(ctx context.Context, port int) error {
	ln, err := net.Listen("tcp4", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		return err
	}
	s.SetPort(ln.Addr().(*net.TCPAddr).Port)
	s.Start(ctx)
	s.httpSrv = &http.Server{Handler: s.Handler(), ReadHeaderTimeout: 10 * time.Second, MaxHeaderBytes: 16 << 10}
	if err := s.writeEndpoint(); err != nil {
		_ = ln.Close()
		s.Stop()
		return err
	}
	s.log.Info("WA_LISTENING", "Bridge listening on loopback.", F{"engine": s.engineName()})
	errC := make(chan error, 1)
	go func() { errC <- s.httpSrv.Serve(ln) }()
	select {
	case <-s.ctx.Done():
	case err = <-errC:
	}
	shCtx, c := context.WithTimeout(context.Background(), 3*time.Second)
	_ = s.httpSrv.Shutdown(shCtx)
	c()
	s.Stop()
	if errors.Is(err, http.ErrServerClosed) {
		err = nil
	}
	return err
}

func (s *Server) engineName() string {
	if s.eng == nil {
		return "none"
	}
	return s.eng.Name()
}

func (s *Server) writeEndpoint() error {
	s.mu.Lock()
	port := s.port
	s.mu.Unlock()
	b, _ := json.Marshal(map[string]any{"v": ProtocolV, "port": port, "pid": os.Getpid(), "engine": s.engineName(),
		"version": s.cfg.Version, "started_at": s.startedAt})
	return securestore.WriteFileAtomic(s.path(FileEndpoint), b)
}

// Stop ends the loops, disconnects the engine, seals the store and removes endpoint.json (if it is ours).
func (s *Server) Stop() {
	if s.cancel == nil {
		return
	}
	s.cancel()
	<-s.done
	if s.eng != nil {
		s.eng.Disconnect()
	}
	s.seal(true)
	s.hub.closeAll()
	if b, err := os.ReadFile(s.path(FileEndpoint)); err == nil {
		var e struct {
			PID int `json:"pid"`
		}
		if json.Unmarshal(b, &e) == nil && e.PID == os.Getpid() {
			_ = os.Remove(s.path(FileEndpoint))
		}
	}
	s.log.Info("WA_STOPPED", "Bridge stopped.", nil)
}

func (s *Server) loops() {
	defer close(s.done)
	kill := time.NewTicker(s.cfg.KillPoll)
	seal := time.NewTicker(s.cfg.SealEvery)
	ret := time.NewTicker(s.cfg.RetentionTick)
	defer kill.Stop()
	defer seal.Stop()
	defer ret.Stop()
	for {
		select {
		case <-s.ctx.Done():
			return
		case <-kill.C:
			s.pollKill()
		case <-seal.C:
			s.seal(false)
			s.pruneNonces()
		case <-ret.C:
			s.purgeRetention()
		case <-s.reconcileC:
			s.reconcile()
		}
	}
}

func (s *Server) requestReconcile() {
	select {
	case s.reconcileC <- struct{}{}:
	default:
	}
}

// reconcile connects the engine when it should run (linked, not killed, mode read/send, store usable) and
// disconnects it otherwise. Linking in progress is left alone.
func (s *Server) reconcile() {
	if s.eng == nil {
		return
	}
	s.mu.Lock()
	want := !s.cfg.StoreLocked && !s.killed && s.pol.Mode != "off"
	linking := s.link.State == engine.LinkWaiting
	s.mu.Unlock()
	if linking {
		return
	}
	if want && s.eng.IsLoggedIn() && !s.eng.IsConnected() {
		s.mu.Lock()
		busy := s.connecting
		s.connecting = true
		s.mu.Unlock()
		if busy {
			return
		}
		go func() { // never block the kill poll on a network handshake
			if err := s.eng.Connect(s.ctx); err != nil {
				s.log.Warn("WA_CONNECT_FAILED", "Engine connect failed.", nil)
			}
			s.mu.Lock()
			s.connecting = false
			stillWanted := !s.killed && s.pol.Mode != "off"
			s.mu.Unlock()
			if !stillWanted { // killed while connecting
				s.eng.Disconnect()
			}
		}()
	} else if !want && s.eng.IsConnected() {
		s.eng.Disconnect()
	}
}

func (s *Server) seal(force bool) {
	if s.cfg.Seal == nil || s.cfg.StoreLocked {
		return
	}
	s.sealMu.Lock()
	defer s.sealMu.Unlock()
	ctx := context.Background()
	var n int64 = -1
	if s.cfg.Changes != nil {
		if c, err := s.cfg.Changes(ctx); err == nil {
			n = c
		}
	}
	if !force && n >= 0 && n == s.lastSealed {
		return
	}
	if err := s.cfg.Seal(ctx); err != nil {
		s.log.Error("WA_SEAL_FAILED", "Sealing the store failed.", nil)
		return
	}
	s.lastSealed = n
}

func (s *Server) pruneNonces() {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.nowTS()
	changed := false
	for n, exp := range s.used {
		if exp < now {
			delete(s.used, n)
			changed = true
		}
	}
	if changed {
		_ = saveNonces(s.path(FileNonces), s.used)
	}
	cut := 0
	for cut < len(s.sent) && now-s.sent[cut].TS >= policy.WindowDay {
		cut++
	}
	s.sent = s.sent[cut:]
}

// pollKill applies the KILL file state (present = killed).
func (s *Server) pollKill() {
	on, reason := readKill(s.path(FileKill))
	s.applyKill(on, reason)
}

func (s *Server) applyKill(on bool, reason string) {
	s.mu.Lock()
	changed := on != s.killed || (on && reason != s.killReason)
	was := s.killed
	s.killed = on
	if on {
		s.killReason = reason
	} else {
		s.killReason = ""
	}
	s.mu.Unlock()
	if !changed {
		return
	}
	if on && s.eng != nil {
		s.eng.Disconnect() // within 2 s of the KILL file appearing (poll <= 250 ms)
	}
	if on != was {
		var r any = reason
		if !on {
			r = nil
		}
		s.log.Warn("WA_KILL", "Kill switch changed.", F{"on": on})
		s.hub.broadcast("", map[string]any{"type": "killed", "on": on, "reason": r})
		s.requestReconcile()
	}
}

// selfKill creates the KILL file with a reason and applies it at once.
func (s *Server) selfKill(reason string) {
	_ = writeKill(s.cfg.Dir, reason, s.nowTS())
	s.applyKill(true, reason)
}

func (s *Server) savePolicyLocked() {
	if err := savePolicy(s.path(FilePolicy), s.pol); err != nil {
		s.log.Error("WA_POLICY_WRITE_FAILED", "Writing policy.json failed.", nil)
	}
}

// readable is the read gate (section 3).
func (s *Server) readableLocked(chat string) bool {
	if policy.IsGroup(chat) {
		return s.pol.ReadScope.Groups && contains(s.pol.ReadAllow, chat)
	}
	if !policy.IsDirect(chat) {
		return false
	}
	return s.pol.ReadScope.AllDirect || contains(s.pol.ReadAllow, chat)
}

func (s *Server) readable(chat string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.readableLocked(chat)
}

func contains(l []string, v string) bool {
	for _, x := range l {
		if x == v {
			return true
		}
	}
	return false
}

func (s *Server) retentionMinTSLocked() float64 {
	return s.nowTS() - float64(s.pol.RetentionDays)*86400
}

func (s *Server) purgeRetention() {
	s.mu.Lock()
	keepNothing := s.pol.KeepNothing
	before := s.retentionMinTSLocked()
	s.mu.Unlock()
	var n int64
	var err error
	if keepNothing {
		n, err = s.st.Purge(context.Background(), nil)
	} else {
		n, err = s.st.Purge(context.Background(), &before)
	}
	if err != nil {
		s.log.Error("WA_PURGE_FAILED", "Retention purge failed.", nil)
	} else if n > 0 {
		s.log.Info("WA_RETENTION_PURGE", "Retention purge deleted messages.", F{"deleted": n})
	}
}

// onEvent handles every engine event.
func (s *Server) onEvent(e engine.Event) {
	ctx := context.Background()
	switch ev := e.(type) {
	case engine.MessageEvent:
		s.mu.Lock()
		readable := s.readableLocked(ev.Msg.Chat)
		keep := readable && !s.pol.KeepNothing && ev.Msg.TS >= s.retentionMinTSLocked()
		own := s.ownSent[ev.Msg.ID]
		s.mu.Unlock()
		if ev.Chat.ID == "" {
			ev.Chat.ID = ev.Msg.Chat
		}
		ev.Chat.IsGroup = policy.IsGroup(ev.Chat.ID)
		if err := s.st.UpsertChat(ctx, ev.Chat); err != nil {
			s.log.Error("WA_STORE_FAILED", "Storing a chat failed.", nil)
			return
		}
		if err := s.st.AddMessage(ctx, ev.Msg, keep, !ev.History); err != nil {
			s.log.Error("WA_STORE_FAILED", "Storing a message failed.", nil)
			return
		}
		if ev.Msg.FromMe && !own {
			if last, ok := s.st.GetMeta(ctx, "phone_last_seen"); !ok || ev.Msg.TS > last {
				_ = s.st.SetMeta(ctx, "phone_last_seen", ev.Msg.TS)
			}
		}
		if !ev.History && readable {
			s.hub.broadcast(ev.Msg.Chat, map[string]any{"type": "message.new", "chat": ev.Msg.Chat, "id": ev.Msg.ID,
				"ts": ev.Msg.TS, "from_me": ev.Msg.FromMe, "kind": ev.Msg.Kind})
		}
	case engine.EditEvent:
		_ = s.st.EditMessage(ctx, ev.Chat, ev.ID, ev.Text)
	case engine.RevokeEvent:
		_ = s.st.DeleteMessage(ctx, ev.Chat, ev.ID)
	case engine.ContactEvent:
		_ = s.st.RenameChat(ctx, ev.Chat)
	case engine.ReceiptEvent:
		if s.readable(ev.Chat) {
			s.hub.broadcast(ev.Chat, map[string]any{"type": "receipt", "chat": ev.Chat, "id": ev.ID, "state": ev.State})
		}
	case engine.ConnectionEvent:
		s.log.Info("WA_CONNECTION", "Connection state changed.", F{"state": ev.State})
		s.hub.broadcast("", map[string]any{"type": "connection.state", "state": ev.State})
	case engine.AccountWarningEvent:
		now := s.nowTS()
		s.mu.Lock()
		s.pol.WarningsTotal++
		s.pol.WarningsSinceRead++
		s.pol.AccountWarning = &Warning{Code: ev.Code, At: now}
		if s.pol.Mode != "off" {
			s.pol.ReadSince = &now
		} else {
			s.pol.ReadSince = nil
		}
		s.savePolicyLocked()
		s.mu.Unlock()
		s.log.Warn("WA_ACCOUNT_WARNING", "WhatsApp account warning; the bridge killed itself.", F{"code": ev.Code})
		s.hub.broadcast("", map[string]any{"type": "account.warning", "code": ev.Code})
		s.selfKill("account_warning:" + ev.Code)
	case engine.LoggedOutEvent:
		s.log.Warn("WA_LOGGED_OUT", "The linked device was logged out; the bridge killed itself.", F{"reason": ev.Reason})
		s.hub.broadcast("", map[string]any{"type": "logged_out", "reason": ev.Reason})
		s.selfKill("logged_out")
	case engine.LinkEvent:
		s.mu.Lock()
		s.link = linkState{State: ev.State, QR: ev.QR, PairingCode: ev.PairingCode}
		if ev.QR != "" {
			s.link.QRText = renderQR(ev.QR)
		}
		if ev.ExpiresAt > 0 {
			e := ev.ExpiresAt
			s.link.ExpiresAt = &e
		}
		s.mu.Unlock()
		s.log.Info("WA_LINK", "Link state changed.", F{"state": ev.State})
		if ev.State != engine.LinkWaiting {
			s.requestReconcile()
		}
	}
}

// watchDaysLeft = whole days until POST /mode send is allowed without override (0 = allowed).
func (s *Server) watchDaysLeftLocked() int {
	if s.pol.ReadSince == nil || s.pol.WarningsSinceRead > 0 {
		return WatchDays
	}
	left := float64(WatchDays) - (s.nowTS()-*s.pol.ReadSince)/86400
	if left <= 0 {
		return 0
	}
	return int(math.Ceil(left))
}

func (s *Server) status(ctx context.Context) map[string]any {
	now := s.nowTS()
	s.mu.Lock()
	pol := s.pol
	killed, reason := s.killed, s.killReason
	sent := append([]policy.Sent(nil), s.sent...)
	days := s.watchDaysLeftLocked()
	s.mu.Unlock()
	conn, linked := engine.StateNotLinked, false
	if s.eng != nil {
		conn, linked = s.eng.State(), s.eng.IsLoggedIn()
	}
	chats, _ := s.st.AllChats(ctx)
	counts, _ := s.st.MessageCount(ctx)
	nChats, nMsgs, nUnread := 0, 0, 0
	for _, c := range chats {
		if s.readable(c.ID) {
			nChats++
			nMsgs += counts[c.ID]
			nUnread += c.Unread
		}
	}
	a, h, d, r := policy.RateCounts(now, sent)
	var phone any
	if v, ok := s.st.GetMeta(ctx, "phone_last_seen"); ok {
		phone = v
	}
	var warn any
	if pol.AccountWarning != nil {
		warn = pol.AccountWarning
	}
	var kr any
	if killed {
		kr = reason
	}
	store := "ok"
	if s.cfg.StoreLocked {
		store = "locked"
	}
	return map[string]any{
		"v": ProtocolV, "engine": s.engineName(), "version": s.cfg.Version, "mode": pol.Mode, "killed": killed,
		"kill_reason": kr, "connection": conn, "linked": linked, "device_name": "JARVES bridge",
		"phone_last_seen": phone, "account_warning": warn, "warnings_total": pol.WarningsTotal,
		"read_since": pol.ReadSince, "watch_days_left": days, "retention_days": pol.RetentionDays,
		"keep_nothing": pol.KeepNothing,
		"counts":       map[string]int{"chats": nChats, "messages": nMsgs, "unread": nUnread},
		"rate":         map[string]any{"last_15s": a, "hour": h, "day": d, "recipients_hour": r, "limits": policy.Limits},
		"read_scope": map[string]any{"all_direct": pol.ReadScope.AllDirect, "groups": pol.ReadScope.Groups,
			"allow": len(pol.ReadAllow)},
		"send_allow": len(pol.SendAllow), "started_at": s.startedAt, "now": now,
		"key_protection": s.cfg.KeyProtection, "store": store,
	}
}

func msgMine(to, id string, at float64, text string) msgstore.Message {
	return msgstore.Message{ID: id, Chat: to, FromMe: true, TS: at, Kind: "text", Text: text}
}

// chatJSON adds can_send to a stored chat.
func (s *Server) chatJSON(c msgstore.Chat) map[string]any {
	s.mu.Lock()
	canSend := !c.IsGroup && contains(s.pol.SendAllow, c.ID)
	s.mu.Unlock()
	return map[string]any{"id": c.ID, "name": c.Name, "number": c.Number, "is_group": c.IsGroup, "unread": c.Unread,
		"last_ts": c.LastTS, "has_history": c.HasHistory, "lid": c.LID, "can_send": canSend}
}

// normalizeChat turns "+972..." into "972...@s.whatsapp.net" and validates JIDs.
func normalizeChat(c string) (string, bool) {
	c = strings.TrimSpace(c)
	if strings.HasPrefix(c, "+") {
		d := c[1:]
		if len(d) < 6 || len(d) > 15 || strings.Trim(d, "0123456789") != "" {
			return "", false
		}
		return d + "@s.whatsapp.net", true
	}
	if policy.IsDirect(c) {
		return c, true
	}
	if policy.IsGroup(c) {
		id := strings.TrimSuffix(c, "@g.us")
		if id != "" && len(id) <= 64 && strings.Trim(id, "0123456789-") == "" {
			return c, true
		}
	}
	return "", false
}

func sortedUnique(l []string) []string {
	m := map[string]bool{}
	out := []string{}
	for _, x := range l {
		if !m[x] {
			m[x] = true
			out = append(out, x)
		}
	}
	sort.Strings(out)
	return out
}
