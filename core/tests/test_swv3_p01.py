"""SW-V3 P0.1: regressions reproduced from the retained 19 Oct conversations (docs/reviews/SWV3_P01_BUG_TABLE.md).
Every test names the bug-table row it reproduces. Offline only: fake registries, fake Live connection, no audio."""
from __future__ import annotations

import asyncio
import contextvars
import time
from types import SimpleNamespace

import pytest

from jarves import brain as brain_mod
from jarves import runtime as runtime_mod
from jarves.brain import Brain, BrainUnavailable, OFFLINE_HELP
from jarves.skills import ROUTE, Skill, SkillRegistry, Tool, ToolResult, bounded_ai
from jarves.skills.builtin import ClockSkill
from jarves.voice import live as live_mod
from jarves.voice.live import LiveVoice, answer_said, front_instructions


# ------------------------------------------------------------------ helpers
class Sent:
    def __init__(self):
        self.items = []

    def appender(self, kind):
        async def append(content, delegation_id, event_id=None):
            self.items.append((kind, content, delegation_id, event_id))
        return SimpleNamespace(append=append)


def make_live(reg=None, **kw):
    reg = reg or SkillRegistry()
    if "clock" not in reg.skills:
        reg.register(ClockSkill())
    lv = LiveVoice(reg, lambda: "backend", **kw)
    sent = Sent()
    lv.conn = SimpleNamespace(session=SimpleNamespace(thinking=sent.appender("thinking"),
                                                      commentary=sent.appender("commentary")))
    lv.running = lv._active = True
    lv._backend_busy = False
    return lv, sent


async def _cleanup(lv):
    lv.running = False
    for t in lv._tasks:
        t.cancel()
    await asyncio.gather(*lv._tasks, return_exceptions=True)


class MailAndPlayer(Skill):
    name = "fake"

    def tools(self):
        async def read_mail(_):
            return ToolResult(True, "mail text written by someone else")

        async def louder(_):
            return ToolResult(True, "louder")
        return [Tool("mail_read", "read mail", read_mail, permission="read", untrusted_output=True),
                Tool("louder", "volume up", louder, permission="action")]


def mail_registry():
    reg = SkillRegistry(allowed_actions={"louder"})
    reg.register(MailAndPlayer())
    now = [1000.0]
    reg.clock = lambda: now[0]
    return reg, now


# ------------------------------------------------------------------ B01: "shut down" opened the dashboard
class FakeCore:
    def __init__(self):
        self.stop_event = asyncio.Event()


async def test_b01_voice_quit_stops_the_core_after_the_goodbye(monkeypatch):
    monkeypatch.setattr(runtime_mod, "QUIT_DELAY_S", 0.05)
    rt = SimpleNamespace(core=FakeCore())
    display, app = runtime_mod.make_skill(rt)
    reg = SkillRegistry(allowed_actions={"quit_jarves", "hide_yourself", "show_yourself"})
    reg.register(display)
    reg.register(app)
    res = await reg.call("quit_jarves", {}, origin="model")
    assert res.ok and "נסגר" in res.message
    assert not rt.core.stop_event.is_set()                 # the goodbye is spoken first
    await asyncio.sleep(0.1)
    assert rt.core.stop_event.is_set()


def test_b01_quit_hide_and_dashboard_are_distinguished_for_both_models():
    rt = SimpleNamespace(core=FakeCore())
    display, app = runtime_mod.make_skill(rt)
    tools = {t.name: t for t in display.tools() + app.tools()}
    assert "quit_jarves" in tools["hide_yourself"].description
    assert "שט דאון" in tools["quit_jarves"].description and "dashboard" in tools["quit_jarves"].description
    assert app.name != display.name                        # quit is not hidden with the display feature
    front = front_instructions()
    assert "quitting the JARVES app" in front and "never the dashboard" in front
    assert "quit_jarves" in brain_mod.INSTRUCTIONS and "open_dashboard only opens" in brain_mod.INSTRUCTIONS


# ------------------------------------------------------------------ B02: completed backend answer never said
def test_b02_answer_said_tells_fillers_from_answers():
    answer = "מניית אינטל עלתה היום ב-3 אחוזים אחרי הדוחות."
    r1 = "עוד שנייה, כמעט שם.רגע אני בודק מה קורה עכשיו במניית אינטל."   # what the 17:15 run said
    assert answer_said(answer, r1) is False
    assert answer_said(answer, "") is False
    assert answer_said(answer, "אינטל עלתה בשלושה אחוזים אחרי הדוחות.") is True
    assert answer_said("הווליום 70", "הגברתי לשבעים אחוז.") is True        # rephrased: digits became words
    assert answer_said(answer, "רגע, בודק. עלתה ב-3 אחוזים.") is True


async def _completed_answer(lv, text, d="d1"):
    await lv._backend_event({"type": "response.created"}, d)
    await lv._backend_event({"type": "response.output_text.done", "text": text}, d)
    await lv._backend_event({"type": "response.completed", "response": {}}, d)


async def test_b02_answer_only_followed_by_fillers_is_handed_over_once(monkeypatch):
    monkeypatch.setattr(live_mod, "DELIVERY_WINDOW_S", 0.05)
    monkeypatch.setattr(live_mod, "ANNOUNCE_USER_QUIET_S", 0.0)
    lv, sent = make_live()
    await _completed_answer(lv, "מניית אינטל עלתה ב-3 אחוזים.")
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": "עוד שנייה, כמעט שם."})
    await asyncio.sleep(2.2)                     # the 1.5 s rejection wait of _send_note included
    notes = [s for s in sent.items if s[0] == "commentary"]
    assert len(notes) == 1 and "3 אחוזים" in notes[0][1] and "not been said" in notes[0][1]
    assert lv._stats["undelivered_answers"] == 1
    await _cleanup(lv)


async def test_b02_spoken_answer_or_a_new_user_turn_gets_no_rescue(monkeypatch):
    monkeypatch.setattr(live_mod, "DELIVERY_WINDOW_S", 0.05)
    lv, sent = make_live()
    await _completed_answer(lv, "מניית אינטל עלתה ב-3 אחוזים.")
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": "אינטל עלתה בשלושה אחוזים."})
    lv2, sent2 = make_live()
    await _completed_answer(lv2, "מניית אינטל עלתה ב-3 אחוזים.")
    await lv2.handle_event({"type": "session.input_transcript.delta", "delta": "עזוב, תגיד מה השעה"})
    await asyncio.sleep(0.4)
    assert not [s for s in sent.items + sent2.items if s[0] == "commentary"]
    await _cleanup(lv)
    await _cleanup(lv2)


# ------------------------------------------------------------------ B09: lead-in + 2 progress notes per lookup
async def test_b09_no_progress_note_right_after_a_spoken_lead_in(monkeypatch):
    monkeypatch.setattr(live_mod, "PROGRESS_MIN_RESPONSE_S", 0.0)
    lv, sent = make_live(progress_after_s=0.05)
    lv._last_speech_t = time.monotonic()            # "בודק שוב." was just said for this lookup
    await lv._backend_event({"type": "response.created"}, "d1")
    lv._backend_busy = True
    await asyncio.sleep(1.5)                         # old rule: a note after 0.8 s of silence
    assert not [s for s in sent.items if s[0] == "commentary"]
    await _cleanup(lv)


def test_b10_back_to_back_responses_get_a_space_in_the_caption():
    assert live_mod._join("שנייה.", "רשמתי לי") == " רשמתי לי"
    assert live_mod._join("עלה ב-3.", "5 אחוז") == "5 אחוז"            # a decimal number stays intact
    assert live_mod._join("שלום", " עולם") == " עולם"


# ------------------------------------------------------------------ B03: the human's approval time became AI_TIMEOUT
class ConfirmTool(Skill):
    name = "cal"

    def tools(self):
        async def create(_):
            return ToolResult(True, "created")
        return [Tool("cal_create", "create", create, permission="action", confirm="Create it?")]


def confirm_registry(answer=True, wait_s=0.3, blocking=False):
    reg = SkillRegistry(allowed_actions={"cal_create"})
    reg.register(ConfirmTool())

    async def confirm(prompt):
        if blocking:
            time.sleep(wait_s)                       # the terminal session's input() blocks the loop
        else:
            await asyncio.sleep(wait_s)
        return answer
    reg.confirm = confirm
    return reg


@pytest.mark.parametrize("blocking", [False, True])
async def test_b03_approval_time_never_counts_as_an_ai_timeout(blocking):
    reg = confirm_registry(wait_s=0.4, blocking=blocking)

    async def turn():
        await asyncio.sleep(0.05)                    # model call
        res = await reg.call("cal_create", {})
        await asyncio.sleep(0.05)                    # the model words the result
        return res.message
    assert await bounded_ai(turn(), 0.2, reg) == "created"
    assert reg.confirm_wait_s >= 0.35


async def test_b03_a_slow_model_still_times_out():
    reg = confirm_registry()
    with pytest.raises(asyncio.TimeoutError):
        await bounded_ai(asyncio.sleep(1), 0.1, reg)


async def test_b03_timeout_reply_does_not_claim_there_is_no_ai():
    class Slow:
        async def reply(self, text):
            raise BrainUnavailable("AI_TIMEOUT", "the AI did not answer in time", "try again")
    r = await Brain(SkillRegistry(), Slow()).respond("תקבע לי פגישה מחר בעשר")
    assert r.ok is False and r.code == "AI_TIMEOUT"
    assert r.text != OFFLINE_HELP and "בלי בינה מלאכותית" not in r.text and "לא קיבלתי תשובה בזמן" in r.text


async def test_b03_terminal_confirmation_accepts_hebrew_yes():
    from jarves import text_session
    assert {"yes", "y", "כן"} <= set(text_session.CONFIRM_WORDS)


# ------------------------------------------------------------------ B04: untrusted-content taint timing and scope
async def test_b04_taint_is_per_route_so_a_mail_read_in_voice_never_blocks_the_scheduler():
    reg, now = mail_registry()
    ctx = contextvars.copy_context()

    async def in_voice(name):
        ROUTE.set("voice")
        return await reg.call(name, {})
    assert (await asyncio.create_task(in_voice("mail_read"), context=ctx)).ok
    assert (await asyncio.create_task(in_voice("louder"), context=ctx)).code == "TOOL_UNTRUSTED_TURN"
    assert (await reg.call("louder", {})).ok                 # default route (scheduler): not tainted


async def test_b04_first_user_transcript_delta_clears_the_taint_before_the_tool_call():
    """Live event order: mail read -> the user starts "louder" -> delegation -> tool call, all before the 2 s
    transcript flush. The action must run (the user really asked)."""
    reg, now = mail_registry()
    lv, _ = make_live(reg)

    async def voice_call(name):
        ROUTE.set(live_mod.VOICE_ROUTE)
        return await reg.call(name, {})
    assert (await asyncio.create_task(voice_call("mail_read"))).ok
    now[0] += 5                                                # the answer about the mail was spoken meanwhile
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "תגביר"})
    res = await asyncio.create_task(voice_call("louder"))
    assert res.ok, res.code
    await _cleanup(lv)


async def test_b04_transcript_of_the_same_turn_keeps_the_taint():
    reg, now = mail_registry()
    lv, _ = make_live(reg)

    async def voice_call(name):
        ROUTE.set(live_mod.VOICE_ROUTE)
        return await reg.call(name, {})
    await asyncio.create_task(voice_call("mail_read"))
    now[0] += 0.5                                              # within the grace: the triggering utterance itself
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "תקרא לי את המייל"})
    assert (await asyncio.create_task(voice_call("louder"))).code == "TOOL_UNTRUSTED_TURN"
    await _cleanup(lv)


# ------------------------------------------------------------------ B05: failed AI review recorded as "reviewed"
def test_b05_failed_review_is_not_marked_reviewed_and_retries_are_bounded(home):
    from test_wph_costs import _Client, _voice_session
    from jarves import diagnose
    from jarves.budget import Budget
    from jarves.config import Config
    from jarves.memory import MemoryStore
    from jarves.store import Store
    cfg = Config()
    st = Store(home / "store.db", backend_model="gpt-6-luna")
    sid = _voice_session(st, 1_760_000_000.0)
    mem = MemoryStore(home / "memory.db")
    budget = Budget(mem, cfg.ai)

    class Down:
        class responses:
            @staticmethod
            def create(**kw):
                raise ConnectionError("network down")
    assert diagnose.llm_review(st, cfg, sid, client=Down(), budget=budget) == []
    assert not st.meta(f"llm_reviewed:{sid}")                          # the old code said "already reviewed"
    assert diagnose.review_skip_reason(st, cfg, sid, have_client=True) is None   # one retry allowed
    cl = _Client()
    assert len(diagnose.llm_review(st, cfg, sid, client=cl, budget=budget)) == 1 and st.meta(f"llm_reviewed:{sid}")
    sid2 = _voice_session(st, 1_760_100_000.0)
    for _ in range(3):
        diagnose.llm_review(st, cfg, sid2, client=Down(), budget=budget)
    assert "failed 2 times" in diagnose.review_skip_reason(st, cfg, sid2, have_client=True)
    budget.close()
    mem.close()
    st.close()


# ------------------------------------------------------------------ B06: an older missing conversation never imported
def test_b06_import_backfills_a_missing_older_conversation_exactly_once():
    from pathlib import Path
    from jarves.store import Store
    fixture = Path(__file__).parent / "fixtures" / "session_2026-10-19.jsonl"
    st = Store(":memory:", tier="priority", backend_model="gpt-6-luna")
    st.ingest({"ts": "2026-10-19T19:33:05+00:00", "code": "LIVE_STARTED", "session": "newer001",
               "msg": "GPT-Live session started (gpt-live-1 -> gpt-6-luna)"})
    st.ingest({"ts": "2026-10-19T19:33:10+00:00", "code": "LIVE_USER_SAID", "session": "newer001", "text": "שלום"})
    st.ingest({"ts": "2026-10-19T19:36:26+00:00", "code": "VOICE_ENDED", "session": "newer001", "voice_seconds": 200})
    skip = st.newest_ts()
    assert st.ingest_log(fixture, skip) == []                          # the old behaviour: skipped forever
    ended = st.ingest_log(fixture, skip, backfill=True)
    assert len(ended) == 1 and len(st.sessions()) == 2
    turns = st.x("SELECT COUNT(*) FROM turns").fetchone()[0]
    assert st.ingest_log(fixture, st.newest_ts(), backfill=True) == []   # idempotent
    assert st.x("SELECT COUNT(*) FROM turns").fetchone()[0] == turns and len(st.sessions()) == 2
    st.close()


# ------------------------------------------------------------------ B07: a requested close logged as a fault
async def test_b07_requested_face_close_is_info_unexpected_loss_stays_a_warning(core, caplog):
    from conftest import open_face
    f = await open_face(core)
    await core.link.send_face("display.close", {"reason": "hide"})       # what runtime.hide / stop send
    await f.ws.close()
    for _ in range(50):
        if core.health.components["face_link"].status == "unavailable":
            break
        await asyncio.sleep(0.05)
    assert core.health.components["face_link"].detail == "display window closed"
    core.link._close_expected_until = 0.0
    f2 = await open_face(core)
    await f2.ws.close()                                                  # nobody asked: a real fault
    for _ in range(50):
        if core.health.components["face_link"].detail == "Godot face disconnected":
            break
        await asyncio.sleep(0.05)
    assert core.health.components["face_link"].detail == "Godot face disconnected"


# ------------------------------------------------------------------ B08: "close the session" had no tool
async def test_b08_end_conversation_stops_only_the_voice_session(monkeypatch):
    monkeypatch.setattr(runtime_mod, "QUIT_DELAY_S", 0.04)
    stopped = []

    class Live:
        _tools_running = 0
        _speaking_until = 0.0

        async def stop(self, reason="user"):
            stopped.append(reason)
    core = FakeCore()
    core.live = Live()
    rt = SimpleNamespace(core=core)
    _, app = runtime_mod.make_skill(rt)
    reg = SkillRegistry(allowed_actions={"end_conversation", "quit_jarves"})
    reg.register(app)
    res = await reg.call("end_conversation", {})
    assert res.ok and "היי ג'ארוויס" in res.message
    await asyncio.sleep(0.2)
    assert stopped == ["user"] and core.live is None and not core.stop_event.is_set()   # the Core keeps running
    assert (await reg.call("end_conversation", {})).code == "NO_CONVERSATION"
    assert "closing the window only hides" in brain_mod.INSTRUCTIONS


# ------------------------------------------------------------------ B11: a stopped process's session absorbed later records
def test_b11_session_of_a_stopped_core_is_not_stretched_by_other_processes():
    from jarves.store import Store
    st = Store(":memory:", tier="priority", backend_model="gpt-6-luna")
    t0 = 1_792_350_193.0
    rec = lambda dt, code, proc, **f: {"ts": t0 + dt, "code": code, "session": proc, "msg": f.pop("msg", code), **f}
    for d in (rec(0, "LIVE_STARTED", "procA", msg="GPT-Live session started (gpt-live-1 -> gpt-6-luna)"),
              rec(0.1, "CORE_STOPPED", "procA"),
              rec(600, "TOOL_RESULT", "cli1", tool="music_play", ok=True, msg="music_play -> ok"),
              rec(2700, "LIVE_STARTED", "probe", msg="GPT-Live session started (gpt-live-1 -> gpt-6-luna)")):
        st.ingest(d)
    a = next(s for s in st.sessions() if s["core_session"] == "procA")
    assert a["ended"] - a["started"] < 1 and a["end_reason"] == "core_stopped"     # was ~45 min (false $2.27)
    st.close()
