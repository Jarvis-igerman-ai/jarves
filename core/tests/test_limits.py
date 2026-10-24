"""No silent limits (owner incident 23 Oct): a used-up daily cap is announced everywhere. Offline: fake budget numbers,
fake live session, fake audio, fake local speech; no microphone, cloud or paid call."""
import asyncio

from conftest import FakeTTS, FakeSTT, open_face
from jarves import limits, selftest
from jarves.app import Core
from jarves.budget import BudgetExceeded
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio

MIN = 60


def test_view_levels_and_words():
    ok = limits.view("voice_seconds", 10 * MIN, 30 * MIN)
    warn = limits.view("voice_seconds", 25 * MIN, 30 * MIN)
    out = limits.view("voice_seconds", 30 * MIN, 30 * MIN)
    almost = limits.view("voice_seconds", 30 * MIN - 5, 30 * MIN)        # < 20 s left is useless: exhausted
    assert (ok["level"], warn["level"], out["level"], almost["level"]) == ("ok", "warn", "exhausted", "exhausted")
    assert "voice limit reached (30/30 min today)" in out["text_en"] or "limit reached (30/30 min today)" in out["text_en"]
    assert "ai.daily_realtime_minutes" in out["action_en"] and "ai.daily_realtime_minutes" in out["action_he"]
    assert "30" in limits.spoken(out, "en") and "ai.daily_realtime_minutes" in limits.spoken(out, "he")
    rq = limits.view("ai_requests", 160, 200)
    assert rq["level"] == "warn" and "160/200 requests" in rq["text_en"] and "ai.daily_request_limit" in rq["action_en"]
    assert limits.view("ai_requests", 200, 200)["level"] == "exhausted"
    assert limits.health_entry(out)[0] == "degraded" and limits.health_entry(warn)[0] == "ok" and limits.health_entry(warn)[2]
    lines = limits.status_lines([out, warn, ok])
    assert lines[0].startswith("LIMIT REACHED:") and lines[1].startswith("WARNING:") and len(lines) == 2


async def until(pred, t=5.0):
    for _ in range(int(t / 0.05)):
        if pred():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached")


def _core(home, cfg, started):
    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, **kw):
            self.on_state, self.on_end = on_state, kw.get("on_end")

        async def start(self):
            started.append(1)

        async def stop(self):
            pass

    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=FakeLive)
    c._local_tts = FakeTTS()
    return c


async def test_exhausted_voice_budget_opens_no_session_and_says_so(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg.voice.mode = "live"
    cfg.voice.language = "en"
    started = []
    c = _core(home, cfg, started)
    await c.start()
    try:
        c.budget.record("voice_seconds", 30 * MIN)
        f = await open_face(c)
        await f.send("talk.down")
        err = await f.wait("error", lambda p: p["code"] == "VOICE_DAILY_LIMIT")
        assert err["used"] == 30 and err["max"] == 30 and "ai.daily_realtime_minutes" in err["action"]
        assert not started and c.live is None                           # no cloud session, no reconnect loop
        await until(lambda: any("30 of 30 min" in t for t in c._local_tts.spoken))  # local notice, English by voice.language
        comp = c.health.components["voice_budget"]
        assert comp.status == "degraded" and "ai.daily_realtime_minutes" in comp.action
        snap = c.snapshot()
        assert snap["limit_lines"][0].startswith("LIMIT REACHED") and snap["limits"][0]["kind"] == "voice_seconds"
        from jarves.runtime.tray import status_line
        assert "voice limit reached (30/30 min today)" in status_line(
            {"core": {"state": "running"}, "limits": [v for v in snap["limits"] if v["level"] != "ok"]})
    finally:
        await c.stop()


async def test_80_percent_warning_and_hebrew_notice(home, cfg):
    cfg.voice.language = "he"
    c = _core(home, cfg, [])
    await c.start()
    try:
        c.budget.record("voice_seconds", 25 * MIN)
        c.limits_refresh()
        comp = c.health.components["voice_budget"]
        assert comp.status == "ok" and "25/30" in comp.detail and comp.action
        v = limits.view("voice_seconds", 30 * MIN, 30 * MIN)
        await c.announce_limit(v)
        assert any("המגבלה היומית" in t for t in c._local_tts.spoken)
    finally:
        await c.stop()


async def test_budget_refusal_is_announced_once_for_every_kind(home, cfg):
    c = _core(home, cfg, [])
    await c.start()
    try:
        f = await open_face(c)
        cfg.ai.daily_request_limit = 1
        c.budget.reserve("ai_requests").commit()
        try:
            c.budget.reserve("ai_requests")
            raise AssertionError("must refuse")
        except BudgetExceeded:
            pass
        err = await f.wait("error", lambda p: p["code"] == "AI_DAILY_LIMIT")
        assert err["max"] == 1 and "ai.daily_request_limit" in err["setting"]
        assert c.health.components["request_budget"].status == "degraded"
        await until(lambda: c._local_tts.spoken)
        n = len(c._local_tts.spoken)
        for _ in range(3):                                   # a retry loop cannot nag: debounced
            try:
                c.budget.reserve("ai_requests")
            except BudgetExceeded:
                pass
        await asyncio.sleep(0.2)
        assert len(c._local_tts.spoken) == n
    finally:
        await c.stop()


async def test_timeout_by_budget_is_not_resumed(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg.voice.mode = "live"
    cfg.voice.activation = "conversation"
    ends = []
    c = _core(home, cfg, [])

    async def spy():
        ends.append("resumed")
    c._resume_live = spy
    await c.start()
    try:
        c.budget.record("voice_seconds", 30 * MIN)
        f = await open_face(c)
        # drive the end callback the way LiveVoice does when its watchdog fires after the budget ran out
        captured = {}
        orig = c._live_factory

        def factory(core, **kw):
            captured.update(kw)
            return orig(core, **kw)
        c._live_factory = factory
        c.memory._x("UPDATE usage SET count = ? WHERE kind = 'voice_seconds'", (29 * MIN + 50,))
        await c._start_live()                                 # 10 s left: below the useful minimum -> no session
        assert not captured
        await f.wait("error", lambda p: p["code"] == "VOICE_DAILY_LIMIT")
        assert not ends
    finally:
        await c.stop()


def test_resume_limit_is_loud(home, cfg):
    c = _core(home, cfg, [])
    c.link = type("L", (), {"send_face": staticmethod(lambda *a, **k: asyncio.sleep(0))})()
    sent = []
    c._spawn = lambda coro: (sent.append(coro), coro.close())
    c._rollovers = []
    assert [c._rollover_allowed() for _ in range(4)] == [True, True, True, False]
    assert len(sent) == 1                                      # the 4th refusal produced the on-screen error


def test_selftest_reports_exhausted_limit(home, cfg):
    from jarves.budget import Budget
    from jarves.memory import MemoryStore
    m = MemoryStore(home / "memory.db")
    Budget(m, cfg.ai).record("voice_seconds", 30 * MIN)
    m.close()
    ctx = selftest.Ctx(home=home, cfg=cfg)
    status, detail, fix = selftest.check_limits(ctx)
    assert status == selftest.FAIL and "limit reached (30/30 min today)" in detail and "ai.daily_realtime_minutes" in fix
