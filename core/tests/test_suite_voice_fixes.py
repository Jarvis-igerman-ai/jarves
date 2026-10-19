"""Regressions for the bugs the silent pre-use voice suite (tools/suite_voice.py, 19 Oct 2026) found. Offline."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from conftest import FakeSTT, FakeTTS, drain_tools, open_face

from jarves.app import Core
from jarves.skills import SkillRegistry, Tool, ToolResult
from jarves.skills.builtin import ClockSkill
from jarves.skills.music import FakePlayer
from jarves.voice import live as live_mod
from jarves.voice.audio import SimAudio
from jarves.voice.live import LiveVoice


class Conn:
    """Records what the Core sends on the Live connection."""

    def __init__(self):
        self.sent = []
        outer = self

        def appender(kind):
            async def append(content, delegation_id, event_id=None):
                outer.sent.append((kind, content, event_id))
            return SimpleNamespace(append=append)

        async def item_create(item):
            outer.sent.append(("response.item.create", item, None))

        async def response_create():
            outer.sent.append(("response.create", None, None))
        self.session = SimpleNamespace(thinking=appender("thinking"), commentary=appender("commentary"))
        self.response = SimpleNamespace(item=SimpleNamespace(create=item_create), create=response_create)


def make(registry=None, **kw):
    reg = registry or SkillRegistry()
    if registry is None:
        reg.register(ClockSkill())
    lv = LiveVoice(reg, lambda: "backend", **kw)
    lv.conn = Conn()
    lv.running = True
    lv._active = True
    lv._backend_busy = False
    return lv


async def cancel_all(lv):
    for t in lv._tasks:
        t.cancel()
    await asyncio.gather(*lv._tasks, return_exceptions=True)


# ------------------------------------------------------------------ greeting vs. clock context
def test_greeting_message_carries_the_clock():
    lv = make(greeting="שלום!", clock_context=True)
    text = lv.session_config()["input"][0]["content"][0]["text"]
    assert "Greet now" in text and "[clock]" in text


async def test_clock_loop_sends_nothing_before_the_greeting_was_spoken():
    """A thinking.append right after session.started suppressed the greeting (startup failed after 15 s)."""
    lv = make(greeting="שלום!", clock_context=True)
    task = asyncio.create_task(lv._clock_loop())
    await asyncio.sleep(0.4)
    assert not lv.conn.sent                                  # no event before the greeting audio
    lv._first_audio.set()
    lv._speaking_until = time.monotonic() - 2
    await asyncio.sleep(0.5)
    assert not lv.conn.sent                                  # this minute's clock already went with the greeting
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_clock_loop_without_greeting_sends_at_once():
    lv = make(clock_context=True)
    task = asyncio.create_task(lv._clock_loop())
    await asyncio.sleep(0.1)
    assert [s[0] for s in lv.conn.sent] == ["thinking"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# ------------------------------------------------------------------ answer text reaches the face
async def test_quiet_flush_sends_the_answer_to_the_screen():
    got = []

    async def on_tr(role, text):
        got.append((role, text))
    lv = make(on_transcript=on_tr)
    await lv.handle_event({"type": "session.input_transcript.delta", "delta": "מה השעה"})
    await lv.handle_event({"type": "session.output_transcript.delta", "delta": "שבע וחצי"})
    lv._schedule_flush(0.05)
    await asyncio.sleep(0.2)
    assert ("assistant", "שבע וחצי") in got and ("user", "מה השעה") in got


# ------------------------------------------------------------------ tools never block the event loop
async def test_slow_tool_does_not_freeze_events_and_continuation_follows():
    gate = asyncio.Event()
    reg = SkillRegistry()

    class Slow:
        name = "slow"

        def tools(self):
            async def run(_):
                await gate.wait()
                return ToolResult(True, "done")
            return [Tool("slow_tool", "slow", run, permission="read")]
    reg.register(Slow())
    lv = make(reg)
    t0 = time.monotonic()
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "slow_tool", "call_id": "c1", "arguments": "{}"}}})
    assert time.monotonic() - t0 < 0.2 and lv._tools_running == 1      # handler returned at once
    await lv.handle_event({"type": "session.usage.updated", "usage": {"seconds": 3}})
    assert lv.usage_s == 3                                             # events keep flowing meanwhile
    gate.set()
    await drain_tools(lv)
    assert [s[0] for s in lv.conn.sent] == ["response.item.create", "response.create"]
    assert lv._tools_running == 0
    await cancel_all(lv)


async def test_tool_cancelled_before_sending_releases_the_reserved_continuation(tmp_path):
    from jarves.budget import Budget
    from jarves.config import AIConfig
    from jarves.memory import MemoryStore
    gate = asyncio.Event()
    reg = SkillRegistry()

    class Slow:
        name = "slow"

        def tools(self):
            async def run(_):
                await gate.wait()
                return ToolResult(True, "done")
            return [Tool("slow_tool", "slow", run, permission="read")]
    reg.register(Slow())
    b = Budget(MemoryStore(tmp_path / "m.db"), AIConfig(daily_request_limit=5))
    lv = make(reg, budget=b)
    await lv.handle_event({"type": "response.event", "delegation_id": "d1", "event": {
        "type": "response.output_item.done",
        "item": {"type": "function_call", "name": "slow_tool", "call_id": "c1", "arguments": "{}"}}})
    assert b.held("ai_requests") == 1
    await cancel_all(lv)                                    # session ended while the tool was still running
    assert b.held("ai_requests") == 0 and b.used("ai_requests") == 0 and not lv._pending


# ------------------------------------------------------------------ progress notes
async def test_no_progress_note_for_a_young_continuation(monkeypatch):
    """A note sent 50 ms before a fast continuation finished was spoken INSTEAD of the answer."""
    monkeypatch.setattr(live_mod, "PROGRESS_SILENCE_S", 0.0)
    lv = make(progress_after_s=0.05)
    await lv._backend_event({"type": "response.created"}, "d1")
    await asyncio.sleep(0.4)                                # < PROGRESS_MIN_RESPONSE_S (1.5 s)
    assert not [s for s in lv.conn.sent if s[0] == "commentary"]
    await cancel_all(lv)


async def test_no_progress_note_while_a_confirmation_is_pending(monkeypatch):
    monkeypatch.setattr(live_mod, "PROGRESS_SILENCE_S", 0.0)
    monkeypatch.setattr(live_mod, "PROGRESS_MIN_RESPONSE_S", 0.0)
    monkeypatch.setattr(live_mod, "CONFIRM_NOTE_AFTER_S", 0.05)
    lv = make(progress_after_s=0.05)
    await lv.confirm_pending("Delete memory #1?")
    await lv._backend_event({"type": "response.created"}, "d1")
    await asyncio.sleep(0.3)
    notes = [s for s in lv.conn.sent if s[0] == "commentary"]
    assert len(notes) == 1 and notes[0][2] == "jarves_progress_confirm" and "approv" in notes[0][1]
    lv.confirm_done()
    assert not lv._confirm_pending
    await cancel_all(lv)


async def test_a_quick_on_screen_answer_gets_no_spoken_hint(monkeypatch):
    """The hint sent at once was spoken INSTEAD of the result after a 0.4 s approval."""
    monkeypatch.setattr(live_mod, "CONFIRM_NOTE_AFTER_S", 0.2)
    lv = make()
    await lv.confirm_pending("Delete?")
    await asyncio.sleep(0.05)
    lv.confirm_done()
    await asyncio.sleep(0.3)
    assert not [s for s in lv.conn.sent if s[0] == "commentary"]
    await cancel_all(lv)


# ------------------------------------------------------------------ idle: room noise cannot keep a session open
async def test_room_noise_alone_cannot_keep_the_session_open(monkeypatch):
    """A false wake's session stayed open 3 min 16 s on hiss/coughs (mic energy counted as activity)."""
    monkeypatch.setattr(live_mod, "ENERGY_EXTEND_S", 0.3)
    ended = []
    lv = make(idle_disconnect_s=0.4)
    lv._speaking_until = 0.0
    lv._request_end = lambda reason: ended.append(reason)
    lv.note_activity()
    t0 = time.monotonic()

    async def noise():
        while not ended:
            lv._last_energy = time.monotonic()                # loud mic, never transcribed
            await asyncio.sleep(0.05)
    n = asyncio.create_task(noise())
    await asyncio.wait_for(lv._idle_watchdog(), 5)
    n.cancel()
    assert ended == ["idle"] and time.monotonic() - t0 < 2.5   # 0.3 s extension + 0.4 s idle (+1 s checks)


async def test_real_activity_still_keeps_the_session_open(monkeypatch):
    monkeypatch.setattr(live_mod, "ENERGY_EXTEND_S", 0.3)
    ended = []
    lv = make(idle_disconnect_s=0.6)
    lv._speaking_until = 0.0
    lv._request_end = lambda reason: ended.append(reason)
    lv.note_activity()
    task = asyncio.create_task(lv._idle_watchdog())
    for _ in range(8):                                        # the user keeps talking (transcribed)
        await lv.handle_event({"type": "session.input_transcript.delta", "delta": "עוד "})
        await asyncio.sleep(0.25)
    assert not ended
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await cancel_all(lv)


# ------------------------------------------------------------------ Core: one session; confirmation note
async def test_second_start_while_a_session_is_open_is_ignored(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    made = []

    class Live:
        running = True

        def __init__(self, core, **kw):
            made.append(self)

        async def start(self):
            pass

        async def stop(self):
            self.running = False
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=Live)
    await c.start()
    try:
        await c._start_live()
        await c._start_live()
        assert len(made) == 1 and c.live is made[0]
    finally:
        await c.stop()


async def test_failed_startup_stops_the_half_started_session(home, cfg, monkeypatch):
    """prepare_desktop failed (no greeting audio) but its session stayed open; the face then opened a second one."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg.voice.activation = "conversation"
    cfg.voice.greeting = True
    made = []

    class Live:
        running = True

        def __init__(self, core, **kw):
            self.stopped = False
            made.append(self)

        async def start(self):
            pass

        async def wait_ready(self):
            raise RuntimeError("live greeting audio was not received")

        def present(self):
            pass

        async def stop(self):
            self.stopped = True
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=Live)
    await c.start()
    try:
        with pytest.raises(RuntimeError):
            await c.prepare_desktop()
        assert made[0].stopped and c.live is None
    finally:
        await c.stop()


async def test_stop_releases_the_database_file(home, cfg):
    """After Core.stop() memory.db must not stay open (Windows: it could not be moved or deleted)."""
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False)
    await c.start()
    c.budget.reserve("ai_requests").commit()
    await c.stop()
    (home / "memory.db").rename(home / "memory.moved")       # PermissionError while a handle is open
    assert (home / "memory.moved").exists()


async def test_confirmation_tells_the_voice_session(core):
    calls = []

    class Live:
        async def confirm_pending(self, prompt):
            calls.append(("pending", prompt))

        def confirm_done(self):
            calls.append(("done",))

        async def stop(self):
            pass
    core.live = Live()
    face = await open_face(core)
    task = asyncio.create_task(core.ask_confirm("Delete?", timeout=2))
    req = await face.wait("confirm.request")
    await face.send("confirm.reply", {"id": req["id"], "approved": False})
    assert await task is False
    assert calls == [("pending", "Delete?"), ("done",)]
    core.live = None
    await face.ws.close()
