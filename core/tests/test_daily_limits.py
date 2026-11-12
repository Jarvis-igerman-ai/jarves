"""Daily voice and request caps are enforced and reset at local midnight (JAR-219, R-086).

Hermetic and clock-free: a real Budget on a temp memory.db, the calendar day replaced by a fake `date`.
No Core, network, microphone or paid call."""
import datetime as _dt

import pytest

from jarves import budget as budget_mod, limits, memory as memory_mod
from jarves.budget import Budget, BudgetExceeded
from jarves.memory import MemoryStore

MIN = 60


class _Day:
    """Stand-in for datetime.date whose today() is set by the test."""
    value = _dt.date(2026, 11, 10)

    @classmethod
    def today(cls):
        return cls.value


@pytest.fixture
def day(monkeypatch):
    _Day.value = _dt.date(2026, 11, 10)
    monkeypatch.setattr(budget_mod, "date", _Day)
    monkeypatch.setattr(memory_mod, "date", _Day)
    return _Day


@pytest.fixture
def budget(home, cfg, day):
    cfg.ai.daily_realtime_minutes = 30
    cfg.ai.daily_request_limit = 3
    m = MemoryStore(home / "memory.db")
    b = Budget(m, cfg.ai, clock=lambda: 1_000.0, alive=lambda pid: True)
    yield b
    b.close()
    m.close()


def test_voice_cap_refuses_after_daily_minutes(budget):
    assert budget.limit("voice_seconds") == 30 * MIN
    budget.record("voice_seconds", 29 * MIN)
    assert not budget.exhausted("voice_seconds") and budget.remaining("voice_seconds") == MIN
    budget.record("voice_seconds", MIN)
    assert budget.exhausted("voice_seconds") and budget.remaining("voice_seconds") == 0
    with pytest.raises(BudgetExceeded) as e:
        budget.reserve("voice_seconds", 1)
    assert e.value.kind == "voice_seconds" and e.value.limit == 30 * MIN and "daily_realtime_minutes" in e.value.action
    v = limits.view("voice_seconds", budget.used("voice_seconds"), budget.limit("voice_seconds"))
    assert v["level"] == "exhausted" and v["code"] == "VOICE_DAILY_LIMIT" and "30/30 min" in v["text_en"]


def test_request_cap_refuses_the_request_over_the_limit(budget):
    seen = []
    budget.on_exceeded = seen.append
    for _ in range(3):
        budget.reserve("ai_requests").commit()
    assert budget.used("ai_requests") == 3 and budget.remaining("ai_requests") == 0
    with pytest.raises(BudgetExceeded) as e:
        budget.reserve("ai_requests")
    assert e.value.limit == 3 and e.value.used == 3 and "daily_request_limit" in e.value.action
    assert [x.kind for x in seen] == ["ai_requests"]          # the owner is told, not a silent refusal
    assert budget.used("ai_requests") == 3                     # a refused request is not counted
    budget.ai.daily_request_limit = 4                          # a held (uncommitted) reservation also counts
    r = budget.reserve("ai_requests")
    with pytest.raises(BudgetExceeded):
        budget.reserve("ai_requests")
    r.release()
    v = limits.view("ai_requests", budget.used("ai_requests"), 3)
    assert v["level"] == "exhausted" and v["code"] == "AI_DAILY_LIMIT" and "3/3 requests" in v["text_en"]


def test_both_caps_reset_on_the_next_local_day(budget, day):
    budget.record("voice_seconds", 30 * MIN)
    for _ in range(3):
        budget.reserve("ai_requests").commit()
    assert budget.exhausted("voice_seconds") and budget.exhausted("ai_requests")
    day.value = _dt.date(2026, 11, 11)                         # local midnight passed
    assert budget.used("voice_seconds") == 0 and budget.used("ai_requests") == 0
    assert budget.remaining("voice_seconds") == 30 * MIN and budget.remaining("ai_requests") == 3
    budget.reserve("ai_requests").commit()
    assert budget.used("ai_requests") == 1
    views = limits.views_from(budget.used, budget.limit)
    assert {v["kind"]: v["level"] for v in views}["voice_seconds"] == "ok"
    day.value = _dt.date(2026, 11, 10)                         # yesterday's usage was kept, not wiped
    assert budget.used("voice_seconds") == 30 * MIN and budget.used("ai_requests") == 3



def test_owner_sets_voice_limit_of_one_and_second_voice_request_is_refused(home, day):
    """JAR-262: the owner sets a daily voice limit of 1 minute through `jarvis budget set`; the first voice request
    may start, the second gets a clear limit-reached refusal (which limit, used/max, how to raise it)."""
    from jarves import cli, daily_limits
    from jarves.config import load_config
    assert cli.main(["budget", "set", "--voice-minutes", "1", "--requests", "1"]) == 0
    cfg2, _ = load_config(home / "config.toml")
    m = MemoryStore(home / "memory.db")
    b = Budget(m, cfg2.ai, clock=lambda: 1_000.0, alive=lambda pid: True)
    try:
        assert b.limit("voice_seconds") == MIN and b.limit("ai_requests") == 1
        assert daily_limits.admit_voice(b).allowed
        b.record("voice_seconds", MIN)                          # the first conversation used the minute
        second = daily_limits.admit_voice(b)
        v = second.view
        assert not second.allowed and v["code"] == "VOICE_DAILY_LIMIT" and v["level"] == "exhausted"
        assert "voice limit reached (1/1 min today)" in v["text_en"] and "daily_realtime_minutes" in v["action_en"]
        assert v["text_he"] and "The daily voice limit is used up" in limits.spoken(v, "en")
    finally:
        b.close()
        m.close()


def test_voice_gate_keeps_the_old_min_useful_boundary(budget):
    """Same test as the gate it replaced in Core._start_live (`_voice_budget_left_s() < min_useful` refuses, and
    _voice_budget_left_s() is budget.remaining("voice_seconds")): 20 s left starts, 19 s does not."""
    from jarves import daily_limits
    useful = int(limits.SPECS["voice_seconds"]["min_useful"])
    budget.record("voice_seconds", 30 * MIN - useful)
    assert budget.remaining("voice_seconds") == useful and daily_limits.admit_voice(budget).allowed
    budget.record("voice_seconds", 1)
    assert not daily_limits.admit_voice(budget).allowed


def _core(home, cfg, monkeypatch, **kw):
    from conftest import FakeSTT, FakeTTS
    from jarves.app import Core
    from jarves.skills.music import FakePlayer
    from jarves.voice.audio import SimAudio
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    return Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
                use_real_audio=False, **kw)


async def test_core_voice_start_goes_through_the_daily_limits_gate(home, cfg, monkeypatch):
    """With a 1-minute voice limit used up, the Core's voice start asks daily_limits.admit_voice, opens no cloud
    session and shows the VOICE_DAILY_LIMIT refusal (removing the gate from _start_live fails this)."""
    from conftest import open_face
    from jarves import daily_limits
    cfg.voice.mode, cfg.voice.activation = "live", "conversation"
    cfg.ai.daily_realtime_minutes = 1
    calls, started = [], []
    real = daily_limits.admit_voice
    monkeypatch.setattr(daily_limits, "admit_voice", lambda b: calls.append(1) or real(b))

    class FakeLive:
        def __init__(self, core, **kw):
            started.append(1)
    c = _core(home, cfg, monkeypatch, live_factory=FakeLive)
    await c.start()
    try:
        c.budget.record("voice_seconds", MIN)                  # the first conversation used the minute
        f = await open_face(c)
        started.clear(), calls.clear()
        await c._start_live()                                  # the second voice request
        assert calls and not started
        err = await f.wait("error", lambda p: p["code"] == "VOICE_DAILY_LIMIT")
        assert err["max"] == 1 and "daily_realtime_minutes" in err["setting"]
    finally:
        await c.stop()


async def test_core_request_at_the_daily_request_limit_sends_nothing_and_says_so(home, cfg, monkeypatch):
    """The request side through the real Core path (handle_utterance -> Brain -> AgentBrain -> BudgetedModel):
    at a used-up request limit of 1 no model call is sent and the owner sees the limit-reached refusal."""
    from agents.testing.model import ScriptedModel, assistant_message
    from conftest import open_face
    cfg.ai.daily_request_limit = 1
    c = _core(home, cfg, monkeypatch)
    await c.start()
    try:
        assert c.agent is not None
        model = ScriptedModel([[assistant_message("should not be sent")]])
        c.agent._inner_model, c.agent._agent = model, None     # rebuilt around the fake model on the next request
        c.budget.reserve("ai_requests").commit()               # the first request used the limit
        f = await open_face(c)
        await c.handle_utterance("what is the capital of France", typed=True)
        said = await f.wait("assistant.text", lambda p: p.get("code") == "AI_BUDGET")
        assert "ai.daily_request_limit" in said["text"] and not model.calls and c.budget.used("ai_requests") == 1
        err = await f.wait("error", lambda p: p["code"] == "AI_DAILY_LIMIT")
        assert err["max"] == 1 and "ai.daily_request_limit" in err["setting"]
    finally:
        await c.stop()
