"""SW-V2 WP-C (Issue #17 items 4-6): backend event tracing, hard deadline + one retry + honest failure for stalled
GPT-Live lookups, background tasks (TaskManager / TaskSkill / ResearchRunner). Offline: fakes only, no network."""
import asyncio
import json
import logging
import time
from types import SimpleNamespace

import pytest

from conftest import until
from jarves import logs
from jarves.brain import BackendTrace
from jarves.budget import Budget
from jarves.config import AIConfig, Config
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill
from jarves.tasks import (LookupFailed, LookupResult, ResearchRunner, TaskManager, TaskSkill, announcement,
                          make_task_manager, read_history)
from jarves.voice import live as live_mod
from jarves.voice.live import STALL_FAIL_NOTE, LiveVoice
from openai_fakes import FakeResponses, FakeStream

WEB_STREAM = [
    {"type": "response.created", "response": {"id": "resp_1", "model": "gpt-6-luna"}},
    {"type": "response.output_item.added", "item": {"type": "web_search_call", "id": "ws_1"}},
    {"type": "response.web_search_call.in_progress", "item_id": "ws_1"},
    {"type": "response.web_search_call.searching", "item_id": "ws_1"},
    {"type": "response.web_search_call.completed", "item_id": "ws_1"},
    {"type": "response.output_item.done", "item": {"type": "web_search_call", "status": "completed", "action": {
        "type": "search", "query": "Intel stock news today", "sources": [{"type": "url", "url": "https://x"}]}}},
    {"type": "response.output_item.done", "item": {"type": "web_search_call", "action": {"type": "open_page",
                                                                                       "url": "https://x"}}},
    {"type": "response.reasoning_summary_text.done", "text": "The user wants Intel's price; search news."},
    {"type": "response.output_text.delta", "delta": "מניית אינטל "},
    {"type": "response.output_text.delta", "delta": "עלתה היום."},
    {"type": "response.output_text.done", "text": "מניית אינטל עלתה היום."},
    {"type": "response.completed", "response": {"usage": {"input_tokens": 120, "output_tokens": 30,
                                                          "output_tokens_details": {"reasoning_tokens": 8}}}},
]


def codes(caplog):
    return [getattr(r, "code", None) or r.getMessage() for r in caplog.records]


def test_backend_trace_logs_every_stage_and_maps_screen_activity(caplog):
    caplog.set_level(logging.DEBUG, logger="jarves")
    tr = BackendTrace("live", "LIVE_BACKEND", delegation="d1")
    shown = [s for s in (tr.feed(e) for e in WEB_STREAM) if s]
    assert shown[0] == ("thinking", "חושב…")
    assert ("searching", "מחפש ברשת…") in shown and ("searching", "מחפש: Intel stock news today") in shown
    assert ("reading", "קורא מקורות…") in shown and shown[-1] == ("thinking", "מנסח תשובה…")
    assert tr.queries == ["Intel stock news today"] and tr.sources == 1 and tr.pages == 1
    assert tr.usage == {"input_tokens": 120, "output_tokens": 30, "reasoning_tokens": 8}
    assert tr.reasoning and tr.text == "מניית אינטל עלתה היום." and tr.status == "completed"
    text = codes(caplog)
    done = next(r for r in caplog.records if getattr(r, "code", "") == "LIVE_BACKEND_DONE")
    assert done.fields["usage_in"] == 120 and done.fields["usage_reasoning"] == 8 and done.fields["searches"] == 1
    assert "backend_s" in done.fields
    from jarves import logs
    assert logs.scrub(done.fields)["usage_out"] == 30                 # not masked like a credential
    for code in ("LIVE_BACKEND_START", "LIVE_BACKEND_WEB_SEARCH", "LIVE_BACKEND_WEB_SEARCH_DONE",
                 "LIVE_BACKEND_READING", "LIVE_BACKEND_REASONING", "LIVE_BACKEND_WRITING", "LIVE_BACKEND_DONE"):
        assert code in text, code


def test_backend_trace_failure_and_incomplete_reasons():
    tr = BackendTrace("x", quiet=True)
    tr.feed({"type": "response.failed", "response": {"error": {"code": "server_error", "message": "boom"}}})
    assert tr.status == "failed" and tr.error == "server_error"
    tr2 = BackendTrace("x", quiet=True)
    tr2.feed({"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}})
    assert tr2.status == "incomplete" and tr2.error == "max_output_tokens"


# ------------------------------------------------------------------ live: deadline, retry, honest failure
class Sent:
    def __init__(self):
        self.items = []

    def appender(self, kind):
        async def append(content, delegation_id, event_id=None):
            self.items.append((kind, content, delegation_id, event_id))
        return SimpleNamespace(append=append)


def make_live(**kw):
    reg = SkillRegistry()
    reg.register(ClockSkill())
    acts = []

    async def on_activity(kind, text="", detail=""):
        acts.append((kind, text, detail))
    lv = LiveVoice(reg, lambda: "backend", on_activity=on_activity, **kw)
    sent = Sent()
    lv.conn = SimpleNamespace(session=SimpleNamespace(thinking=sent.appender("thinking"),
                                                      commentary=sent.appender("commentary")))
    lv.running = lv._active = True
    lv._backend_busy = False
    return lv, sent, acts


async def _cleanup(lv):
    lv.running = False
    for t in lv._tasks:
        t.cancel()
    await asyncio.gather(*lv._tasks, return_exceptions=True)


async def test_stalled_lookup_is_retried_once_and_the_answer_is_handed_to_live():
    asked = []

    async def retry(q):
        asked.append(q)
        return "מניית אינטל עלתה בשלושה אחוזים."
    lv, sent, acts = make_live(backend_deadline_s=0.2, retry_lookup=retry)
    lv._remember_user("אתה יכול לבדוק בחדשות מה המצב של מניית אינטל")
    await lv._backend_event({"type": "response.created"}, "d1")
    await until(lambda: [s for s in sent.items if s[0] == "commentary"])
    notes = [s for s in sent.items if s[0] == "commentary"]
    assert len(asked) == 1 and "אינטל" in asked[0]
    assert len(notes) == 1 and "שלושה אחוזים" in notes[0][1] and notes[0][3].startswith("jarves_stall")
    assert lv._stats["backend_stalls"] == 1 and lv._backend_busy is False
    assert ("searching", "מנסה שוב…", "") in [a[:3] for a in acts] or any(a[1] == "מנסה שוב…" for a in acts)
    await _cleanup(lv)


async def test_stalled_lookup_without_answer_says_the_honest_failure_once():
    async def retry(q):
        raise LookupFailed("timeout")
    lv, sent, _ = make_live(backend_deadline_s=0.2, retry_lookup=retry)
    lv._remember_user("מה המצב של מניית אינטל")
    await lv._backend_event({"type": "response.created"}, "d1")
    await until(lambda: [s for s in sent.items if s[0] == "commentary"])
    notes = [s[1] for s in sent.items if s[0] == "commentary"]
    assert notes == [STALL_FAIL_NOTE] and "לא הצלחתי להביא את זה, ננסה שוב?" in notes[0]
    await _cleanup(lv)


async def test_late_original_answer_cancels_the_retry_and_is_not_counted_twice():
    started = asyncio.Event()

    async def retry(q):
        started.set()
        await asyncio.sleep(5)
        return "x"
    lv, sent, _ = make_live(backend_deadline_s=0.2, retry_lookup=retry)
    lv._remember_user("שאלה")
    await lv._backend_event({"type": "response.created"}, "d1")
    await asyncio.wait_for(started.wait(), 30)
    await lv._backend_event({"type": "response.completed"}, "d1")      # the stalled response finally answered
    await asyncio.sleep(0.05)
    assert lv._retry_task.cancelled() and not [s for s in sent.items if s[0] == "commentary"]
    assert lv._stats["backend_responses"] == 1 and "d1" not in lv._abandoned
    await _cleanup(lv)


async def test_tool_run_is_not_a_stall():
    lv, sent, _ = make_live(backend_deadline_s=0.2, retry_lookup=None)
    await lv._backend_event({"type": "response.created"}, "d1")
    lv._tools_running = 1                                             # e.g. a slow music search or an approval
    await asyncio.sleep(0.6)
    assert not lv._stats.get("backend_stalls") and lv._backend_busy
    await _cleanup(lv)


async def test_screen_shows_elapsed_time_while_the_backend_works(monkeypatch):
    lv, _, acts = make_live(backend_deadline_s=40)
    await lv._backend_event({"type": "response.created"}, "d1")
    lv._busy_t0 = time.monotonic() - 7                                # 7 s into the lookup
    await lv._backend_event({"type": "response.output_item.done", "item": {"type": "web_search_call", "action": {
        "type": "search", "query": "intel"}}}, "d1")
    assert acts[-1] == ("searching", "מחפש: intel", "7 שנ׳")
    await _cleanup(lv)


async def test_progress_notes_never_over_the_users_speech(monkeypatch):
    monkeypatch.setattr(live_mod, "PROGRESS_SILENCE_S", 0.0)
    monkeypatch.setattr(live_mod, "PROGRESS_MIN_RESPONSE_S", 0.0)
    lv, sent, _ = make_live(progress_after_s=0.02)
    lv._in_text = "רגע, ועוד דבר"                                       # the user is talking meanwhile
    await lv._backend_event({"type": "response.created"}, "d1")
    await asyncio.sleep(0.3)
    assert not sent.items
    await _cleanup(lv)


def test_reasoning_summary_requested_only_when_the_backend_reasons():
    lv, _, _ = make_live(reasoning_effort="low", reasoning_summary="auto")
    assert lv.session_config()["delegation"]["responses"]["reasoning"] == {"effort": "low", "summary": "auto"}
    lv2, _, _ = make_live(reasoning_effort="none", reasoning_summary="auto")
    assert lv2.session_config()["delegation"]["responses"]["reasoning"] == {"effort": "none"}


async def test_announce_waits_until_the_user_is_quiet():
    lv, sent, _ = make_live()
    lv._last_user_voice = time.monotonic()                            # the user is speaking right now
    t = asyncio.create_task(lv.announce("[task] result", tag="abc"))
    await asyncio.sleep(0.5)
    assert not sent.items
    ok = await asyncio.wait_for(t, 30)
    assert ok and sent.items[-1][1] == "[task] result" and sent.items[-1][3] == "jarves_task_abc_1"
    await _cleanup(lv)


async def test_announcement_rejected_as_too_long_is_resent_shorter():
    """Real GPT-Live run 19 Oct: 'Context append text must not exceed 500 tokens' -> the result was never spoken."""
    lv, sent, _ = make_live()
    long_note = "[task] instructions first.\nResult: " + "א" * 1500
    t = asyncio.create_task(lv.announce(long_note, tag="t1"))
    while not sent.items:
        await asyncio.sleep(0.02)
    assert len(sent.items[0][1]) == live_mod.NOTE_MAX_CHARS
    await lv.handle_event({"type": "error", "error": {"code": "invalid_value", "client_event_id": sent.items[0][3],
                                                      "message": "Context append text must not exceed 500 tokens."}})
    assert await asyncio.wait_for(t, 30) is True
    assert len(sent.items) == 2 and sent.items[1][1].startswith("[task] instructions first.")
    assert len(sent.items[1][1]) == live_mod.NOTE_MAX_CHARS // 2
    await _cleanup(lv)


def test_announcement_fits_the_commentary_limit():
    from jarves.tasks import Task
    note = announcement(Task("a", "x" * 500, status="done", result="ב" * 3000))
    assert len(note) < live_mod.NOTE_MAX_CHARS and note.index("Result:") > note.index("Do not delegate")


async def test_announce_gives_up_when_the_session_ends():
    lv, sent, _ = make_live()
    lv._backend_busy = True
    t = asyncio.create_task(lv.announce("x"))
    await asyncio.sleep(0.1)
    lv.running = False
    assert await asyncio.wait_for(t, 30) is False and not sent.items


async def test_rejected_stall_or_task_note_does_not_switch_progress_off():
    lv, _, _ = make_live(progress_after_s=2.5)
    await lv.handle_event({"type": "error", "error": {"code": "x", "client_event_id": "jarves_task_abc"}})
    assert lv.progress_after_s == 2.5 and lv._stats["errors"] == 0


# ------------------------------------------------------------------ TaskManager
def fake_runner(delay=0.05, text="תוצאה", fail=None):
    calls = []

    async def run(description, on_progress=None, *, reservation=None):
        calls.append(description)
        if on_progress:
            on_progress("searching", f"מחפש: {description[:10]}")
        await asyncio.sleep(delay)
        if reservation is not None:
            reservation.commit()
        if fail:
            raise LookupFailed(fail)
        return LookupResult(text, 1, {"input_tokens": 5})
    run.calls = calls
    return run


async def test_tasks_run_in_background_with_bounded_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "_privacy", False)          # development mode: tasks.jsonl keeps the text
    updates, finished = [], []
    tm = TaskManager(fake_runner(0.2), store=tmp_path / "tasks.jsonl", max_running=2, max_queued=1,
                     on_update=lambda v: updates.append(v), on_finished=lambda t: finished.append(t))
    a, b, c = [await tm.start(f"מחקר {i}") for i in range(3)]
    await asyncio.sleep(0.05)
    assert [t.status for t in (a, b, c)] == ["running", "running", "queued"]
    with pytest.raises(Exception) as e:
        await tm.start("one too many")
    assert getattr(e.value, "code", "") == "TASK_LIMIT"
    await until(lambda: len(finished) == 3 and all(t.status == "done" for t in (a, b, c)))
    assert [t.status for t in (a, b, c)] == ["done", "done", "done"] and len(finished) == 3
    assert updates and {v["status"] for v in updates[-1]} == {"done"}
    hist = read_history(tmp_path / "tasks.jsonl")
    assert len(hist) == 3 and hist[0]["result"] == "תוצאה" and "duration_s" in hist[0]
    await tm.shutdown()


async def test_task_history_keeps_no_text_in_privacy_mode(tmp_path, monkeypatch):
    """SW-V4 B: with log.privacy on, tasks.jsonl keeps lengths and timings, never what was asked or found."""
    monkeypatch.setattr(logs, "_privacy", True)
    finished = []
    tm = TaskManager(fake_runner(0.0, text="דנה גרה ברחוב הרצל 12"), store=tmp_path / "tasks.jsonl",
                     on_finished=lambda t: finished.append(t))
    t = await tm.start("מה הכתובת של דנה כהן?")
    await until(lambda: finished)
    assert t.result == "דנה גרה ברחוב הרצל 12"                 # still announced in full, live
    raw = (tmp_path / "tasks.jsonl").read_text(encoding="utf-8")
    assert "דנה" not in raw and "הרצל" not in raw and read_history(tmp_path / "tasks.jsonl")[0]["result"] == "<21 chars>"
    await tm.shutdown()


async def test_task_cancel_and_timeout_and_failure_are_recorded(tmp_path):
    tm = TaskManager(fake_runner(5), store=tmp_path / "t.jsonl", timeout_s=10)
    t = await tm.start("ארוך")
    await asyncio.sleep(0.05)
    assert (await tm.cancel("")).status == "cancelled"
    tm2 = TaskManager(fake_runner(5), store=tmp_path / "t.jsonl", timeout_s=0.1)
    t2 = await tm2.start("איטי")
    await asyncio.sleep(0.3)
    assert t2.status == "timeout"
    tm3 = TaskManager(fake_runner(0.01, fail="timeout"), store=tmp_path / "t.jsonl")
    t3 = await tm3.start("נכשל")
    await asyncio.sleep(0.1)
    assert t3.status == "failed" and t3.error == "timeout" and "ננסה" not in announcement(t3)
    assert "try again" in announcement(t3)
    statuses = {r["id"]: r["status"] for r in read_history(tmp_path / "t.jsonl")}
    assert statuses == {t.id: "cancelled", t2.id: "timeout", t3.id: "failed"}
    assert tm3.unannounced() == [t3]
    tm3.mark_announced(t3)
    assert tm3.unannounced() == [] and read_history(tmp_path / "t.jsonl")[0]["announced"] is True


async def test_task_budget_is_reserved_at_start_and_refused_when_exhausted(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    budget = Budget(mem, AIConfig(daily_request_limit=1))
    tm = TaskManager(fake_runner(0.05), budget=budget)
    await tm.start("first")
    with pytest.raises(Exception) as e:
        await tm.start("second")                    # the first holds the only unit
    assert e.value.code == "AI_BUDGET"
    await asyncio.sleep(0.15)
    assert budget.used("ai_requests") == 1
    await tm.shutdown()
    budget.close()
    mem.close()


async def test_task_cancelled_while_queued_releases_its_reservation(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    budget = Budget(mem, AIConfig(daily_request_limit=5))
    tm = TaskManager(fake_runner(5), budget=budget, max_running=1)
    await tm.start("a")
    b = await tm.start("b")
    await asyncio.sleep(0.05)
    assert b.status == "queued"
    await tm.cancel(b.id)
    assert b.status == "cancelled" and budget.held("ai_requests") == 1
    await tm.shutdown()
    budget.close()
    mem.close()


async def test_task_skill_tools_through_the_registry_policy():
    tm = TaskManager(fake_runner(0.05))
    reg = SkillRegistry()
    reg.allowed_actions |= {"task_start", "task_cancel"}
    reg.register(TaskSkill(tm))
    r = await reg.call("task_start", json.dumps({"description": "השווה שלושה טלפונים"}))
    assert r.ok and r.data["task_id"]
    s = await reg.call("task_status", "{}")
    assert s.ok and s.data["tasks"][0]["status"] in ("queued", "running")
    await asyncio.sleep(0.15)
    s = await reg.call("task_status", json.dumps({"task_id": r.data["task_id"]}))
    assert s.data["tasks"][0]["status"] == "done" and s.data["tasks"][0]["result"] == "תוצאה"
    c = await reg.call("task_cancel", "{}")
    assert not c.ok and c.code == "TASK_NOT_ACTIVE"
    await tm.shutdown()


def test_task_start_is_denied_unless_preauthorized():
    reg = SkillRegistry()
    reg.register(TaskSkill(TaskManager(fake_runner())))
    r = asyncio.run(reg.call("task_start", json.dumps({"description": "abc def"})))
    assert not r.ok and r.code == "TOOL_FORBIDDEN"


# ------------------------------------------------------------------ ResearchRunner (fake OpenAI client)
async def test_research_runner_deadline_then_one_retry_with_progress_and_budget(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    budget = Budget(mem, AIConfig(daily_request_limit=10))
    hung = FakeStream(WEB_STREAM[:4], hang=True)
    resp = FakeResponses([hung, FakeStream(WEB_STREAM)])
    runner = ResearchRunner("gpt-6-luna", "inst", budget=budget, client=SimpleNamespace(responses=resp),
                            reasoning_effort="low", reasoning_summary="auto", attempt_s=0.2, retries=1)
    progress = []
    out = await runner("מה עם אינטל", lambda k, t: progress.append((k, t)))
    assert out.text == "מניית אינטל עלתה היום." and out.attempts == 2 and out.queries == ["Intel stock news today"]
    assert hung.closed and ("searching", "מנסה שוב…") in progress
    assert resp.bodies[0]["reasoning"] == {"effort": "low", "summary": "auto"} and resp.bodies[0]["store"] is False
    assert resp.bodies[0]["tools"] == [{"type": "web_search"}]
    assert budget.used("ai_requests") == 2 and budget.held("ai_requests") == 0
    budget.close()
    mem.close()


async def test_research_runner_reports_failure_after_the_retry():
    fail = [{"type": "response.created", "response": {}},
            {"type": "response.failed", "response": {"error": {"code": "server_error"}}}]
    runner = ResearchRunner("m", "i", client=SimpleNamespace(responses=FakeResponses([FakeStream(fail),
                                                                                       FakeStream(fail)])),
                            attempt_s=1, retries=1)
    with pytest.raises(LookupFailed) as e:
        await runner("q")
    assert e.value.code == "server_error"


def test_task_manager_wiring_follows_the_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real-000000")
    cfg = Config()
    tm = make_task_manager(cfg, object(), None, tmp_path)
    assert isinstance(tm.runner, ResearchRunner) and tm.max_running == 3
    plan = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"connection": "chatgpt"})})
    assert make_task_manager(plan, object(), None, tmp_path) is None             # no plan lookup available
    off = cfg.model_copy(update={"ai": cfg.ai.model_copy(update={"tasks_enabled": False})})
    assert make_task_manager(off, object(), None, tmp_path) is None
