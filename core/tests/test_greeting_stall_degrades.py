"""#179: a missing greeting is an OPEN SILENT ROOM, never a crash or a half-open session. A fake live server sends
session.started and then no greeting audio (and variants). Real LiveVoice + real Core.prepare_desktop, no network."""
import asyncio
import base64
import time

import pytest

import jarves.voice.live as live_mod
from conftest import FakeSTT, FakeTTS
from jarves import logs
from jarves.app import Core
from jarves.skills import SkillRegistry
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio
from jarves.voice.live import LiveVoice
from test_readiness import _loud
from test_voice_faults import FakeClient

pytestmark = pytest.mark.asyncio


class _Note:
    def __init__(self, conn, answer_on=None, late_s=0.0, drop=False):
        self.conn, self.answer_on, self.late_s, self.drop = conn, answer_on, late_s, drop

    async def append(self, **kw):
        if self.drop:
            self.conn.push({"type": "session.closed", "reason": "connection_lost"})
        elif kw.get("event_id") == self.answer_on:
            asyncio.get_running_loop().call_later(self.late_s, self.conn.push, {
                "type": "session.output_audio.delta", "delta": base64.b64encode(_loud(4800)).decode()})


def _factory(holder, behaviour, said):
    """live_factory for Core: a REAL LiveVoice on the fake server; `behaviour(conn)` wires the server's reaction."""
    def make(core, **kw):
        async def on_transcript(role, text):
            said.append((role, text))
        kw = {k: v for k, v in kw.items() if k in ("greeting", "hold_output", "on_end", "on_state", "on_health",
                                                   "on_usage", "budget")}
        voice = LiveVoice(SkillRegistry(), lambda: "backend", client=FakeClient(), on_transcript=on_transcript, **kw)
        orig = voice.start

        async def start():
            await orig()
            behaviour(voice.conn, voice)
        voice.start = start
        holder.append(voice)
        return voice
    return make


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(live_mod, "GREETING_NUDGE_S", 0.05)
    monkeypatch.setattr(live_mod, "GREETING_RETRY_AFTER_S", 0.05)
    monkeypatch.setattr(live_mod, "GREETING_BUDGET_S", 0.4)
    monkeypatch.setattr(LiveVoice.wait_ready, "__defaults__", (0.4,))     # default arg was bound at import time
    logged = []
    for lvl in ("info", "warn"):
        orig = getattr(logs, lvl)
        monkeypatch.setattr(logs, lvl, lambda comp, code, *a, _o=orig, _l=lvl, **k: (logged.append((_l, code)),
                                                                                    _o(comp, code, *a, **k))[1])
    return logged


async def _core(home, cfg, monkeypatch, factory):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    cfg.voice.activation = "conversation"
    cfg.voice.greeting = True
    core = Core(cfg, home, audio=SimAudio(), stt=FakeSTT(), tts=FakeTTS(), player=FakePlayer(),
                live_factory=factory, console_logs=False, use_real_audio=False)
    await core.start()
    return core


async def _user_turn_works(voice, said):
    voice.conn.push({"type": "session.input_transcript.delta", "delta": "מה השעה"})
    voice.conn.push({"type": "session.output_transcript.delta", "delta": "שלוש"})
    for _ in range(100):
        if ("user", "מה השעה") in said:
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.parametrize("variant", ["silent", "nudge_ignored", "late_after_budget"])
async def test_no_greeting_audio_leaves_an_open_usable_silent_room(home, cfg, monkeypatch, fast, variant):
    holder, said = [], []

    def behaviour(conn, voice):
        if variant == "late_after_budget":      # answers the nudge, but only after the whole budget has passed
            conn.session.thinking = _Note(conn, "jarves_greeting_nudge", late_s=2.0)
            conn.session.commentary = _Note(conn, "jarves_greeting_nudge", late_s=2.0)
        else:                                   # silent / nudge ignored: notes are accepted but nothing comes back
            conn.session.thinking = _Note(conn)
            conn.session.commentary = _Note(conn)

    core = await _core(home, cfg, monkeypatch, _factory(holder, behaviour, said))
    try:
        t0 = time.monotonic()
        await core.prepare_desktop()                                   # no exception escapes
        assert time.monotonic() - t0 < 10.0                            # never hangs past the start limit (real budget 15 s; 2 s flaked on a loaded runner, #667)
        st = core.snapshot()["startup"]
        assert st["ready"] and st["phase"] == "ready"
        voice = holder[0]
        assert core.live is voice and voice.running and voice.greeting_outcome == "missed"
        assert core._greeted and not core.tts.spoken                   # no second TTS greeting
        assert ("warn", "LIVE_GREETING_MISSED") in fast                # information code, not an error/crash
        assert not [c for lvl, c in fast if lvl == "warn" and c in ("LIVE_STOP_FAILED",)]
        assert await _user_turn_works(voice, said)                     # a later user turn works
    finally:
        await core.stop()


async def test_connection_drop_right_after_session_started_fails_cleanly_without_hanging(home, cfg, monkeypatch, fast):
    holder, said = [], []

    def behaviour(conn, voice):
        conn.session.thinking = _Note(conn, drop=True)               # the first note finds the connection gone
        conn.push({"type": "session.closed", "reason": "connection_lost"})

    core = await _core(home, cfg, monkeypatch, _factory(holder, behaviour, said))
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError):                             # ended session: a closed room, truthfully
            await asyncio.wait_for(core.prepare_desktop(), 5)
        assert time.monotonic() - t0 < 2.0
        assert core.snapshot()["startup"]["phase"] == "failed"
        assert core.live is None and not holder[0].running           # nothing half-open (or billed) is left
        assert ("warn", "LIVE_GREETING_MISSED") not in fast
    finally:
        await core.stop()
