"""JAR-197: the start (wake) and end (close) chimes, proven through a SILENT virtual audio sink.

The production path is used end to end: Core._chime -> DevicePort.speaker_play -> SoundDeviceAudio.play -> the
`sounddevice` module. Only the last hop is replaced: a virtual output device that records every buffer instead of
sending it to a sound card. Nothing is audible, no real device is opened, nothing is written to disk. The recorded
buffers are then checked by frequency, duration and level, so a silent, clipped, wrong or swapped chime fails.

What this does NOT prove: that a human hears it through the owner's real speakers (UNVERIFIED: ear check).
"""
import asyncio
import sys
import types

import numpy as np
import pytest

from conftest import FakeSTT, FakeTTS
from jarves.app import Core
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SoundDeviceAudio


class VirtualSink:
    """A `sounddevice` stand-in: one virtual output device that captures what is played. Silent by construction."""

    NAME = "JARVES Virtual Sink (silent)"

    def __init__(self) -> None:
        self.buffers: list[tuple[np.ndarray, int, object]] = []      # (samples, rate, device)

    def module(self) -> types.ModuleType:
        m = types.ModuleType("sounddevice")
        m.query_devices = lambda device=None, kind=None: {"name": self.NAME, "max_input_channels": 0,
                                                          "max_output_channels": 2}
        m.play = lambda data, samplerate, device=None, **kw: self.buffers.append(
            (np.array(data, dtype=np.float32, copy=True), int(samplerate), device))
        m.stop = lambda: None
        return m


def dominant_hz(x: np.ndarray, rate: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=1 << 16))
    return float(np.fft.rfftfreq(1 << 16, 1 / rate)[int(np.argmax(spec))])


def segments(x: np.ndarray, rate: int, frame_s: float = 0.02) -> list[tuple[float, float]]:
    """(dominant Hz, seconds) per note: the dominant frequency of each 20 ms frame with sound in it, then runs of
    the same frequency joined. A frame that straddles two notes is a run of one and is dropped."""
    n = int(rate * frame_s)
    hz = [round(dominant_hz(x[i:i + n], rate) / 10) * 10 if np.max(np.abs(x[i:i + n])) > 0.01 else 0
          for i in range(0, len(x) - n + 1, n)]
    runs: list[list[float]] = []
    for f in hz:
        if runs and runs[-1][0] == f:
            runs[-1][1] += frame_s
        else:
            runs.append([f, frame_s])
    return [(f, d) for f, d in runs if f and d > frame_s * 1.5]


@pytest.fixture
def sink(monkeypatch):
    s = VirtualSink()
    monkeypatch.setitem(sys.modules, "sounddevice", s.module())
    return s


async def make_core(home, cfg, chime=True, live_factory=None):
    cfg.voice.chime = chime
    c = Core(cfg, home, audio=SoundDeviceAudio(output_device=VirtualSink.NAME), player=FakePlayer(), stt=FakeSTT(),
             tts=FakeTTS(), console_logs=False, use_real_audio=False, live_factory=live_factory)
    await c.start()
    return c


def note_freqs(x: np.ndarray, rate: int) -> list[float]:
    return [hz for hz, _ in segments(x, rate)]


async def test_the_virtual_sink_is_what_core_plays_to(home, cfg, sink):
    c = await make_core(home, cfg)
    try:
        ok, detail = c.dev.audio.check()
        assert ok and VirtualSink.NAME in detail                      # the adapter really sees the virtual device
    finally:
        await c.stop()


async def test_start_chime_is_one_soft_high_pip(home, cfg, sink):
    c = await make_core(home, cfg)
    try:
        await c._chime("wake")
        assert len(sink.buffers) == 1
        x, rate, dev = sink.buffers[0]
        assert dev == VirtualSink.NAME and rate == 24000
        assert abs(len(x) / rate - 0.10) < 0.005                     # 0.10 s: short, not a speech-length sound
        assert note_freqs(x, rate) == [880] and dominant_hz(x, rate) == pytest.approx(880, abs=5)                           # one high note = "I heard you"
        peak = float(np.max(np.abs(x)))
        assert 0.05 < peak <= 0.25                                    # audible but soft (0.22 x volume), not clipped
        assert abs(x[0]) < 0.02 and abs(x[-1]) < 0.02                 # soft attack/release: no click at the edges
    finally:
        await c.stop()


async def test_end_chime_is_a_falling_two_note_cue(home, cfg, sink):
    c = await make_core(home, cfg)
    try:
        await c._chime("close")
        assert len(sink.buffers) == 1
        x, rate, _ = sink.buffers[0]
        assert abs(len(x) / rate - 0.34) < 0.01
        notes = segments(x, rate)
        assert [hz for hz, _ in notes] == [660, 440]  # falling = "closing", distinct from the start
        assert notes[0][0] > notes[1][0]
        assert 0.05 < float(np.max(np.abs(x))) <= 0.25
    finally:
        await c.stop()


async def test_start_and_end_chimes_are_distinguishable(home, cfg, sink):
    c = await make_core(home, cfg)
    try:
        await c._chime("wake")
        await c._chime("close")
        (start, r1, _), (end, r2, _) = sink.buffers
        assert note_freqs(start, r1) != note_freqs(end, r2)
        assert len(end) > len(start)
    finally:
        await c.stop()


async def test_chimes_switch_off_means_the_sink_hears_nothing(home, cfg, sink):
    c = await make_core(home, cfg, chime=False)
    try:
        await c._chime("wake")
        await c._chime("close")
        assert sink.buffers == []
    finally:
        await c.stop()


async def test_hidden_mode_idle_end_plays_the_end_chime_through_the_sink(home, cfg, sink, monkeypatch):
    """The real trigger: a live conversation ends with reason idle_stop -> the end chime reaches the sink."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    cfg.voice.activation, cfg.voice.wake_enabled = "conversation", False
    sessions = []

    class FakeLive:
        def __init__(self, core, on_state, on_transcript, on_usage, on_end=None, **kw):
            self.on_end, self.running = on_end, False
            sessions.append(self)

        async def start(self):
            self.running = True

        async def stop(self):
            self.running = False

        async def wait_ready(self):
            return

        def present(self):
            pass

    c = await make_core(home, cfg, live_factory=FakeLive)
    try:
        await c.prepare_desktop()
        assert sessions
        sink.buffers.clear()
        await sessions[0].on_end("idle_stop")
        for _ in range(50):
            await asyncio.sleep(0.05)
            if sink.buffers:
                break
        assert len(sink.buffers) == 1
        x, rate, _ = sink.buffers[0]
        assert note_freqs(x, rate) == [660, 440]
    finally:
        await c.stop()
