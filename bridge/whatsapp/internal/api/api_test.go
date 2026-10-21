package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"

	"github.com/igalgerman/jarves/bridge/whatsapp/internal/engine"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/memdb"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/msgstore"
	"github.com/igalgerman/jarves/bridge/whatsapp/internal/policy"
)

const (
	dana       = "972541112222@s.whatsapp.net"
	danaNum    = "+972541112222"
	stranger   = "972543334444@s.whatsapp.net"
	group      = "120363000000000001@g.us"
	secretText = "SECRET-TEXT-אני מאחר 🙂"
)

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) Now() time.Time      { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) Add(d time.Duration) { c.mu.Lock(); c.t = c.t.Add(d); c.mu.Unlock() }

type env struct {
	t     *testing.T
	s     *Server
	fake  *engine.Fake
	st    *msgstore.Store
	clk   *clock
	ts    *httptest.Server
	user  string
	admin string
	dir   string
	logs  *bytes.Buffer
	seals *atomic.Int32
	hour  *atomic.Int32
}

type syncBuf struct {
	mu sync.Mutex
	b  *bytes.Buffer
}

func (w *syncBuf) Write(p []byte) (int, error) { w.mu.Lock(); defer w.mu.Unlock(); return w.b.Write(p) }

func newEnv(t *testing.T, linked bool, mutate ...func(*Config)) *env {
	t.Helper()
	ctx := context.Background()
	db, err := memdb.Open(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	st, err := msgstore.New(ctx, db.DB)
	if err != nil {
		t.Fatal(err)
	}
	clk := &clock{t: time.Unix(1790000000, 0)}
	fake := engine.NewFake(linked)
	fake.Now = clk.Now
	logs := &bytes.Buffer{}
	seals := &atomic.Int32{}
	hour := &atomic.Int32{}
	hour.Store(12)
	dir := filepath.Join(t.TempDir(), "whatsapp_bridge")
	cfg := Config{Dir: dir, Version: "test", Engine: fake, Store: st, Changes: db.Changes,
		Seal: func(context.Context) error { seals.Add(1); return nil }, Log: NewLogger(&syncBuf{b: logs}), Now: clk.Now,
		LocalHour: func(time.Time) int { return int(hour.Load()) }, SealEvery: 50 * time.Millisecond}
	for _, m := range mutate {
		m(&cfg)
	}
	s, err := New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(s.Handler())
	u, _ := url.Parse(ts.URL)
	port, _ := strconv.Atoi(u.Port())
	s.SetPort(port)
	sctx, cancel := context.WithCancel(ctx)
	s.Start(sctx)
	t.Cleanup(func() { ts.Close(); cancel(); s.Stop() })
	user, admin := s.Tokens()
	return &env{t: t, s: s, fake: fake, st: st, clk: clk, ts: ts, user: user, admin: admin, dir: dir, logs: logs,
		seals: seals, hour: hour}
}

type resp struct {
	code int
	body map[string]any
	raw  string
}

func (r resp) errCode() string {
	e, _ := r.body["error"].(map[string]any)
	c, _ := e["code"].(string)
	return c
}

func (e *env) do(method, path, token string, body any) resp {
	e.t.Helper()
	var rd io.Reader
	if body != nil {
		if s, ok := body.(string); ok {
			rd = strings.NewReader(s)
		} else {
			b, _ := json.Marshal(body)
			rd = bytes.NewReader(b)
		}
	}
	req, _ := http.NewRequest(method, e.ts.URL+path, rd)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	defer res.Body.Close()
	raw, _ := io.ReadAll(res.Body)
	out := resp{code: res.StatusCode, raw: string(raw)}
	_ = json.Unmarshal(raw, &out.body)
	if res.Header.Get("Access-Control-Allow-Origin") != "" {
		e.t.Fatal("CORS header present")
	}
	return out
}

func (e *env) mustOK(r resp) resp {
	e.t.Helper()
	if r.code != 200 {
		e.t.Fatalf("want 200, got %d %s", r.code, r.raw)
	}
	return r
}

func (e *env) mode(m string, override bool) {
	e.t.Helper()
	e.mustOK(e.do("POST", "/mode", e.admin, map[string]any{"mode": m, "override_watch": override}))
}

func (e *env) msg(chat, id string, ageS float64, text string, history bool) {
	info := msgstore.ChatInfo{ID: chat, Name: "Someone"}
	if chat == dana {
		info.Name, info.Number = "Dana", danaNum
	}
	e.fake.Emit(engine.MessageEvent{
		Msg:     msgstore.Message{ID: id, Chat: chat, TS: float64(e.clk.Now().Unix()) - ageS, Kind: "text", Text: text, SenderName: info.Name},
		Chat:    info,
		History: history,
	})
}

func (e *env) approval(to, text string, night bool) string {
	n := fmt.Sprintf("%032x", nonceSeq.Add(1))
	return policy.Sign(e.s.ApprovalKey(), policy.Payload{V: 1, N: n, To: to, H: policy.TextHash(to, text),
		Exp: float64(e.clk.Now().Unix() + 120), Night: night})
}

var nonceSeq atomic.Int64

func waitFor(t *testing.T, d time.Duration, cond func() bool) bool {
	t.Helper()
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		if cond() {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return cond()
}

func TestStateFilesTokensAndAuth(t *testing.T) {
	e := newEnv(t, true)
	for name, re := range map[string]string{FileToken: "wab_", FileAdmin: "waa_"} {
		b, err := os.ReadFile(filepath.Join(e.dir, name))
		if err != nil || !strings.HasPrefix(string(b), re) || len(b) != 47 {
			t.Fatalf("%s: %q %v", name, b, err)
		}
	}
	k, _ := os.ReadFile(filepath.Join(e.dir, FileApproval))
	if !reKeyHex.Match(k) {
		t.Fatalf("approval.key %q", k)
	}
	if r := e.do("GET", "/status", "", nil); r.code != 401 || r.errCode() != "WA_UNAUTHORIZED" {
		t.Fatalf("no token: %d", r.code)
	}
	if r := e.do("GET", "/status", "wab_wrong", nil); r.code != 401 {
		t.Fatalf("wrong token: %d", r.code)
	}
	e.mustOK(e.do("GET", "/status", e.user, nil))
	e.mustOK(e.do("GET", "/status", e.admin, nil))
	for _, p := range []struct{ m, path string }{{"POST", "/mode"}, {"POST", "/allow"}, {"POST", "/read_scope"},
		{"POST", "/kill"}, {"POST", "/link"}, {"GET", "/link"}, {"POST", "/unlink"}, {"POST", "/retention"},
		{"POST", "/purge"}, {"GET", "/audit"}, {"GET", "/policy"}, {"POST", "/shutdown"}} {
		if r := e.do(p.m, p.path, e.user, map[string]any{}); r.code != 403 || r.errCode() != "WA_FORBIDDEN" {
			t.Fatalf("%s %s with user token: %d %s", p.m, p.path, r.code, r.raw)
		}
	}
	if r := e.do("GET", "/nope", e.user, nil); r.code != 404 || r.errCode() != "WA_NOT_FOUND" {
		t.Fatalf("404: %d", r.code)
	}
	if r := e.do("DELETE", "/status", e.user, nil); r.code != 405 {
		t.Fatalf("405: %d", r.code)
	}
	big := `{"chat":"` + strings.Repeat("a", MaxBody) + `"}`
	if r := e.do("POST", "/seen", e.user, big); r.code != 413 || r.errCode() != "WA_BAD_REQUEST" {
		t.Fatalf("413: %d %s", r.code, r.raw)
	}
	if r := e.do("POST", "/mode", e.admin, "{not json"); r.code != 400 || r.errCode() != "WA_BAD_REQUEST" {
		t.Fatalf("bad json: %d", r.code)
	}
	// DNS-rebinding guard
	req, _ := http.NewRequest("GET", e.ts.URL+"/status", nil)
	req.Host = "evil.example:80"
	req.Header.Set("Authorization", "Bearer "+e.user)
	res, _ := http.DefaultClient.Do(req)
	if res.StatusCode != 403 {
		t.Fatalf("host guard: %d", res.StatusCode)
	}
	res.Body.Close()
	req.Host = "localhost:" + strconv.Itoa(e.s.port)
	res, _ = http.DefaultClient.Do(req)
	if res.StatusCode != 200 {
		t.Fatalf("localhost host: %d", res.StatusCode)
	}
	res.Body.Close()
	// tokens are stable across restarts
	s2, err := New(Config{Dir: e.dir, Store: e.st, Log: NewLogger(io.Discard)})
	if err != nil {
		t.Fatal(err)
	}
	u2, a2 := s2.Tokens()
	if u2 != e.user || a2 != e.admin {
		t.Fatal("tokens regenerated")
	}
}

func TestStatusFields(t *testing.T) {
	e := newEnv(t, true)
	r := e.mustOK(e.do("GET", "/status", e.user, nil))
	for _, k := range []string{"v", "engine", "version", "mode", "killed", "kill_reason", "connection", "linked",
		"device_name", "phone_last_seen", "account_warning", "warnings_total", "read_since", "watch_days_left",
		"retention_days", "keep_nothing", "counts", "rate", "read_scope", "send_allow", "started_at", "now",
		"key_protection"} {
		if _, ok := r.body[k]; !ok {
			t.Fatalf("status lacks %s", k)
		}
	}
	if r.body["device_name"] != "JARVES bridge" || r.body["mode"] != "off" || r.body["engine"] != "fake" ||
		r.body["connection"] != "disconnected" || r.body["retention_days"].(float64) != 14 {
		t.Fatalf("%v", r.raw)
	}
	rate := r.body["rate"].(map[string]any)
	lim := rate["limits"].(map[string]any)
	if lim["per_15s"].(float64) != 1 || lim["per_hour"].(float64) != 10 || lim["per_day"].(float64) != 30 ||
		lim["recipients_per_hour"].(float64) != 3 {
		t.Fatal(rate)
	}
	pol := e.mustOK(e.do("GET", "/policy", e.admin, nil))
	if pol.body["mode"] != "off" || pol.body["read_scope"].(map[string]any)["all_direct"] != true || pol.body["read_since"] != nil {
		t.Fatal(pol.raw)
	}
}

func TestModesReadGateAndWatchWindow(t *testing.T) {
	e := newEnv(t, true)
	for _, p := range []string{"/chats", "/messages?chat=" + dana, "/search?q=x"} {
		if r := e.do("GET", p, e.user, nil); r.code != 409 || r.errCode() != "WA_MODE_OFF" {
			t.Fatalf("%s in off: %d", p, r.code)
		}
	}
	if r := e.do("POST", "/seen", e.user, map[string]any{"chat": dana}); r.errCode() != "WA_MODE_OFF" {
		t.Fatal("seen in off")
	}
	if r := e.do("POST", "/mode", e.admin, map[string]any{"mode": "loud"}); r.code != 400 {
		t.Fatal("bad mode")
	}
	e.mode("read", false)
	if !waitFor(t, 2*time.Second, e.fake.IsConnected) {
		t.Fatal("engine did not connect in read mode")
	}
	r := e.do("POST", "/mode", e.admin, map[string]any{"mode": "send"})
	if r.code != 409 || r.errCode() != "WA_WATCH_WINDOW" || r.body["error"].(map[string]any)["days_left"].(float64) != 14 {
		t.Fatalf("watch: %s", r.raw)
	}
	e.clk.Add(10 * 24 * time.Hour)
	r = e.do("POST", "/mode", e.admin, map[string]any{"mode": "send"})
	if r.body["error"].(map[string]any)["days_left"].(float64) != 4 {
		t.Fatalf("days_left: %s", r.raw)
	}
	if st := e.mustOK(e.do("GET", "/status", e.user, nil)); st.body["watch_days_left"].(float64) != 4 {
		t.Fatal(st.raw)
	}
	e.clk.Add(4 * 24 * time.Hour)
	e.mode("send", false)
	// a warning restarts the window and needs off -> read
	e.mode("read", false)
	e.fake.Emit(engine.AccountWarningEvent{Code: engine.WarnStreamReplaced})
	e.clk.Add(15 * 24 * time.Hour)
	if r := e.do("POST", "/mode", e.admin, map[string]any{"mode": "send"}); r.errCode() != "WA_WATCH_WINDOW" {
		t.Fatalf("after warning: %s", r.raw)
	}
	if pol := e.mustOK(e.do("GET", "/policy", e.admin, nil)); pol.body["read_since"] != nil {
		t.Fatalf("a warning must reset read_since to null: %s", pol.raw)
	}
	// selecting read again while read_since is null restarts the window (and clears warnings_since_read)
	e.mode("read", false)
	if pol := e.mustOK(e.do("GET", "/policy", e.admin, nil)); pol.body["read_since"] == nil || pol.body["warnings_since_read"].(float64) != 0 {
		t.Fatalf("%s", pol.raw)
	}
	e.fake.Emit(engine.AccountWarningEvent{Code: engine.WarnStreamReplaced})
	e.mode("send", true) // override_watch
	pol := e.mustOK(e.do("GET", "/policy", e.admin, nil))
	if pol.body["mode"] != "send" || pol.body["warnings_since_read"].(float64) != 1 || pol.body["warnings_total"].(float64) != 2 {
		t.Fatal(pol.raw)
	}
	b, _ := os.ReadFile(filepath.Join(e.dir, FilePolicy))
	if !strings.Contains(string(b), `"mode": "send"`) {
		t.Fatal("policy.json not persisted")
	}
	e.mode("off", false)
	if !waitFor(t, 2*time.Second, func() bool { return !e.fake.IsConnected() }) {
		t.Fatal("engine still connected in off mode")
	}
}

func TestChatsMessagesSearchSeen(t *testing.T) {
	e := newEnv(t, true)
	e.mode("read", false)
	e.msg(dana, "m1", 300, "Hello Dana", false)
	e.msg(dana, "m2", 200, "שלום עולם", false)
	e.fake.Emit(engine.MessageEvent{Msg: msgstore.Message{ID: "m3", Chat: dana, FromMe: true, TS: float64(e.clk.Now().Unix() - 100), Kind: "image",
		Media: &msgstore.Media{Mime: "image/jpeg", Size: 1234, Caption: "beach"}}})
	e.msg(group, "g1", 50, "group secret", false)
	e.msg(stranger, "s1", 10, "from a stranger", false)

	r := e.mustOK(e.do("GET", "/chats", e.user, nil))
	chats := r.body["chats"].([]any)
	if len(chats) != 2 {
		t.Fatalf("groups must be hidden by default: %s", r.raw)
	}
	c0 := chats[0].(map[string]any)
	c1 := chats[1].(map[string]any)
	if c0["id"] != stranger || c1["id"] != dana || c1["name"] != "Dana" || c1["number"] != danaNum ||
		c1["unread"].(float64) != 2 || c1["has_history"] != true || c1["can_send"] != false || c1["is_group"] != false {
		t.Fatalf("%s", r.raw)
	}
	for _, k := range []string{"id", "name", "number", "is_group", "unread", "last_ts", "has_history", "lid", "can_send"} {
		if _, ok := c1[k]; !ok {
			t.Fatalf("chat lacks %s", k)
		}
	}
	m := e.mustOK(e.do("GET", "/messages?chat="+url.QueryEscape(danaNum), e.user, nil))
	msgs := m.body["messages"].([]any)
	if len(msgs) != 3 || msgs[0].(map[string]any)["id"] != "m1" || msgs[2].(map[string]any)["kind"] != "image" ||
		msgs[2].(map[string]any)["text"] != "" || msgs[2].(map[string]any)["media"].(map[string]any)["mime"] != "image/jpeg" {
		t.Fatalf("%s", m.raw)
	}
	if m.body["chat"].(map[string]any)["id"] != dana {
		t.Fatal("messages.chat")
	}
	for _, k := range []string{"id", "chat", "from_me", "sender_name", "ts", "kind", "text", "media", "edited", "deleted"} {
		if _, ok := msgs[0].(map[string]any)[k]; !ok {
			t.Fatalf("message lacks %s", k)
		}
	}
	if m := e.mustOK(e.do("GET", "/messages?limit=1&chat="+dana, e.user, nil)); m.body["messages"].([]any)[0].(map[string]any)["id"] != "m3" {
		t.Fatal("limit keeps the newest")
	}
	if r := e.do("GET", "/messages?chat="+group, e.user, nil); r.code != 404 || r.errCode() != "WA_CHAT_NOT_FOUND" {
		t.Fatalf("group readable: %d", r.code)
	}
	if r := e.do("GET", "/messages", e.user, nil); r.code != 400 {
		t.Fatal("missing chat")
	}
	s := e.mustOK(e.do("GET", "/search?q=HELLO", e.user, nil))
	if got := s.body["messages"].([]any); len(got) != 1 || got[0].(map[string]any)["id"] != "m1" {
		t.Fatalf("search: %s", s.raw)
	}
	if s := e.mustOK(e.do("GET", "/search?q="+url.QueryEscape("עולם"), e.user, nil)); len(s.body["messages"].([]any)) != 1 {
		t.Fatal("hebrew search")
	}
	if s := e.mustOK(e.do("GET", "/search?q=secret", e.user, nil)); len(s.body["messages"].([]any)) != 0 {
		t.Fatal("group leaked into search")
	}
	// group readable only with groups=true AND allow-listed
	e.mustOK(e.do("POST", "/read_scope", e.admin, map[string]any{"groups": true}))
	if r := e.do("GET", "/messages?chat="+group, e.user, nil); r.code != 404 {
		t.Fatal("group without allow-list")
	}
	e.mustOK(e.do("POST", "/allow", e.admin, map[string]any{"list": "read", "op": "add", "chat": group}))
	if r := e.mustOK(e.do("GET", "/messages?chat="+group, e.user, nil)); len(r.body["messages"].([]any)) != 0 {
		t.Fatal("group history before allow-listing must not have been stored")
	}
	// all_direct off -> only allow-listed direct chats
	e.mustOK(e.do("POST", "/read_scope", e.admin, map[string]any{"all_direct": false}))
	e.mustOK(e.do("POST", "/allow", e.admin, map[string]any{"list": "read", "op": "add", "chat": danaNum}))
	r = e.mustOK(e.do("GET", "/chats", e.user, nil))
	if len(r.body["chats"].([]any)) != 2 { // dana + group
		t.Fatalf("%s", r.raw)
	}
	e.mustOK(e.do("POST", "/seen", e.user, map[string]any{"chat": dana}))
	c, _, _ := e.st.GetChat(context.Background(), dana)
	if c.Unread != 0 {
		t.Fatal("seen")
	}
	if r := e.do("POST", "/seen", e.user, map[string]any{"chat": stranger}); r.code != 404 {
		t.Fatal("seen on unreadable chat")
	}
	e.mustOK(e.do("POST", "/allow", e.admin, map[string]any{"list": "send", "op": "add", "chat": danaNum}))
	if r := e.do("POST", "/allow", e.admin, map[string]any{"list": "send", "op": "add", "chat": group}); r.errCode() != "WA_GROUP_REFUSED" {
		t.Fatal("group on send list")
	}
	if r := e.do("POST", "/allow", e.admin, map[string]any{"list": "send", "op": "add", "chat": "+12"}); r.code != 400 {
		t.Fatal("bad number")
	}
	r = e.mustOK(e.do("GET", "/chats", e.user, nil))
	for _, c := range r.body["chats"].([]any) {
		cm := c.(map[string]any)
		if want := cm["id"] == dana; cm["can_send"] != want {
			t.Fatalf("can_send %s", r.raw)
		}
	}
	// phone_last_seen = the last from_me message not sent by the bridge
	st := e.mustOK(e.do("GET", "/status", e.user, nil))
	if st.body["phone_last_seen"].(float64) != float64(e.clk.Now().Unix()-100) {
		t.Fatal(st.raw)
	}
}

func setupSend(t *testing.T) *env {
	e := newEnv(t, true)
	e.mode("send", true)
	e.mustOK(e.do("POST", "/allow", e.admin, map[string]any{"list": "send", "op": "add", "chat": dana}))
	e.msg(dana, "h1", 1000, "earlier chat", false)
	if !waitFor(t, 2*time.Second, e.fake.IsConnected) {
		t.Fatal("not connected")
	}
	return e
}

func (e *env) send(to, text, approval string) resp {
	return e.do("POST", "/send", e.user, map[string]any{"to": to, "text": text, "approval_id": approval})
}

func TestSendApprovalReuseAndAudit(t *testing.T) {
	e := setupSend(t)
	if r := e.send(dana, secretText, ""); r.code != 403 || r.errCode() != "WA_APPROVAL_MISSING" {
		t.Fatalf("%s", r.raw)
	}
	ap := e.approval(dana, secretText, false)
	if r := e.send(stranger, secretText, ap); r.errCode() != "WA_NOT_ALLOWED" {
		t.Fatal(r.raw)
	}
	r := e.mustOK(e.send(dana, secretText, ap))
	if r.body["ok"] != true || r.body["id"] == "" || len(e.fake.Sent) != 1 || e.fake.Sent[0].Text != secretText {
		t.Fatalf("%s", r.raw)
	}
	e.clk.Add(20 * time.Second)
	if r := e.send(dana, secretText, ap); r.code != 403 || r.errCode() != "WA_APPROVAL_REUSED" {
		t.Fatalf("reuse: %s", r.raw)
	}
	// the reuse protection survives a restart (nonces.json)
	if used := loadNonces(filepath.Join(e.dir, FileNonces), float64(e.clk.Now().Unix())); len(used) != 1 {
		t.Fatal("nonce not persisted")
	}
	// a refused request leaves the approval usable
	ap2 := e.approval(dana, "second", false)
	e.hour.Store(23)
	if r := e.send(dana, "second", ap2); r.errCode() != "WA_QUIET_HOURS" {
		t.Fatal(r.raw)
	}
	e.hour.Store(12)
	e.mustOK(e.send(dana, "second", ap2))
	// the sent message is stored as from_me, and does NOT count as phone activity
	msgs, _ := e.st.Messages(context.Background(), dana, 0, 0, 10)
	if len(msgs) != 3 || !msgs[2].FromMe {
		t.Fatalf("%+v", msgs)
	}
	a := e.mustOK(e.do("GET", "/audit", e.admin, nil))
	lines := a.body["lines"].([]any)
	if len(lines) != 6 {
		t.Fatalf("audit lines: %s", a.raw)
	}
	raw, _ := os.ReadFile(filepath.Join(e.dir, FileAudit))
	for _, bad := range []string{secretText, "SECRET", dana, "972541112222", "second"} {
		if strings.Contains(string(raw), bad) {
			t.Fatalf("audit leaks %q", bad)
		}
	}
	first := lines[2].(map[string]any)
	if first["result"] != "sent" || first["chat_hash"] != ChatHash(dana) || len(first["chat_hash"].(string)) != 16 ||
		first["text_sha256"] != sha256Hex(secretText) || first["chars"].(float64) != float64(len([]rune(secretText))) {
		t.Fatalf("%v", first)
	}
	// engine failure -> 502, nonce consumed, not counted
	e.clk.Add(20 * time.Second)
	e.fake.SendErr = fmt.Errorf("boom")
	ap3 := e.approval(dana, "third", false)
	if r := e.send(dana, "third", ap3); r.code != 502 || r.errCode() != "WA_SEND_FAILED" {
		t.Fatal(r.raw)
	}
	e.fake.SendErr = nil
	e.clk.Add(20 * time.Second)
	if r := e.send(dana, "third", ap3); r.errCode() != "WA_APPROVAL_REUSED" {
		t.Fatal(r.raw)
	}
	e.fake.Disconnect()
	if r := e.send(dana, "fourth", e.approval(dana, "fourth", false)); r.code != 503 || r.errCode() != "WA_NOT_CONNECTED" {
		t.Fatal(r.raw)
	}
}

func TestRateLimitEleventhInAnHour(t *testing.T) {
	e := setupSend(t)
	for i := 0; i < 10; i++ {
		text := fmt.Sprintf("msg %d", i)
		e.mustOK(e.send(dana, text, e.approval(dana, text, false)))
		e.clk.Add(16 * time.Second)
	}
	r := e.send(dana, "eleven", e.approval(dana, "eleven", false))
	if r.code != 429 || r.errCode() != "WA_RATE_LIMITED" {
		t.Fatalf("%s", r.raw)
	}
	er := r.body["error"].(map[string]any)
	if er["limit"] != "per_hour" || er["retry_after_s"].(float64) < 1 {
		t.Fatal(r.raw)
	}
	st := e.mustOK(e.do("GET", "/status", e.user, nil))
	if st.body["rate"].(map[string]any)["hour"].(float64) != 10 {
		t.Fatal(st.raw)
	}
	// counters survive a restart (rebuilt from audit.jsonl)
	s2, _ := New(Config{Dir: e.dir, Store: e.st, Log: NewLogger(io.Discard), Now: e.clk.Now})
	if len(s2.sent) != 10 {
		t.Fatalf("sent rebuilt: %d", len(s2.sent))
	}
	e.clk.Add(time.Hour)
	e.mustOK(e.send(dana, "later", e.approval(dana, "later", false)))
}

func TestKillFileStopsWithinTwoSeconds(t *testing.T) {
	e := setupSend(t)
	if err := os.WriteFile(filepath.Join(e.dir, FileKill), []byte("cli: owner pressed kill\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	if !waitFor(t, 2*time.Second, func() bool { return !e.fake.IsConnected() }) {
		t.Fatal("engine not disconnected within 2 s")
	}
	if el := time.Since(start); el > 2*time.Second {
		t.Fatalf("kill took %v", el)
	}
	st := e.mustOK(e.do("GET", "/status", e.user, nil))
	if st.body["killed"] != true || st.body["kill_reason"] != "cli:_owner_pressed_kill" {
		t.Fatalf("%s", st.raw)
	}
	if r := e.send(dana, "x", e.approval(dana, "x", false)); r.code != 423 || r.errCode() != "WA_KILLED" {
		t.Fatal(r.raw)
	}
	e.mustOK(e.do("GET", "/chats", e.user, nil)) // reads keep working on stored data
	if r := e.do("POST", "/link", e.admin, map[string]any{}); r.code != 423 {
		t.Fatal("link while killed")
	}
	e.mustOK(e.do("POST", "/kill", e.admin, map[string]any{"on": false}))
	if _, err := os.Stat(filepath.Join(e.dir, FileKill)); !os.IsNotExist(err) {
		t.Fatal("KILL not removed")
	}
	if !waitFor(t, 2*time.Second, e.fake.IsConnected) {
		t.Fatal("no reconnect after release")
	}
	// POST /kill on creates the file
	e.mustOK(e.do("POST", "/kill", e.admin, map[string]any{"on": true}))
	if on, reason := readKill(filepath.Join(e.dir, FileKill)); !on || reason != "admin" {
		t.Fatal("KILL file")
	}
	if e.fake.IsConnected() {
		t.Fatal("still connected after POST /kill")
	}
	// logout:true also unlinks
	e.mustOK(e.do("POST", "/kill", e.admin, map[string]any{"on": true, "logout": true}))
	if e.fake.Logouts != 1 || e.fake.IsLoggedIn() {
		t.Fatal("logout")
	}
	if r := e.do("POST", "/kill", e.admin, map[string]any{}); r.code != 400 {
		t.Fatal("kill without on")
	}
}

func TestAutoKillOnWarningAndLogout(t *testing.T) {
	for _, tc := range []struct {
		ev     engine.Event
		reason string
		typ    string
	}{
		{engine.AccountWarningEvent{Code: engine.WarnTemporaryBan}, "account_warning:temporary_ban", "account.warning"},
		{engine.LoggedOutEvent{Reason: "phone"}, "logged_out", "logged_out"},
	} {
		e := setupSend(t)
		ws := e.dial(t, e.user)
		e.fake.Emit(tc.ev)
		on, reason := readKill(filepath.Join(e.dir, FileKill))
		if !on || reason != tc.reason {
			t.Fatalf("auto-kill: %v %q", on, reason)
		}
		if e.fake.IsConnected() {
			t.Fatal("still connected")
		}
		types := ws.until(t, "killed")
		if !contains(types, tc.typ) {
			t.Fatalf("events %v", types)
		}
		st := e.mustOK(e.do("GET", "/status", e.user, nil))
		if tc.typ == "account.warning" && (st.body["account_warning"].(map[string]any)["code"] != engine.WarnTemporaryBan || st.body["warnings_total"].(float64) != 1) {
			t.Fatal(st.raw)
		}
	}
}

func TestRetentionPurgeAndKeepNothing(t *testing.T) {
	e := newEnv(t, true)
	e.mode("read", false)
	e.msg(dana, "old", 20*86400, "too old", true) // history sync older than retention: not stored
	e.msg(dana, "a", 10*86400, "ten days", false)
	e.msg(dana, "b", 60, "fresh", false)
	n, _ := e.st.MessageCount(context.Background())
	if n[dana] != 2 {
		t.Fatalf("stored %d", n[dana])
	}
	e.mustOK(e.do("POST", "/retention", e.admin, map[string]any{"days": 7}))
	n, _ = e.st.MessageCount(context.Background())
	if n[dana] != 1 {
		t.Fatalf("after retention 7: %d", n[dana])
	}
	for _, bad := range []any{0, 91, nil} {
		if r := e.do("POST", "/retention", e.admin, map[string]any{"days": bad}); r.code != 400 {
			t.Fatalf("days %v accepted", bad)
		}
	}
	// hourly purge: time passes, the tick deletes
	e.clk.Add(8 * 24 * time.Hour)
	e.s.purgeRetention()
	n, _ = e.st.MessageCount(context.Background())
	if n[dana] != 0 {
		t.Fatal("hourly purge")
	}
	if !e.st.HasHistory(context.Background(), dana) {
		t.Fatal("has_history must survive the purge")
	}
	e.msg(dana, "c", 0, "now", false)
	e.msg(dana, "d", 0, "now2", false)
	if r := e.mustOK(e.do("POST", "/purge", e.admin, map[string]any{"older_than_days": nil})); r.body["deleted"].(float64) != 2 {
		t.Fatal(r.raw)
	}
	e.mustOK(e.do("POST", "/retention", e.admin, map[string]any{"days": 14, "keep_nothing": true}))
	e.msg(dana, "e", 0, "live only", false)
	n, _ = e.st.MessageCount(context.Background())
	if n[dana] != 0 {
		t.Fatal("keep_nothing stored a message")
	}
	if st := e.mustOK(e.do("GET", "/status", e.user, nil)); st.body["keep_nothing"] != true {
		t.Fatal(st.raw)
	}
	if r := e.do("POST", "/purge", e.admin, map[string]any{"older_than_days": -1}); r.code != 400 {
		t.Fatal("negative purge")
	}
}

func TestRetentionPurgeAtStart(t *testing.T) {
	ctx := context.Background()
	db, _ := memdb.Open(ctx, nil)
	defer db.Close()
	st, _ := msgstore.New(ctx, db.DB)
	_ = st.AddMessage(ctx, msgstore.Message{ID: "x", Chat: dana, TS: 1790000000 - 30*86400, Kind: "text", Text: "old"}, true, false)
	dir := filepath.Join(t.TempDir(), "whatsapp_bridge")
	s, _ := New(Config{Dir: dir, Store: st, Log: NewLogger(io.Discard), Now: func() time.Time { return time.Unix(1790000000, 0) }})
	s.Start(ctx)
	defer s.Stop()
	if n, _ := st.MessageCount(ctx); n[dana] != 0 {
		t.Fatal("start purge")
	}
}

type wsClient struct {
	c   *websocket.Conn
	ctx context.Context
}

func (e *env) dial(t *testing.T, token string) *wsClient {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	h := http.Header{}
	h.Set("Authorization", "Bearer "+token)
	c, _, err := websocket.Dial(ctx, strings.Replace(e.ts.URL, "http", "ws", 1)+"/events", &websocket.DialOptions{HTTPHeader: h})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.CloseNow() })
	w := &wsClient{c: c, ctx: ctx}
	f := w.next(t)
	if f["type"] != "hello" || f["seq"].(float64) != 0 || f["status"] == nil {
		t.Fatalf("hello %v", f)
	}
	return w
}

func (w *wsClient) next(t *testing.T) map[string]any {
	t.Helper()
	_, b, err := w.c.Read(w.ctx)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	return m
}

// until reads frames until one of type typ and returns all types seen.
func (w *wsClient) until(t *testing.T, typ string) []string {
	t.Helper()
	var seen []string
	for {
		f := w.next(t)
		seen = append(seen, f["type"].(string))
		if f["type"] == typ {
			return seen
		}
	}
}

func TestEventsWebSocket(t *testing.T) {
	e := newEnv(t, true)
	// auth required
	_, res, err := websocket.Dial(context.Background(), strings.Replace(e.ts.URL, "http", "ws", 1)+"/events", nil)
	if err == nil || res.StatusCode != 401 {
		t.Fatal("ws without token")
	}
	ws := e.dial(t, e.user)
	e.mode("read", false)
	f := ws.next(t)
	for f["type"] == "connection.state" {
		f = ws.next(t)
	}
	if f["type"] != "mode.changed" || f["mode"] != "read" || f["seq"].(float64) < 1 {
		t.Fatalf("%v", f)
	}
	e.msg(group, "g1", 0, "group text", false) // unreadable: no event
	e.msg(dana, "m1", 0, secretText, false)
	f = ws.next(t)
	for f["type"] == "connection.state" {
		f = ws.next(t)
	}
	if f["type"] != "message.new" || f["chat"] != dana || f["id"] != "m1" || f["kind"] != "text" || f["from_me"] != false {
		t.Fatalf("%v", f)
	}
	if _, has := f["text"]; has {
		t.Fatal("message.new carries text")
	}
	e.fake.Emit(engine.ReceiptEvent{Chat: dana, ID: "m1", State: "read"})
	if f = ws.next(t); f["type"] != "receipt" || f["state"] != "read" {
		t.Fatalf("%v", f)
	}
	e.fake.Emit(engine.ReceiptEvent{Chat: group, ID: "g1", State: "read"})
	e.fake.Emit(engine.ConnectionEvent{State: "connecting"})
	if f = ws.next(t); f["type"] != "connection.state" || f["state"] != "connecting" {
		t.Fatalf("unreadable receipt leaked or order: %v", f)
	}
}

func TestLinkAndUnlink(t *testing.T) {
	e := newEnv(t, false)
	if g := e.mustOK(e.do("GET", "/link", e.admin, nil)); g.body["state"] != "idle" {
		t.Fatal(g.raw)
	}
	r := e.mustOK(e.do("POST", "/link", e.admin, map[string]any{}))
	if r.body["state"] != "waiting" || r.body["qr"] == "" || !strings.Contains(r.body["qr_text"].(string), "▀") ||
		r.body["expires_at"] == nil {
		t.Fatalf("%s", r.raw)
	}
	if st := e.mustOK(e.do("GET", "/status", e.user, nil)); st.body["connection"] != "linking" {
		t.Fatal(st.raw)
	}
	e.fake.CompleteLink()
	if g := e.mustOK(e.do("GET", "/link", e.admin, nil)); g.body["state"] != "linked" {
		t.Fatal(g.raw)
	}
	if r := e.do("POST", "/link", e.admin, map[string]any{}); r.code != 409 || r.errCode() != "WA_ALREADY_LINKED" {
		t.Fatal(r.raw)
	}
	e.mode("read", false)
	e.msg(dana, "m1", 0, "x", false)
	e.mustOK(e.do("POST", "/unlink", e.admin, nil))
	if e.fake.IsLoggedIn() {
		t.Fatal("still linked")
	}
	if all, _ := e.st.AllChats(context.Background()); len(all) != 0 {
		t.Fatal("store not wiped")
	}
	r = e.mustOK(e.do("POST", "/link", e.admin, map[string]any{"phone": "+972541112222"}))
	if r.body["pairing_code"] != "FAKE-CODE" {
		t.Fatal(r.raw)
	}
	if r := e.do("POST", "/link", e.admin, map[string]any{"phone": "0541112222"}); r.code != 400 {
		t.Fatal("phone without +")
	}
}

func TestStoreLockedRefusesLink(t *testing.T) {
	e := newEnv(t, false, func(c *Config) { c.StoreLocked = true })
	if r := e.do("POST", "/link", e.admin, map[string]any{}); r.code != 409 || r.errCode() != "WA_STORE_LOCKED" {
		t.Fatal(r.raw)
	}
	if st := e.mustOK(e.do("GET", "/status", e.user, nil)); st.body["store"] != "locked" {
		t.Fatal(st.raw)
	}
}

func TestListenEndpointShutdownAndSeal(t *testing.T) {
	ctx := context.Background()
	db, _ := memdb.Open(ctx, nil)
	defer db.Close()
	st, _ := msgstore.New(ctx, db.DB)
	dir := filepath.Join(t.TempDir(), "whatsapp_bridge")
	var seals atomic.Int32
	logs := &bytes.Buffer{}
	s, err := New(Config{Dir: dir, Version: "1.2.3", Engine: engine.NewFake(false), Store: st, Changes: db.Changes,
		Seal: func(context.Context) error { seals.Add(1); return nil }, Log: NewLogger(&syncBuf{b: logs})})
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- s.ListenAndServe(ctx, 0) }()
	var ep struct {
		V         int     `json:"v"`
		Port      int     `json:"port"`
		PID       int     `json:"pid"`
		Engine    string  `json:"engine"`
		Version   string  `json:"version"`
		StartedAt float64 `json:"started_at"`
	}
	if !waitFor(t, 5*time.Second, func() bool {
		b, err := os.ReadFile(filepath.Join(dir, FileEndpoint))
		return err == nil && json.Unmarshal(b, &ep) == nil
	}) {
		t.Fatal("endpoint.json")
	}
	if ep.V != 1 || ep.Port == 0 || ep.PID != os.Getpid() || ep.Engine != "fake" || ep.Version != "1.2.3" || ep.StartedAt == 0 {
		t.Fatalf("%+v", ep)
	}
	user, admin := s.Tokens()
	req, _ := http.NewRequest("GET", fmt.Sprintf("http://127.0.0.1:%d/status", ep.Port), nil)
	req.Header.Set("Authorization", "Bearer "+user)
	res, err := http.DefaultClient.Do(req)
	if err != nil || res.StatusCode != 200 {
		t.Fatalf("status over loopback: %v", err)
	}
	res.Body.Close()
	req, _ = http.NewRequest("POST", fmt.Sprintf("http://127.0.0.1:%d/shutdown", ep.Port), nil)
	req.Header.Set("Authorization", "Bearer "+admin)
	res, err = http.DefaultClient.Do(req)
	if err != nil || res.StatusCode != 200 {
		t.Fatal("shutdown")
	}
	res.Body.Close()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("did not stop")
	}
	if seals.Load() < 1 {
		t.Fatal("store not sealed at shutdown")
	}
	if _, err := os.Stat(filepath.Join(dir, FileEndpoint)); !os.IsNotExist(err) {
		t.Fatal("endpoint.json left behind")
	}
	if strings.Contains(logs.String(), user) || strings.Contains(logs.String(), admin) {
		t.Fatal("token logged")
	}
}

func TestSealOnlyWhenDirty(t *testing.T) {
	e := newEnv(t, true)
	time.Sleep(200 * time.Millisecond)
	base := e.seals.Load()
	time.Sleep(200 * time.Millisecond)
	if e.seals.Load() != base {
		t.Fatal("sealed without changes")
	}
	e.mode("read", false)
	e.msg(dana, "m", 0, "x", false)
	if !waitFor(t, 2*time.Second, func() bool { return e.seals.Load() > base }) {
		t.Fatal("not sealed after a change")
	}
}

func TestLogsNeverContainSecrets(t *testing.T) {
	e := setupSend(t)
	e.mustOK(e.send(dana, secretText, e.approval(dana, secretText, false)))
	e.send(stranger, secretText, "")
	e.fake.Emit(engine.AccountWarningEvent{Code: engine.WarnClientOutdated})
	logs := e.logs.String()
	if logs == "" {
		t.Fatal("no logs")
	}
	for _, bad := range []string{e.user, e.admin, secretText, "SECRET", "972541112222", "972543334444", "Dana",
		fmt.Sprintf("%x", e.s.ApprovalKey())} {
		if strings.Contains(logs, bad) {
			t.Fatalf("log leaks %q", bad)
		}
	}
	for _, line := range strings.Split(strings.TrimSpace(logs), "\n") {
		var l map[string]any
		if json.Unmarshal([]byte(line), &l) != nil {
			t.Fatalf("not JSON: %s", line)
		}
		for _, k := range []string{"ts", "level", "code", "msg", "fields"} {
			if _, ok := l[k]; !ok {
				t.Fatalf("log line lacks %s", k)
			}
		}
	}
}
