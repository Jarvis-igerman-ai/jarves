"""Wake word wired into the Core, with a fake mic and FakeDetector (offline, no model, no hardware)."""
import asyncio
import time

import pytest

import numpy as np

from conftest import FakeSTT, FakeTTS, open_face
from jarves.app import Core
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio
from jarves.voice.wakeword import FakeDetector

CHUNK = 2560      # 80 ms at 16 kHz int16


def tone(ms, amp=0.3):
    n = 16 * ms
    return (np.sin(np.arange(n) * 2 * np.pi * 220 / 16000) * amp * 32767).astype("<i2").tobytes()


class FakeMic:
    opened = 0
    closed = 0

    def __init__(self, script):
        self.script = script
        FakeMic.opened += 1

    async def mic_chunks(self, rate, chunk_bytes):
        if FakeMic.opened == 1:
            await asyncio.sleep(0.6)                  # let the test face connect first
        for c in self.script():
            yield c
            await asyncio.sleep(0.001)
        while True:                                   # then quiet room
            yield bytes(chunk_bytes)
            await asyncio.sleep(0.01)

    def close(self):
        FakeMic.closed += 1


def wake_then_speech():
    yield bytes(CHUNK)
    yield FakeDetector.marker_chunk()
    for i in range(0, 1200, 80):
        yield tone(80)
    for _ in range(15):
        yield bytes(CHUNK)


async def make(home, cfg, script, **kw):
    FakeMic.opened = FakeMic.closed = 0
    cfg.voice.wake_enabled = True
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, wake_detector=FakeDetector(), wake_audio=lambda: FakeMic(script), **kw)
    await c.start()
    return c


async def test_hey_jarvis_then_question_gets_answer(home, cfg):
    c = await make(home, cfg, wake_then_speech)
    try:
        assert c.snapshot()["components"]["wake_word"]["status"] == "ok"
        f = await open_face(c)
        await f.wait("face.set_state", lambda p: p["state"] == "listening")
        await f.wait("assistant.activity")
        r = await f.wait("assistant.text", lambda p: p.get("source") == "command")
        assert "השעה" in r["text"] and c.wake.stats.triggers == 1
        assert any(m["type"] == "mic.level" for m in f.msgs)       # UI mic bar is fed
    finally:
        await c.stop()


async def test_wake_starts_live_conversation(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    started = []

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, **kw):
            self.on_state = on_state
            self.on_end = kw["on_end"]

        async def start(self):
            started.append(1)
            await self.on_state("listening")

        async def stop(self):
            pass
    c = await make(home, cfg, wake_then_speech, live_factory=FakeLive)
    try:
        for _ in range(1500):
            if started:
                break
            await asyncio.sleep(0.02)
        assert started and c.live is not None
        assert FakeMic.closed >= 1                                   # wake mic released for the live session
        session = c.live
        c._startup_stage("ready", "test")
        await session.on_end("idle")
        assert c.live is None and c.snapshot()["startup"]["ready"]
        for _ in range(1500):
            if c.snapshot()["wake_listening"]:
                break
            await asyncio.sleep(0.01)
        assert c.snapshot()["wake_listening"]  # quiet end resumes the local mic without a click
    finally:
        await c.stop()


async def test_mute_stops_wake_and_releases_mic(home, cfg):
    def wake_later():
        for _ in range(30):
            yield bytes(CHUNK)
        yield FakeDetector.marker_chunk()
    c = await make(home, cfg, wake_later)
    try:
        f = await open_face(c)
        await f.send("mic.set_mute", {"muted": True})
        await asyncio.sleep(0.5)
        assert c.wake.muted and c.wake.stats.triggers == 0 and c.listening is False
    finally:
        await c.stop()


async def test_wake_unavailable_is_reported(home, cfg):
    class Broken(FakeDetector):
        def check(self):
            return False, "pyopen-wakeword not installed"
    cfg.voice.wake_enabled = True
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, wake_detector=Broken(), wake_audio=lambda: FakeMic(lambda: iter(())))
    await c.start()
    try:
        w = c.snapshot()["components"]["wake_word"]
        assert w["status"] == "unavailable" and "jarvis status" in w["action"] and c.wake is None
    finally:
        await c.stop()


class QuietNearMiss(FakeDetector):
    """Scores 0.2 (below the 0.5 threshold) on the marker frame and exposes it like the real detector does."""
    last_score = 0.0

    def feed(self, pcm):
        hit = bool((np.frombuffer(pcm, np.int16) == self.MARKER).any())
        self.last_score = 0.2 if hit else 0.0
        return None


async def test_near_miss_is_logged_with_score_and_hint(home, cfg, caplog):
    """User said 'hi garvis': score 0.17 < threshold. It must be visible in the log, not silently ignored."""
    import json as _json

    def near():
        yield bytes(CHUNK)
        yield FakeDetector.marker_chunk()
        for _ in range(5):
            yield bytes(CHUNK)
    cfg.voice.wake_enabled = True
    FakeMic.opened = FakeMic.closed = 0
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, wake_detector=QuietNearMiss(threshold=0.5), wake_audio=lambda: FakeMic(near))
    await c.start()
    try:
        for _ in range(300):
            await asyncio.sleep(0.1)
            lines = [_json.loads(l) for l in (home / "logs" / "jarves.log").read_text(encoding="utf-8").splitlines()]
            hit = [l for l in lines if l["code"] == "WAKE_NEAR_MISS"]
            if hit:
                break
        assert hit and hit[0]["score"] == 0.2 and hit[0]["needs"] == 0.5 and "jarvis wake test" in hit[0]["hint"]
        assert not [l for l in lines if l["code"] == "WAKE_WORD"]        # no false wake
        summary = [l for l in lines if l["code"] == "STARTUP_SUMMARY"]
        assert summary and summary[0]["activation"] == "wake_word" and "api_key" in summary[0]
    finally:
        await c.stop()


async def test_connected_mode_wake_word_is_the_way_back_in_after_the_session_ends(home, cfg, monkeypatch):
    """Open connected (greeting in a live session). While it is open the wake mic stays closed; when the session
    ends (idle) 'Hey Jarvis' reconnects it - with a wake chime, then a ready chime, and NO spoken delay."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg.voice.activation, cfg.voice.wake_enabled, cfg.voice.chime = "conversation", True, True
    sessions, audio = [], SimAudio()

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, on_end=None, **kw):
            self.on_state, self.on_end, self.running, self.kw = on_state, on_end, False, kw
            sessions.append(self)

        async def start(self):
            self.running = True
            await self.on_state("listening")

        async def stop(self):
            self.running = False

        async def wait_ready(self):
            return

        def present(self):
            pass

    def script():
        yield bytes(CHUNK)
        yield FakeDetector.marker_chunk()
        for _ in range(400):
            yield bytes(CHUNK)
    FakeMic.opened = FakeMic.closed = 0
    c = Core(cfg, home, audio=audio, player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=FakeLive, wake_detector=FakeDetector(), wake_audio=lambda: FakeMic(script))
    await c.start()
    try:
        assert c.snapshot()["components"]["wake_word"]["status"] == "ok"     # wake is ON in connected mode
        await c.prepare_desktop()
        assert len(sessions) == 1 and c.live is sessions[0] and sessions[0].kw.get("hold_output") is True
        await asyncio.sleep(1.5)
        assert len(sessions) == 1 and FakeMic.opened == 0                    # wake mic closed during the conversation
        await sessions[0].on_end("idle")                                     # quiet for 3 minutes: back to wake mode
        for _ in range(400):
            await asyncio.sleep(0.1)
            if len(sessions) == 2:
                break
        assert len(sessions) == 2                                            # "Hey Jarvis" opened a new conversation
        assert sessions[1].kw.get("greeting") is None                       # no spoken "yes?": talk right away
        await asyncio.sleep(0.6)
        assert [n for n, _ in audio.played][:1] == [2400]                    # ONE wake chime (0.10 s)
        assert 4800 not in [n for n, _ in audio.played]                      # one-pip rule: no second ready pip
    finally:
        await c.stop()


async def _conversation_core(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg.voice.activation, cfg.voice.wake_enabled = "conversation", False
    sessions = []

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, on_end=None, **kw):
            self.on_state, self.on_end, self.running, self._last_activity = on_state, on_end, False, time.monotonic()
            sessions.append(self)

        async def start(self):
            self.running = True

        async def stop(self):
            self.running = False

        async def wait_ready(self):
            return

        def present(self):
            pass
    c = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(), console_logs=False,
             use_real_audio=False, live_factory=FakeLive)
    await c.start()
    await c.prepare_desktop()
    return c, sessions


async def test_server_close_in_the_middle_of_a_conversation_resumes_smoothly(home, cfg, monkeypatch):
    c, sessions = await _conversation_core(home, cfg, monkeypatch)
    try:
        await sessions[0].on_end("remote_closed")                  # server closed it while the user was active
        for _ in range(400):
            await asyncio.sleep(0.1)
            if len(sessions) == 2:
                break
        assert len(sessions) == 2 and c.live is sessions[1]       # back in the same conversation, no wake word
    finally:
        await c.stop()


@pytest.mark.parametrize("reason", ["idle", "user", "budget", "error:invalid_api_key"])
async def test_real_ends_do_not_resume(home, cfg, monkeypatch, reason):
    c, sessions = await _conversation_core(home, cfg, monkeypatch)
    try:
        await sessions[0].on_end(reason)
        await asyncio.sleep(1.0)
        assert len(sessions) == 1 and c.live is None
    finally:
        await c.stop()


async def test_resume_is_limited_when_the_server_keeps_closing(home, cfg, monkeypatch):
    c, sessions = await _conversation_core(home, cfg, monkeypatch)
    try:
        for n in range(1, 6):
            await sessions[-1].on_end("remote_closed")
            await asyncio.sleep(0.8)
        assert len(sessions) == 4                                  # first + at most 3 resumes
    finally:
        await c.stop()
