"""Wake word wired into the Core, with a fake mic and FakeDetector (offline, no model, no hardware)."""
import asyncio

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
        for _ in range(200):
            if started:
                break
            await asyncio.sleep(0.02)
        assert started and c.live is not None
        assert FakeMic.closed >= 1                                   # wake mic released for the live session
        session = c.live
        c._startup_stage("ready", "test")
        await session.on_end("idle")
        assert c.live is None and c.snapshot()["startup"]["ready"]
        for _ in range(100):
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
