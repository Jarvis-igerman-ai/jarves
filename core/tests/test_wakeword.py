"""Wake word listener + end-of-speech capture: offline, no model, no hardware."""
import numpy as np
import pytest

from jarves.voice.wakeword import (FRAME_BYTES, FRAME_SAMPLES, FakeDetector, OpenWakeWordDetector, WakeListener,
                                   WakeWordDetector, WakeWordUnavailable, capture_until_silence, level_meter)

SILENCE = np.zeros(FRAME_SAMPLES, np.int16).tobytes()


async def aiter(chunks):
    for c in chunks:
        yield c


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(cooldown=2.0):
    wakes = []
    clock = Clock()

    async def on_wake(score):
        wakes.append(score)

    det = FakeDetector()
    return WakeListener(det, on_wake, cooldown_s=cooldown, clock=clock), det, wakes, clock


def test_fake_and_real_detectors_match_protocol():
    assert isinstance(FakeDetector(), WakeWordDetector)
    assert isinstance(OpenWakeWordDetector(), WakeWordDetector)


def test_unknown_model_is_a_clear_error():
    d = OpenWakeWordDetector("hey_nobody")
    try:
        import pyopen_wakeword  # noqa: F401
    except ImportError:
        with pytest.raises(WakeWordUnavailable) as e:
            d.feed(SILENCE)
        assert e.value.code == "WAKE_NO_ENGINE"
        return
    with pytest.raises(WakeWordUnavailable) as e:
        d.feed(SILENCE)
    assert e.value.code == "WAKE_BAD_MODEL"


async def test_marker_triggers_on_wake():
    lis, det, wakes, _ = make()
    await lis.run(aiter([SILENCE, SILENCE, FakeDetector.marker_chunk(), SILENCE]))
    assert wakes == [0.9]
    assert lis.stats.triggers == 1 and lis.stats.frames == 3 and lis.stats.dropped_frames == 1
    assert lis.stats.avg_cpu_ms >= 0


async def test_cooldown_blocks_retrigger_then_allows():
    lis, det, wakes, clock = make(cooldown=2.0)
    m = FakeDetector.marker_chunk()
    await lis.process(m)
    clock.t = 1.0
    await lis.process(m)                 # within cooldown
    assert wakes == [0.9]
    clock.t = 2.5
    await lis.process(m)
    assert wakes == [0.9, 0.9]
    assert det.resets >= 1               # detector state reset after a trigger


async def test_pause_and_resume():
    lis, det, wakes, _ = make()
    lis.pause()
    assert lis.paused
    await lis.process(FakeDetector.marker_chunk())
    assert wakes == [] and lis.stats.frames == 0 and lis.stats.dropped_frames == 1
    lis.resume()
    await lis.process(FakeDetector.marker_chunk())
    assert wakes == [0.9] and det.resets == 1


async def test_mute_stops_immediately_mid_chunk():
    lis, det, wakes, _ = make()

    async def on_wake(score):
        wakes.append(score)
        lis.mute()

    lis.on_wake = on_wake
    big = FakeDetector.marker_chunk() * 3 + SILENCE    # 4 frames in one chunk; mute after the first
    lis.cooldown_s = 0
    await lis.process(big)
    assert wakes == [0.9] and lis.stats.frames == 1 and len(lis._buf) == 0
    await lis.process(FakeDetector.marker_chunk())
    assert wakes == [0.9]
    lis.unmute()
    await lis.process(FakeDetector.marker_chunk())
    assert wakes == [0.9, 0.9]


async def test_odd_chunk_sizes_are_reframed_to_80ms():
    lis, det, wakes, _ = make()
    stream = (SILENCE * 3 + FakeDetector.marker_chunk() + SILENCE)
    sizes = [1, 333, 1000, 7, 2559, 4096, 3]
    chunks, i = [], 0
    while i < len(stream):
        n = sizes[len(chunks) % len(sizes)]
        chunks.append(stream[i:i + n])
        i += n
    await lis.run(aiter(chunks))
    assert set(det.frame_sizes) == {FRAME_BYTES}
    assert len(det.frame_sizes) == 4      # trigger on frame 4 clears the buffer; frame 5 is cooldown
    assert wakes == [0.9]


async def test_no_audio_retained_after_processing():
    lis, det, wakes, _ = make()
    await lis.run(aiter([SILENCE[:1001], SILENCE, SILENCE[:777]]))
    assert len(lis._buf) == 0            # partial frame dropped at end of stream
    big = [v for v in vars(lis).values() if isinstance(v, (bytes, bytearray, np.ndarray)) and len(v)]
    assert big == []
    assert all(isinstance(s, int) for s in det.frame_sizes)


def speech(ms, amp=0.3, f=220.0):
    t = np.arange(int(16000 * ms / 1000)) / 16000
    return (amp * np.sin(2 * np.pi * f * t) * 32767).astype(np.int16).tobytes()


def quiet(ms, amp=0.0005):
    rng = np.random.default_rng(0)
    return (rng.normal(0, amp, int(16000 * ms / 1000)) * 32767).astype(np.int16).tobytes()


def split(b, n=1024):
    return [b[i:i + n] for i in range(0, len(b), n)]


async def test_capture_until_silence_ends_after_speech():
    stream = quiet(300) + speech(1000) + quiet(2000) + speech(1000)
    cap = await capture_until_silence(aiter(split(stream)), silence_ms=800)
    assert cap.reason == "silence"
    assert 900 <= cap.speech_ms <= 1100
    assert 1.9 <= len(cap.audio) / 16000 <= 2.3          # 0.3 + 1.0 + 0.8 s, then stops
    assert not cap.too_quiet and cap.peak_level > 0.7


async def test_capture_speech_from_first_frame():
    cap = await capture_until_silence(aiter(split(speech(600) + quiet(1500))))
    assert cap.reason == "silence" and cap.speech_ms >= 500


async def test_capture_max_len():
    cap = await capture_until_silence(aiter(split(speech(5000))), max_s=2)
    assert cap.reason == "max_len" and abs(len(cap.audio) / 16000 - 2) < 0.05


async def test_all_zeros_is_flagged_too_quiet():
    zeros = np.zeros(16000 * 6, np.int16).tobytes()
    cap = await capture_until_silence(aiter(split(zeros)), no_speech_s=5)
    assert cap.reason == "no_speech" and cap.speech_ms == 0
    assert cap.peak_level == 0 and cap.avg_level == 0 and cap.too_quiet


def test_level_meter():
    assert level_meter(np.zeros(1280, np.int16).tobytes()) == 0.0
    assert level_meter(b"") == 0.0
    full = level_meter(speech(80, amp=1.0))
    assert 0.94 <= full <= 1.0      # full-scale sine = -3 dBFS
    mid = level_meter(np.full(1280, 0.01, np.float32))   # -40 dBFS
    assert abs(mid - 1 / 3) < 0.01
