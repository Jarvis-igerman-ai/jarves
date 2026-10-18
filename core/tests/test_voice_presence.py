"""Idle lifecycle and actual audio-level events; no network or physical devices."""
import asyncio
import time

import pytest

from jarves.skills import SkillRegistry
from jarves.voice.live import LiveVoice
from test_voice_faults import FakeClient


async def test_idle_waits_for_tools_and_recent_activity_then_closes_once():
    ended = []
    voice = LiveVoice(SkillRegistry(), lambda: "backend", client=FakeClient(),
                      idle_disconnect_s=0.03, on_end=ended.append)
    await voice.start()
    try:
        voice._backend_busy = True
        voice._last_activity = time.monotonic() - 20
        await asyncio.sleep(0.07)
        assert voice.running and not ended
        voice._backend_busy = False
        voice.note_activity()
        await asyncio.sleep(0.01)
        assert voice.running
        await asyncio.wait_for(voice.wait_ended(), 1)
        assert ended == ["idle"]
        assert voice.end_reason == "idle"
    finally:
        await voice.stop()


async def test_queued_playback_prevents_idle_shutdown():
    class Audio:
        def queued_s(self): return 2
    voice = LiveVoice(SkillRegistry(), lambda: "backend", client=FakeClient(), idle_disconnect_s=0.02)
    await voice.start()
    voice.audio_io = Audio()
    try:
        voice._last_activity = time.monotonic() - 20
        await asyncio.sleep(0.06)
        assert voice.running
    finally:
        voice.audio_io = None
        await voice.stop()


async def test_speaker_envelope_is_emitted_by_player_not_enqueue(monkeypatch):
    import sys
    from types import SimpleNamespace
    import numpy as np
    from jarves.voice.audio import StreamingAudio
    levels, writes = [], []
    observed = asyncio.Event()

    async def level(value):
        levels.append(value)
        observed.set()

    class Output:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def write(self, data): writes.append(data)

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawOutputStream=Output))
    audio = StreamingAudio(on_output_level=level)
    pcm = np.full(2400, 3277, dtype="<i2").tobytes()
    audio.enqueue(pcm)
    assert not levels
    player = asyncio.create_task(audio.run_player(24000))
    await asyncio.wait_for(observed.wait(), 1)
    audio.close()
    await asyncio.wait_for(player, 1)
    assert writes == [pcm] and levels[0] == pytest.approx(0.5, abs=0.01)


async def test_desktop_waits_for_actual_wake_mic_frames(home, cfg):
    from conftest import FakeSTT, FakeTTS
    from jarves.app import Core
    from jarves.skills.music import FakePlayer
    from jarves.voice.audio import SimAudio
    from jarves.voice.wakeword import FakeDetector
    release = asyncio.Event()

    class Mic:
        async def mic_chunks(self, rate, chunk_bytes):
            await release.wait()
            while True:
                yield bytes(chunk_bytes)
                await asyncio.sleep(0.01)
        def close(self): pass

    cfg.voice.wake_enabled = True
    core = Core(cfg, home, audio=SimAudio(), player=FakePlayer(), stt=FakeSTT(), tts=FakeTTS(),
                console_logs=False, use_real_audio=False, wake_detector=FakeDetector(), wake_audio=Mic)
    await core.start()
    core.use_real_audio = True  # exercise readiness while all physical adapters remain simulated
    boot = asyncio.create_task(core.prepare_desktop())
    try:
        await asyncio.sleep(0.02)
        assert not boot.done() and not core.snapshot()["startup"]["ready"]
        release.set()
        await asyncio.wait_for(boot, 1)
        assert core.snapshot()["wake_listening"] and core.snapshot()["startup"]["ready"]
    finally:
        release.set()
        await core.stop()
