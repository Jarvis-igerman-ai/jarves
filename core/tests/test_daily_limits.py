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
