"""Staying alive across the things an always-on PC does: sleep/resume and audio devices that vanish.

- ResumeWatcher: a cheap local timer (no network, no voice session). When the wall clock jumps far past the timer
  interval the machine slept; the callback re-opens audio and drops a voice session whose connection died asleep.
- stall_guard: wraps a microphone stream. PortAudio simply stops calling back when a headset is unplugged (or after
  resume), which used to leave the wake loop waiting forever; no chunk for `timeout_s` raises AudioUnavailable so the
  loop re-opens the (new default) device.
- reset_audio_backend: PortAudio caches the device list at initialisation; re-initialise it so a re-plugged or new
  default device is found. Only called while no stream of ours is open.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Awaitable, Callable

from .. import logs


class ResumeWatcher:
    def __init__(self, on_resume: Callable[[float], Awaitable[None]], *, interval_s: float = 5.0,
                 gap_s: float = 30.0, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.on_resume, self.interval_s, self.gap_s = on_resume, interval_s, gap_s
        self.clock, self._sleep = clock, sleep
        self.resumes = 0

    async def run(self) -> None:
        last = self.clock()
        while True:
            await self._sleep(self.interval_s)
            now = self.clock()
            slept = now - last - self.interval_s
            last = now
            if slept >= self.gap_s:
                self.resumes += 1
                logs.info("runtime", "SYSTEM_RESUMED", "the computer was asleep; re-checking audio and voice",
                          slept_s=round(slept))
                try:
                    await self.on_resume(slept)
                except Exception:
                    logs.error("runtime", "RESUME_HANDLER_FAILED", "recovery after sleep failed", exc=True)


async def stall_guard(chunks: AsyncIterator[bytes], timeout_s: float) -> AsyncIterator[bytes]:
    """Yield from `chunks`; raise AudioUnavailable when the device delivers nothing for `timeout_s` seconds."""
    it = chunks.__aiter__()
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(it.__anext__(), timeout_s) if timeout_s > 0 else await it.__anext__()
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                from ..voice.audio import AudioUnavailable   # #159: not at import (numpy: +0.4 s for `jarvis status`)
                raise AudioUnavailable("AUDIO_MIC_STALLED", f"the microphone sent nothing for {timeout_s:g} s "
                                       "(unplugged, or the computer woke up)",
                                       "reconnect the microphone; JARVES re-opens it automatically") from None
            yield chunk
    finally:
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass


def reset_audio_backend() -> bool:
    """Re-initialise PortAudio so new / re-plugged devices are seen. False when sounddevice is unavailable."""
    try:
        import sounddevice as sd
        sd._terminate()
        sd._initialize()
        logs.info("runtime", "AUDIO_BACKEND_RESET", "audio devices re-scanned")
        return True
    except Exception as e:
        logs.debug("runtime", "AUDIO_BACKEND_RESET_FAILED", "audio backend reset failed", error_type=type(e).__name__)
        return False
