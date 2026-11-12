package api

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/engine"
	"github.com/Jarvis-igerman-ai/jarves/bridge/whatsapp/internal/msgstore"
)

// readyBackfill: read mode, connected, one stored recent message of dana (the anchor).
func readyBackfill(t *testing.T, mutate ...func(*Config)) *env {
	e := newEnv(t, true, mutate...)
	e.mode("read", false)
	e.msg(dana, "d1", 1000, "recent", false)
	if !waitFor(t, 2*time.Second, e.fake.IsConnected) {
		t.Fatal("not connected")
	}
	return e
}

// answer emits one on-demand history answer for chat with n messages older than ageS0 (oldest last).
func (e *env) answer(chat string, n int, ageS0 float64, tag string) {
	now := float64(e.clk.Now().Unix())
	for i := 0; i < n; i++ {
		e.fake.Emit(engine.MessageEvent{Msg: msgstore.Message{ID: fmt.Sprintf("%s%d", tag, i), Chat: chat,
			TS: now - ageS0 - float64(i), Kind: "text", Text: secretText}, Chat: msgstore.ChatInfo{ID: chat},
			History: true, OnDemand: true})
	}
	e.fake.Emit(engine.HistoryAnswerEvent{Chats: []string{chat}})
}

func (e *env) requests() []engine.FakeHistoryRequest {
	return e.fake.Requests()
}

func (e *env) drain(cursor int) resp {
	e.t.Helper()
	return e.mustOK(e.do("GET", fmt.Sprintf("/history/backfill?cursor=%d", cursor), e.admin, nil))
}

func TestBackfillWalksChatUntilWhatsAppHasNoMore(t *testing.T) {
	e := readyBackfill(t)
	if r := e.do("POST", "/history/backfill", e.user, map[string]any{}); r.code != 403 {
		t.Fatalf("user token: %d", r.code)
	}
	r := e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	if r.body["state"] != "running" || r.body["chats_total"] != 1.0 {
		t.Fatalf("start %s", r.raw)
	}
	reqs := e.requests()
	if len(reqs) != 1 || reqs[0].Chat != dana || reqs[0].Anchor.ID != "d1" || reqs[0].Count != 50 {
		t.Fatalf("first request %+v", reqs)
	}
	if r := e.do("POST", "/history/backfill", e.admin, map[string]any{}); r.errCode() != "WA_BACKFILL_RUNNING" {
		t.Fatalf("second start %s", r.raw)
	}
	e.answer(dana, 3, 200*86400, "old") // 200 days old: far past the 14-day bridge retention
	reqs = e.requests()
	if len(reqs) != 2 || reqs[1].Anchor.ID != "old2" {
		t.Fatalf("next request must anchor on the oldest answer: %+v", reqs)
	}
	got := e.drain(0)
	msgs, _ := got.body["messages"].([]any)
	if len(msgs) != 3 || got.body["next_cursor"] != 3.0 || msgs[0].(map[string]any)["text"] != secretText {
		t.Fatalf("drain %s", got.raw)
	}
	if stored, _ := e.st.Messages(e.s.ctx, dana, 0, 0, 500); len(stored) != 1 {
		t.Fatalf("old messages must never reach the bridge store: %d", len(stored))
	}
	e.answer(dana, 0, 0, "none") // WhatsApp has nothing older
	got = e.drain(3)
	if got.body["state"] != "done" || got.body["buffered"] != 0.0 || got.body["chats_done"] != 1.0 {
		t.Fatalf("end %s", got.raw)
	}
	logs := e.logs.String()
	for _, bad := range []string{secretText, "972541112222", "old2"} {
		if strings.Contains(logs, bad) {
			t.Fatalf("log leaks %q", bad)
		}
	}
}

func TestBackfillRefusals(t *testing.T) {
	e := newEnv(t, true)
	if r := e.do("POST", "/history/backfill", e.admin, map[string]any{}); r.errCode() != "WA_MODE_OFF" {
		t.Fatalf("mode off %s", r.raw)
	}
	e = readyBackfill(t)
	if r := e.do("POST", "/history/backfill", e.admin, map[string]any{"chat": stranger}); r.code != 404 {
		t.Fatalf("unknown chat %s", r.raw)
	}
	e.mustOK(e.do("POST", "/retention", e.admin, map[string]any{"days": 14, "keep_nothing": true}))
	if r := e.do("POST", "/history/backfill", e.admin, map[string]any{}); r.errCode() != "WA_KEEP_NOTHING" {
		t.Fatalf("keep nothing %s", r.raw)
	}
	e.mustOK(e.do("POST", "/retention", e.admin, map[string]any{"days": 14, "keep_nothing": false}))
	e.mustOK(e.do("POST", "/kill", e.admin, map[string]any{"on": true}))
	if r := e.do("POST", "/history/backfill", e.admin, map[string]any{}); r.errCode() != "WA_KILLED" {
		t.Fatalf("killed %s", r.raw)
	}
	l := newEnv(t, true, func(c *Config) { c.StoreLocked = true })
	l.mode("read", false)
	if r := l.do("POST", "/history/backfill", l.admin, map[string]any{}); r.errCode() != "WA_STORE_LOCKED" {
		t.Fatalf("locked %s", r.raw)
	}
}

func TestBackfillBufferClearedOnStopKillKeepNothingAndScope(t *testing.T) {
	for _, stop := range []string{"stop", "kill", "keep_nothing", "mode_off"} {
		e := readyBackfill(t)
		e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{"chat": danaNum}))
		e.answer(dana, 2, 100*86400, "x")
		switch stop {
		case "stop":
			e.mustOK(e.do("DELETE", "/history/backfill", e.admin, nil))
		case "kill":
			e.mustOK(e.do("POST", "/kill", e.admin, map[string]any{"on": true}))
		case "keep_nothing":
			e.mustOK(e.do("POST", "/retention", e.admin, map[string]any{"days": 14, "keep_nothing": true}))
		case "mode_off":
			e.mode("off", false)
		}
		e.s.mu.Lock()
		n, st := len(e.s.bf.buf), e.s.bf.state
		e.s.mu.Unlock()
		if n != 0 || st != "stopped" {
			t.Fatalf("%s: buffered %d state %s", stop, n, st)
		}
	}
	e := readyBackfill(t)
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 2, 100*86400, "y")
	e.mustOK(e.do("POST", "/read_scope", e.admin, map[string]any{"all_direct": false}))
	if msgs, _ := e.drain(0).body["messages"].([]any); len(msgs) != 0 {
		t.Fatal("a chat that left the read scope must not be drained")
	}
}

func TestBackfillBoundedBufferWaitsForDrain(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillBuffer = 60 })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 50, 30*86400, "a")
	if n := len(e.requests()); n != 1 {
		t.Fatalf("full buffer must pause requests: %d", n)
	}
	e.drain(0)
	if n := len(e.requests()); n != 1 {
		t.Fatal("unacknowledged messages still count")
	}
	if got := e.drain(50); got.body["buffered"] != 0.0 || len(e.requests()) != 2 {
		t.Fatalf("drain must resume: %s / %d", got.raw, len(e.requests()))
	}
}

func TestBackfillSinceAndUnansweredRequest(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillWait = 30 * time.Millisecond })
	since := float64(e.clk.Now().Unix()) - 50*86400
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{"since": since}))
	e.answer(dana, 1, 40*86400, "in")
	e.answer(dana, 1, 60*86400, "out") // older than since: not kept, chat finished
	got := e.drain(0)
	msgs, _ := got.body["messages"].([]any)
	if len(msgs) != 1 || got.body["state"] != "done" || len(e.requests()) != 2 {
		t.Fatalf("since %s", got.raw)
	}
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	if !waitFor(t, 2*time.Second, func() bool { return e.drain(0).body["state"] == "done" }) {
		t.Fatal("a request WhatsApp never answers must not hang the backfill")
	}
}
