"""Hermetic idle-wake soak: ~2 h of fake silence through WakeListener; memory must stay flat (JAR-96)."""
import asyncio
import gc
import tracemalloc

import numpy as np

from jarves.voice.wakeword import FRAME_SAMPLES, WakeListener

FRAMES = 90_000          # 90000 x 80 ms = 2 h of audio, no real audio/network/clock
SILENCE = np.zeros(FRAME_SAMPLES, np.int16).tobytes()


class NullDetector:
    """Stateless scorer: what the product keeps per frame is the listener's job, not the detector's."""
    blocking = False

    def feed(self, pcm):
        return None

    def reset(self):
        pass


async def _drive(lis, frames):
    for i in range(frames):
        # odd chunk sizes exercise the reframing buffer
        await lis.process(SILENCE[: len(SILENCE) // 2] if i % 2 else SILENCE)
        await lis.process(SILENCE[len(SILENCE) // 2:] if i % 2 else b"")


def test_idle_listener_memory_flat_and_cpu_reported():
    async def on_wake(score):
        raise AssertionError("silence must never wake")

    lis = WakeListener(NullDetector(), on_wake)
    asyncio.run(_drive(lis, 2000))            # warm-up
    gc.collect()
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    asyncio.run(_drive(lis, FRAMES))
    gc.collect()
    grown = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    assert lis.stats.frames >= FRAMES
    assert lis.stats.triggers == 0
    assert len(lis._buf) < 2 * len(SILENCE)
    assert grown < 64 * 1024, f"idle listener grew {grown} bytes over {FRAMES} frames"
    assert lis.stats.avg_cpu_ms < 5.0         # generous: idle frame cost, not a perf gate
