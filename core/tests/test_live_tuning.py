"""Conversation tuning (2026-10-19): backend speed settings, silent clock context, progress note, echo tail."""
import asyncio
import time
from datetime import datetime
from types import SimpleNamespace

from jarves.config import AIConfig, VoiceConfig
from jarves.skills import SkillRegistry
from jarves.skills.builtin import ClockSkill
from jarves.voice import live as live_mod
from jarves.voice.live import LiveVoice, clock_note


class Sent:
    def __init__(self, fail=False):
        self.items, self.fail = [], fail

    def appender(self, kind):
        outer = self

        async def append(content, delegation_id, event_id=None):
            if outer.fail:
                raise ConnectionError("down")
            outer.items.append((kind, content, delegation_id, event_id))
        return SimpleNamespace(append=append)


def make(fail=False, **kw):
    reg = SkillRegistry()
    reg.register(ClockSkill())
    health = []
    lv = LiveVoice(reg, lambda: "backend", on_health=lambda *a: health.append(a), **kw)
    sent = Sent(fail)
    lv.conn = SimpleNamespace(session=SimpleNamespace(thinking=sent.appender("thinking"),
                                                      commentary=sent.appender("commentary")))
    lv.running = True
    lv._active = True
    return lv, sent, health


def test_defaults_are_fast_and_bounded():
    ai, v = AIConfig(), VoiceConfig()
    assert ai.voice_reasoning_effort == "none" and ai.voice_service_tier == "priority"
    assert ai.voice_max_output_tokens >= 16
    assert v.clock_context is True and 0 < v.progress_after_s <= 5 and v.echo_tail_s == 0.4


def test_session_config_passes_backend_speed_settings():
    lv, _, _ = make(reasoning_effort="none", service_tier="priority", max_output_tokens=8)
    r = lv.session_config()["delegation"]["responses"]
    assert r["reasoning"] == {"effort": "none"} and r["service_tier"] == "priority"
    assert r["max_output_tokens"] == 16                     # the documented minimum
    assert r["parallel_tool_calls"] is False                 # one continuation per call (budget F1)
    plain = make()[0].session_config()["delegation"]["responses"]
    assert not {"reasoning", "service_tier", "max_output_tokens"} & plain.keys()


def test_front_instructions_answer_directly_and_use_the_clock():
    text = make()[0].session_config()["instructions"]
    assert "ANSWER DIRECTLY" in text and "[clock]" in text
    assert "the time" not in text.split("Delegate to the backend only for:")[1].split("\n")[0]


def test_clock_note_has_time_day_and_date():
    n = clock_note(datetime(2026, 10, 19, 14, 30))        # a Monday
    assert "14:30" in n and "שני" in n and "19.10.2026" in n and n.startswith("[clock]")


async def test_clock_loop_sends_silent_context_once_per_minute():
    lv, sent, _ = make(clock_context=True)
    task = asyncio.create_task(lv._clock_loop())
    await asyncio.sleep(0.05)
    lv.running = False
    task.cancel()
    assert len(sent.items) == 1
    kind, content, delegation_id, event_id = sent.items[0]
    assert kind == "thinking" and delegation_id is None and event_id.startswith("jarves_clock_")


async def test_clock_failure_never_raises():
    lv, sent, _ = make(fail=True, clock_context=True)
    await asyncio.wait_for(lv._clock_loop(), 1)            # returns quietly instead of crashing the session


async def test_rejected_clock_context_is_switched_off_without_degrading():
    lv, _, health = make(clock_context=True)
    await lv.handle_event({"type": "error", "error": {"code": "invalid_value", "message": "no",
                                                       "client_event_id": "jarves_clock_202610191430"}})
    assert lv.clock_context is False and health == [] and lv._stats["errors"] == 0


async def test_progress_note_once_when_lookup_is_slow_and_silent(monkeypatch):
    monkeypatch.setattr(live_mod, "PROGRESS_SILENCE_S", 0.0)
    lv, sent, _ = make(progress_after_s=0.05)
    await lv._backend_event({"type": "response.created"}, "d1")
    await asyncio.sleep(0.2)
    await lv._backend_event({"type": "response.created"}, "d1")      # continuation after a tool: no second note
    await asyncio.sleep(0.2)
    notes = [s for s in sent.items if s[0] == "commentary"]
    assert len(notes) == 1 and notes[0][2] is None and "Do not guess" in notes[0][1]
    for t in lv._tasks:
        t.cancel()


async def test_very_slow_lookup_gets_a_second_different_note(monkeypatch):
    monkeypatch.setattr(live_mod, "PROGRESS_SILENCE_S", 0.0)
    monkeypatch.setattr(live_mod, "PROGRESS_AGAIN_S", 0.1)
    lv, sent, _ = make(progress_after_s=0.02)
    await lv._backend_event({"type": "response.created"}, "d1")
    await asyncio.sleep(0.6)
    notes = [s for s in sent.items if s[0] == "commentary"]
    assert len(notes) == 2 and notes[0][1] != notes[1][1]          # never more than two per lookup
    assert notes[1][3] == "jarves_progress_2"
    for t in lv._tasks:
        t.cancel()


async def test_no_progress_note_when_answer_arrives_in_time():
    lv, sent, _ = make(progress_after_s=0.1)
    await lv._backend_event({"type": "response.created"}, "d1")
    await lv._backend_event({"type": "response.completed"}, "d1")
    await asyncio.sleep(0.25)
    assert not [s for s in sent.items if s[0] == "commentary"]
    for t in lv._tasks:
        t.cancel()


async def test_no_progress_note_over_speech(monkeypatch):
    lv, sent, _ = make(progress_after_s=0.05)
    lv._speaking_until = time.monotonic() + 5                  # JARVES is still saying the acknowledgement
    await lv._backend_event({"type": "response.created"}, "d1")
    await asyncio.sleep(0.2)
    assert not sent.items
    for t in lv._tasks:
        t.cancel()


def test_echo_tail_is_configurable():
    lv, _, _ = make(echo_tail_s=0.0)
    lv._speaking_until = time.monotonic() - 0.1
    assert lv._gated() is False
    lv.echo_tail_s = 0.4
    assert lv._gated() is True
