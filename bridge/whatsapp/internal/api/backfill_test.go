package api

import (
	"fmt"
	"strings"
	"sync"
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

// timers replaces the wall clock for unanswered requests: tests fire them explicitly (hermetic, clock-free).
type timers struct {
	mu sync.Mutex
	fs []func()
}

func (tm *timers) after(_ time.Duration, f func()) {
	tm.mu.Lock()
	tm.fs = append(tm.fs, f)
	tm.mu.Unlock()
}

func (tm *timers) fire() {
	tm.mu.Lock()
	fs := tm.fs
	tm.fs = nil
	tm.mu.Unlock()
	for _, f := range fs {
		f()
	}
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
	tm := &timers{}
	e := readyBackfill(t, func(c *Config) { c.AfterFunc = tm.after })
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
	tm.fire() // WhatsApp never answers
	if got := e.drain(0); got.body["state"] != "done" {
		t.Fatalf("a request WhatsApp never answers must not hang the backfill: %s", got.raw)
	}
}

func (e *env) bufLen() int {
	e.s.mu.Lock()
	defer e.s.mu.Unlock()
	return len(e.s.bf.buf)
}

func TestBackfillBufferBoundHoldsForOversizedAnswersWithoutLoss(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillBuffer = 60 })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 80, 30*86400, "big") // the phone sends more than asked for
	if n := e.bufLen(); n != 60 {
		t.Fatalf("buffer must stop at its bound: %d", n)
	}
	got := e.drain(0)
	if got.body["dropped"] != 20.0 || got.body["state"] != "running" {
		t.Fatalf("dropped messages must be reported and the chat kept: %s", got.raw)
	}
	e.drain(60) // acknowledge: room again
	reqs := e.requests()
	if len(reqs) != 2 || reqs[1].Anchor.ID != "d1" {
		t.Fatalf("a truncated answer must be asked again from the same anchor: %+v", reqs)
	}
}

func TestBackfillIgnoresUnsolicitedHistory(t *testing.T) {
	e := readyBackfill(t)
	e.msg(stranger, "s1", 500, "other chat", false)
	e.answer(dana, 5, 30*86400, "early") // no backfill running yet
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{"chat": dana}))
	e.answer(stranger, 5, 30*86400, "other") // readable, but not the chat that was asked
	if n := e.bufLen(); n != 0 {
		t.Fatalf("unsolicited history buffered: %d", n)
	}
}

func TestBackfillAckWipesMessagesAndSkipsChatsThatLeftTheScope(t *testing.T) {
	e := readyBackfill(t)
	e.msg(stranger, "s1", 500, "other chat", false)
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	first := e.requests()[0].Chat
	e.answer(first, 3, 30*86400, "w")
	e.s.mu.Lock()
	old := e.s.bf.buf[:cap(e.s.bf.buf)]
	e.s.mu.Unlock()
	e.drain(3)
	for _, x := range old {
		if x.m.Text != "" {
			t.Fatal("acknowledged message text stays in memory")
		}
	}
	e.mustOK(e.do("POST", "/allow", e.admin, map[string]any{"list": "read", "op": "add", "chat": first}))
	e.mustOK(e.do("POST", "/read_scope", e.admin, map[string]any{"all_direct": false}))
	e.answer(first, 1, 40*86400, "x") // `first` is still readable (allow-list); the other chat is not
	e.answer(first, 0, 0, "end")
	for _, r := range e.requests() {
		if r.Chat != first {
			t.Fatalf("requested history of a chat that left the read scope: %+v", e.requests())
		}
	}
	if got := e.drain(0); got.body["state"] != "done" {
		t.Fatalf("state %s", got.raw)
	}
}

func TestBackfillConcurrentStartsRunOnce(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.AfterFunc = (&timers{}).after })
	codes := make(chan int, 8)
	for i := 0; i < 8; i++ {
		go func() { codes <- e.do("POST", "/history/backfill", e.admin, map[string]any{}).code }()
	}
	ok := 0
	for i := 0; i < 8; i++ {
		if <-codes == 200 {
			ok++
		}
	}
	if ok != 1 || len(e.requests()) != 1 {
		t.Fatalf("%d starts accepted, %d requests", ok, len(e.requests()))
	}
}

func TestBackfillBufferIsAlsoBoundedInBytes(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillBytes = 10000 })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	now := float64(e.clk.Now().Unix())
	for i := 0; i < 5; i++ { // five 4 KB messages: only two fit in 10 KB
		e.fake.Emit(engine.MessageEvent{Msg: msgstore.Message{ID: fmt.Sprintf("huge%d", i), Chat: dana,
			TS: now - 90*86400 - float64(i), Kind: "text", Text: strings.Repeat("x", 4000)},
			Chat: msgstore.ChatInfo{ID: dana}, History: true, OnDemand: true})
	}
	e.fake.Emit(engine.HistoryAnswerEvent{Chats: []string{dana}})
	if n := e.bufLen(); n != 2 {
		t.Fatalf("byte bound: %d messages buffered", n)
	}
	if n := len(e.requests()); n != 1 { // 8 KB of 10 KB used: the retry must wait for a drain, not burn a retry
		t.Fatalf("truncated answer asked again before Core drained: %d requests", n)
	}
	if got := e.drain(0); got.body["dropped"] != 3.0 {
		t.Fatalf("dropped %s", got.raw)
	}
	e.drain(2)
	if reqs := e.requests(); len(reqs) != 2 || reqs[1].Anchor.ID != "d1" {
		t.Fatalf("after the drain the truncated answer must be asked again: %+v", reqs)
	}
}

func TestBackfillAnswerWithinRetentionNeverReachesTheStore(t *testing.T) {
	e := readyBackfill(t)
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 2, 2*86400, "fresh") // inside the 14-day retention: still memory only
	if stored, _ := e.st.Messages(e.s.ctx, dana, 0, 0, 500); len(stored) != 1 {
		t.Fatalf("on-demand history must never reach the bridge store: %d", len(stored))
	}
	e.answer(stranger, 1, 2*86400, "uns") // unsolicited on-demand history is ignored entirely
	if chats, _ := e.st.AllChats(e.s.ctx); len(chats) != 1 {
		t.Fatalf("unsolicited on-demand history created a chat: %d", len(chats))
	}
}

func TestBackfillTinyBufferBoundDoesNotStall(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillBuffer = 10 })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	if n := len(e.requests()); n != 1 {
		t.Fatalf("a bound below one batch must still send the first request: %d", n)
	}
}

func TestBackfillTimeoutWhileOfflineFailsInsteadOfFinishingTheChat(t *testing.T) {
	tm := &timers{}
	e := readyBackfill(t, func(c *Config) { c.AfterFunc = tm.after })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.fake.Disconnect()
	tm.fire()
	if got := e.drain(0); got.body["state"] != "failed" || got.body["error"] != "WA_NOT_CONNECTED" || got.body["chats_done"] != 0.0 {
		t.Fatalf("an unanswered request while offline is not a finished chat: %s", got.raw)
	}
}

func TestBackfillDrainDropsChatsThatLeftTheScopeFromMemory(t *testing.T) {
	e := readyBackfill(t)
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 2, 30*86400, "z")
	e.mustOK(e.do("POST", "/read_scope", e.admin, map[string]any{"all_direct": false}))
	e.drain(0)
	if n := e.bufLen(); n != 0 {
		t.Fatalf("messages of a chat that left the read scope stay in memory: %d", n)
	}
}

func TestBackfillMessageLargerThanTheByteBoundDoesNotStall(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillBytes = 100 })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	for i := 0; i <= backfillRetries; i++ { // one message can never fit: an empty buffer must not wait for a drain
		if n := len(e.requests()); n != i+1 {
			t.Fatalf("answer %d: stalled after %d requests", i, n)
		}
		e.answer(dana, 1, 90*86400, strings.Repeat("y", 200))
	}
	if got := e.drain(0); got.body["state"] != "done" || len(e.requests()) != 1+backfillRetries ||
		got.body["lost"] != 1.0 || got.body["chats_incomplete"] != 1.0 {
		t.Fatalf("stalled on an empty buffer: %d requests, %s", len(e.requests()), got.raw)
	}
}

func TestBackfillRestartWipesTheEarlierBuffer(t *testing.T) {
	e := readyBackfill(t)
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 2, 30*86400, "w")
	e.answer(dana, 0, 0, "none")
	e.s.mu.Lock()
	old := e.s.bf.buf
	e.s.mu.Unlock()
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	for _, x := range old {
		if x.m.Text != "" {
			t.Fatal("a new start left the earlier buffered text in memory")
		}
	}
}

func TestBackfillRevokedQueuedChatIsNotRequestedWhenDrainResumes(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillBuffer = 60 })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 50, 30*86400, "p") // 50 of 60 used: the next request waits for a drain
	if n := len(e.requests()); n != 1 {
		t.Fatalf("paused buffer still requested: %d", n)
	}
	e.mustOK(e.do("POST", "/read_scope", e.admin, map[string]any{"all_direct": false})) // dana leaves the scope
	if got := e.drain(50); len(e.requests()) != 1 || got.body["state"] != "done" {
		t.Fatalf("a revoked chat was asked again after the drain: %d requests, %s", len(e.requests()), got.raw)
	}
}

func TestBackfillAcceptsOnlyMessagesOlderThanTheRequestAnchor(t *testing.T) {
	e := readyBackfill(t)
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 2, 0, "late") // newer than the anchor d1: not an answer to this request
	if n := e.bufLen(); n != 0 {
		t.Fatalf("a delayed or unrelated answer was buffered: %d", n)
	}
}

func TestBackfillRetryAfterTruncationNeitherDuplicatesNorLoses(t *testing.T) {
	e := readyBackfill(t, func(c *Config) { c.BackfillBuffer = 60 })
	e.mustOK(e.do("POST", "/history/backfill", e.admin, map[string]any{}))
	e.answer(dana, 80, 30*86400, "big") // 60 fit, 20 dropped
	e.drain(60)
	e.answer(dana, 80, 30*86400, "big") // the same answer again: only the 20 missing are new
	got := e.drain(60)
	msgs, _ := got.body["messages"].([]any)
	if len(msgs) != 20 || got.body["received"] != 80.0 || msgs[0].(map[string]any)["id"] != "big60" {
		t.Fatalf("retry must add exactly the missing messages: %d, %s", len(msgs), got.raw)
	}
	e.drain(80) // acknowledge: room for the next window
	if reqs := e.requests(); len(reqs) != 3 || reqs[2].Anchor.ID != "big79" {
		t.Fatalf("after the full answer the anchor moves on: %+v", reqs)
	}
}
