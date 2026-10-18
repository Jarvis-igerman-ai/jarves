"""Desktop close, hands-free activation and streamed speech, with no network or real audio."""
import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace

import numpy as np
import pytest

from conftest import FakeSTT, FakeTTS, open_face
from jarves.app import Core
from jarves import cli
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio, SoundDeviceAudio
from jarves.voice import realtime
from jarves.voice.tts import FallbackTTS, MeteredTTS, TTSUnavailable


@pytest.mark.parametrize("activation,expected", [("conversation", 1), ("wake_word", 0)])
async def test_hands_free_activation_and_close(home, cfg, monkeypatch, activation, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-real")
    cfg.voice.activation = activation
    cfg.voice.greeting = True
    started, stopped = [], []

    class Live:
        def __init__(self, core, on_state, **kwargs):
            self.state = on_state

        async def start(self):
            started.append(True)
            await self.state("listening")

        async def stop(self):
            stopped.append(True)

    speech = FakeTTS()
    core = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=speech,
                use_real_audio=False, console_logs=False, live_factory=Live)
    await core.start()
    try:
        face = await open_face(core)
        await face.wait("assistant.text", lambda p: p.get("code") == "GREETING")
        if expected:
            await face.wait("face.set_state", lambda p: p["state"] == "listening")
        else:                                  # wake mode: says hello once (no live session), then idles
            await face.wait("face.set_state", lambda p: p["state"] == "idle")
        assert len(started) == expected
        assert speech.spoken == ["שלום! אני JARVES."]     # both modes greet audibly, exactly once
        await face.send("desktop.close")
        await face.wait("desktop.closed")
        await asyncio.wait_for(core.stop_event.wait(), 1)
        await face.ws.close()
    finally:
        await core.stop()
    assert len(stopped) == expected


async def test_speech_first_chunk_before_generation_finishes(monkeypatch):
    release = asyncio.Event()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send_message(self, text):
            pass

        async def events(self):
            yield SimpleNamespace(type="audio", audio=SimpleNamespace(data=bytes(480)))
            await release.wait()
            yield SimpleNamespace(type="audio_end")

        def __aiter__(self):
            return self.events()

    class Runner:
        async def run(self):
            return Session()

    monkeypatch.setattr(realtime, "_runner", lambda *args: Runner())
    async with aclosing(realtime.RealtimeSpeaker().stream("hello")) as source:
        first, rate = await asyncio.wait_for(anext(source), 1)
        assert len(first) == 240 and rate == 24000 and not release.is_set()
        release.set()
        with pytest.raises(StopAsyncIteration):
            await anext(source)


async def test_stream_no_replay_after_partial_failure():
    calls = []

    class Cloud:
        name, quality = "cloud", "cloud"

        async def stream(self, text):
            yield np.zeros(24), 24000
            raise TTSUnavailable("TTS_FAILED", "connection lost")

    class Local:
        name, quality = "local", "local-fallback"

        async def synth(self, text):
            calls.append(text)
            return np.zeros(24), 24000

    source = FallbackTTS([Cloud(), Local()]).stream("hello")
    async with aclosing(source):
        await anext(source)
        with pytest.raises(TTSUnavailable):
            await anext(source)
    assert calls == []


async def test_stream_cancel_settles_budget(core):
    class Cloud:
        name, quality = "cloud", "cloud"

        async def stream(self, text):
            yield np.zeros(24), 24000
            await asyncio.Event().wait()

    async with aclosing(MeteredTTS(Cloud(), core.budget).stream("hello")) as source:
        await anext(source)
    assert core.memory.usage_today("ai_requests") == 1


async def test_core_plays_stream_before_producer_finishes(core):
    class Speech:
        async def stream(self, text):
            yield np.zeros(24), 24000
            assert core.dev.audio.played == [(24, 24000)]
            yield np.zeros(48), 24000

    core.tts = Speech()
    await core.speak("hello")
    assert core.dev.audio.played == [(24, 24000), (48, 24000)]


async def test_output_deltas_share_one_device_stream(monkeypatch):
    actions = []

    class Output:
        def __init__(self, **kwargs):
            actions.append("open")

        def start(self):
            pass

        def write(self, data):
            actions.append(len(data))

        def stop(self):
            actions.append("drain")

        def close(self):
            actions.append("close")

    audio = SoundDeviceAudio()
    monkeypatch.setattr(audio, "_sd", lambda: SimpleNamespace(OutputStream=Output))

    async def chunks():
        yield np.zeros(24), 24000
        assert actions == ["open", 24]
        yield np.zeros(48), 24000

    assert await audio.play_stream(chunks())
    assert actions == ["open", 24, 48, "drain", "close"]


def test_terminal_follower_exits_on_core_stop(home, cfg):
    path = home / "logs" / "jarves.log"
    path.parent.mkdir()
    path.write_text(json.dumps({"code": "CORE_STOPPED"}) + "\n", encoding="utf-8")
    args = SimpleNamespace(log_level=None, module=None, hide=None, lines=50, follow=True, until_stopped=True)
    assert cli.cmd_logs(args, cfg, home) == 0
