"""No silent limits, part 2: caps that stop ONE thing (a spoken question, a background task, a scheduled job, a
conversation, the delegation ladder). Offline: fake core pieces, fake local speech, no mic, cloud or paid call."""
import asyncio
import json

import pytest

from conftest import FakeTTS, open_face
from jarves import limits, logs
from jarves.tasks import TaskManager
from test_limits import _core, until


@pytest.fixture(autouse=True)
def _no_sink():
    limits.set_sink(None)
    yield
    limits.set_sink(None)


def test_every_event_has_words_setting_and_action():
    for code, spec in limits.EVENTS.items():
        v = limits.event_view(code, "3 of 3", 3)
        assert v["code"] == code and v["key"] == spec["key"] and v["level"] == "event"
        assert "3 of 3" in v["text_en"] and "3 of 3" in v["text_he"]
        assert v["action_en"] and v["action_he"] and (spec["key"] in v["action_en"] or "jarvis budget" in v["action_en"])
        assert limits.event_spoken(v, "en") and limits.event_spoken(v, "he")
    assert "--task-timeout 360" in limits.event_view("TASK_TIMEOUT", "180 s", 180)["action_en"]


def test_report_logs_a_warning_and_calls_the_sink(monkeypatch):
    seen, recs = [], []
    monkeypatch.setattr(logs, "warn", lambda comp, code, msg, **f: recs.append((code, f)))
    limits.set_sink(seen.append)
    limits.report("TASK_LIMIT", "8 active", 3)
    assert recs and recs[0][0] == "LIMIT_EVENT" and recs[0][1]["limit_code"] == "TASK_LIMIT"
    assert seen and seen[0]["code"] == "TASK_LIMIT"
    limits.set_sink(lambda v: 1 / 0)                       # a broken sink never breaks the path that hit the limit
    limits.report("TASK_TIMEOUT", "5 s", 5)


async def test_task_limit_and_timeout_report(tmp_path):
    seen = []
    limits.set_sink(seen.append)

    async def runner(text, progress=None, *, reservation=None):
        await asyncio.sleep(5)

    tm = TaskManager(runner, store=tmp_path / "t.jsonl", max_running=1, max_queued=0, timeout_s=0.1)
    await tm.start("a")
    with pytest.raises(Exception):
        await tm.start("b")
    await asyncio.sleep(0.3)
    codes = [v["code"] for v in seen]
    assert "TASK_LIMIT" in codes and "TASK_TIMEOUT" in codes
    await tm.shutdown()


async def test_job_budget_cap_reports():
    from jarves.scheduler import BudgetExceeded, JobBudget
    seen = []
    limits.set_sink(seen.append)
    jb = JobBudget(None, 1)
    jb.reserve("ai_requests")
    with pytest.raises(BudgetExceeded):
        jb.reserve("ai_requests")
    assert [v["code"] for v in seen] == ["JOB_REQUEST_LIMIT"] and "1 of 1" in seen[0]["detail"]


def test_delegation_ladder_step_reports():
    from types import SimpleNamespace
    from jarves.voice.delegation_client import DelegationState
    seen = []
    limits.set_sink(seen.append)
    st = DelegationState(None, SimpleNamespace(delegation_mode="client", delegation_fallback="local"))
    st.fail("append_rejected", "x")
    assert [v["code"] for v in seen] == ["DELEGATION_FALLBACK"] and "text_only" in seen[0]["detail"]


async def test_core_shows_event_on_every_surface(home, cfg):
    cfg.voice.language = "en"
    c = _core(home, cfg, [])
    await c.start()
    try:
        f = await open_face(c)
        limits.report("DELEGATION_TURN_USD", "$0.0612 > $0.05 per question", 0.05)
        err = await f.wait("error", lambda p: p["code"] == "DELEGATION_TURN_USD")
        assert err["setting"] == "ai.delegation_turn_usd" and "--turn-usd" in err["action"]
        snap = c.snapshot()
        assert snap["limit_events"][0]["code"] == "DELEGATION_TURN_USD"
        assert limits.event_lines(snap["limit_events"])[0].startswith("LIMIT HIT: A spoken question was stopped")
        assert c.health.components["limit_events"].action
        assert not c._local_tts.spoken               # the live model says delegation caps; no second voice
    finally:
        await c.stop()


async def test_session_max_and_idle_are_spoken_locally(home, cfg):
    cfg.voice.language = "en"
    c = _core(home, cfg, [])
    await c.start()
    try:
        await c._voice_limit_notice("VOICE_IDLE_DISCONNECT", "30 s", 30)
        assert any("closed after silence" in t and "voice.idle_disconnect_s" in t for t in c._local_tts.spoken)
        await c._voice_limit_notice("VOICE_SESSION_MAX", "900 s", 900)
        assert any("maximum length" in t and "voice.session_max_s" in t for t in c._local_tts.spoken)
        assert {e["code"] for e in c.recent_limit_events()} == {"VOICE_IDLE_DISCONNECT", "VOICE_SESSION_MAX"}
    finally:
        await c.stop()


def test_selftest_and_log_reader_see_events(home, cfg):
    from datetime import datetime, timezone
    from jarves import selftest
    (home / "logs").mkdir(exist_ok=True)
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "level": "WARNING", "code": "LIMIT_EVENT",
           "limit_code": "TASK_TIMEOUT", "detail": "180 s"}
    (home / "logs" / "jarves.log").write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n", encoding="utf-8")
    evs = limits.events_from_log(home)
    assert evs[0]["code"] == "TASK_TIMEOUT" and evs[0]["count"] == 2
    status, detail, fix = selftest.check_limits(selftest.Ctx(home=home, cfg=cfg))
    assert status == selftest.WARN and "background task was stopped" in detail and "ai.task_timeout_s" in fix


def test_budget_set_task_caps(home, cfg, capsys):
    from types import SimpleNamespace
    from jarves import budget_cli
    a = SimpleNamespace(action="set", voice_minutes=None, requests=None, daily_usd=None, turn_usd=None, turn_requests=None,
                        searches=None, timeout=None, fallback=None, task_running=4, task_timeout=240.0, mode=None,
                        config=home / "config.toml")
    assert budget_cli.cmd_budget(a, cfg, home) == 0
    from jarves.config import load_config
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "task_max_running = 4" in text and "task_timeout_s = 240" in text
    v = budget_cli.status_view(cfg, home)
    assert "task_running" in v["caps"] and v["session_caps"]["session_max_s"]
