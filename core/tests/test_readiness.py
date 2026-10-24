"""Readiness is based on received greeting audio, not a timer; no real network/microphone."""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from conftest import FakeSTT, FakeTTS, open_face
from jarves.app import Core
from jarves import cli
from jarves.skills import SkillRegistry
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio
from jarves.voice.live import LiveVoice
from test_voice_faults import FakeAudio, FakeClient

def _loud(nbytes):
    import numpy as np
    return (np.sin(np.arange(nbytes // 2) * 2 * np.pi * 220 / 24000) * 0.3 * 32767).astype("<i2").tobytes()


async def test_live_greeting_is_buffered_without_opening_mic_until_presented():
    opened = []

    class Audio(FakeAudio):
        async def mic_chunks(self, rate, chunk_bytes):
            opened.append("mic")
            async for data in super().mic_chunks(rate, chunk_bytes):
                yield data

        async def run_player(self, rate):
            opened.append("speaker")
            await super().run_player(rate)

    client, audio = FakeClient(), Audio()
    voice = LiveVoice(SkillRegistry(), lambda: "backend", client=client, audio_io=audio,
                      greeting="שלום", hold_output=True)
    assert voice.session_config()["input"][0]["role"] == "developer"
    await voice.start()
    try:
        waiting = asyncio.create_task(voice.wait_ready(timeout=1))
        await asyncio.sleep(0.02)
        assert not waiting.done() and opened == [] and client.conn.mic_frames > 0  # generated silence only
        client.conn.push({"type": "session.output_audio.delta", "delta": base64.b64encode(_loud(4800)).decode()})
        await waiting
        assert opened == [] and audio.enqueued
        voice.present()
        await asyncio.sleep(0.12)
        assert set(opened) == {"mic", "speaker"}
    finally:
        await voice.stop()


async def test_no_greeting_audio_never_reports_ready():
    voice = LiveVoice(SkillRegistry(), lambda: "backend", client=FakeClient(), greeting="שלום", hold_output=True)
    await voice.start()
    try:
        with pytest.raises(RuntimeError, match="audio was not received"):
            await voice.wait_ready(timeout=0.01)
    finally:
        await voice.stop()


async def test_prepare_waits_for_audio_and_reuses_session_without_tts(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    cfg.voice.activation = "conversation"
    cfg.voice.greeting = True
    audio_ready = asyncio.Event()
    calls = []

    class Live:
        running = True

        def __init__(self, core, **kwargs):
            assert kwargs["hold_output"] and kwargs["greeting"]

        async def start(self):
            calls.append("connect")

        async def wait_ready(self):
            await audio_ready.wait()

        def present(self):
            calls.append("present")

        async def stop(self):
            calls.append("stop")

    speech = FakeTTS()
    core = Core(cfg, home, audio=SimAudio(), stt=FakeSTT(), tts=speech, player=FakePlayer(),
                live_factory=Live, console_logs=False, use_real_audio=False)
    await core.start()
    try:
        task = asyncio.create_task(core.prepare_desktop())
        await asyncio.sleep(0)
        assert not core.snapshot()["startup"]["ready"] and not task.done()
        audio_ready.set()
        await task
        assert core.snapshot()["startup"]["progress"] == 90
        face = await open_face(core)
        assert calls == ["connect", "present"] and not speech.spoken
        assert core.snapshot()["startup"]["progress"] == 100
        timing = json.loads((home / "startup_timing.json").read_text())
        assert set(timing) == {"signature", "seconds"}
        await face.ws.close()
    finally:
        await core.stop()


class _NoGreetingLive:
    running = True
    calls: list = []

    def __init__(self, core, **kwargs):
        pass

    async def start(self):
        pass

    async def wait_ready(self):
        raise RuntimeError("live greeting audio was not received; check voice/network logs")

    def present(self):
        pass

    async def stop(self):
        type(self).calls.append("stop")


async def test_missing_greeting_with_open_session_degrades_instead_of_failing(home, cfg, monkeypatch):
    """#122: 1 of 5 GPT-Live starts got no greeting audio; the Core crashed at the 15 s limit. A live session that is
    still open continues WITHOUT a greeting (truthful log + startup detail), no second TTS greeting, no crash."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    cfg.voice.activation = "conversation"
    cfg.voice.greeting = True

    class Live(_NoGreetingLive):
        calls = []

    speech = FakeTTS()
    core = Core(cfg, home, audio=SimAudio(), stt=FakeSTT(), tts=speech, player=FakePlayer(),
                live_factory=Live, console_logs=False, use_real_audio=False)
    await core.start()
    try:
        await core.prepare_desktop()
        st = core.snapshot()["startup"]
        assert st["ready"] and core.live is not None and not speech.spoken and "stop" not in Live.calls
    finally:
        await core.stop()


async def test_missing_greeting_with_ended_session_still_fails(home, cfg, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    cfg.voice.activation = "conversation"
    cfg.voice.greeting = True

    class Live(_NoGreetingLive):
        running = False
        calls = []

    core = Core(cfg, home, audio=SimAudio(), stt=FakeSTT(), tts=FakeTTS(), player=FakePlayer(),
                live_factory=Live, console_logs=False, use_real_audio=False)
    await core.start()
    try:
        with pytest.raises(RuntimeError, match="did not become ready"):
            await core.prepare_desktop()
    finally:
        await core.stop()


async def test_close_loading_window_cancels_boot_and_stops_core():
    closing = asyncio.Event()
    events = []

    class Viewer:
        returncode = None

        async def wait(self):
            await closing.wait()
            self.returncode = 0

        def terminate(self):
            closing.set()

    class CoreDouble:
        cfg = SimpleNamespace(link=SimpleNamespace(port=12345))
        stop_event = asyncio.Event()
        dev = object()

        def _install_stop_signals(self):             # issue #17 (fix/reliability): cli._run_desktop calls it
            pass

        async def start(self):
            events.append("starting")

        async def prepare_desktop(self):
            await asyncio.Event().wait()

        async def stop(self):
            events.append("stopped")

    async def launch(port):
        return Viewer()

    task = asyncio.create_task(cli._run_desktop(CoreDouble(), launch))
    await asyncio.sleep(0.01)
    closing.set()
    await asyncio.wait_for(task, 1)
    assert events == ["starting", "stopped"]


async def test_close_during_connection_cannot_leave_live_session(core):
    connected, release, stopped = asyncio.Event(), asyncio.Event(), []

    class Live:
        async def start(self):
            connected.set()
            await release.wait()

        async def stop(self):
            stopped.append(True)

    core._live_factory = lambda *args, **kwargs: Live()
    core._run_busy(core._start_live())
    await connected.wait()
    shutdown = asyncio.create_task(core.stop())
    await asyncio.sleep(0)
    release.set()
    await shutdown
    assert stopped == [True] and core.live is None
