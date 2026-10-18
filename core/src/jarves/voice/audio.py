"""Audio adapters behind DevicePort: real PortAudio devices (Windows/Linux) and a simulated device for tests.

The mic is opened only while push-to-talk is held (or a realtime session runs) and never while muted.
Raw audio is never written to disk or logs.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Protocol

import numpy as np

from .. import logs

SR = 16000


class AudioUnavailable(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


class AudioDevice(Protocol):
    def check(self) -> tuple[bool, str]: ...
    def start_capture(self, rate: int = SR) -> None: ...
    def stop_capture(self) -> np.ndarray: ...
    async def play(self, samples: np.ndarray, rate: int) -> bool: ...   # False if interrupted
    def stop_playback(self) -> None: ...


class SoundDeviceAudio:
    def __init__(self, input_device=None, output_device=None, max_s: float = 15, volume: float = 1.0) -> None:
        self.inp, self.out, self.max_s, self.volume = input_device, output_device, max_s, volume
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()
        self._stop_play = asyncio.Event()

    def _sd(self):
        try:
            import sounddevice as sd
            return sd
        except (ImportError, OSError) as e:
            raise AudioUnavailable("AUDIO_NO_BACKEND", "PortAudio/sounddevice is not installed",
                                   "install the voice extra: uv sync --extra voice (Linux: apt install libportaudio2)") from e

    def check(self) -> tuple[bool, str]:
        try:
            sd = self._sd()
            i = sd.query_devices(self.inp, "input")
            o = sd.query_devices(self.out, "output")
            return True, f"in: {i['name']}; out: {o['name']}"
        except AudioUnavailable as e:
            return False, e.message
        except Exception as e:      # PortAudioError / ValueError when no such device
            return False, f"audio device missing: {e}"

    def start_capture(self, rate: int = SR) -> None:
        sd = self._sd()
        self._chunks = []
        limit = int(self.max_s * rate)

        def cb(indata, frames, t, status):
            with self._lock:
                if sum(len(c) for c in self._chunks) < limit:
                    self._chunks.append(indata[:, 0].copy())
        try:
            self._stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", device=self.inp, callback=cb)
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise AudioUnavailable("AUDIO_MIC_UNAVAILABLE", f"cannot open the microphone: {e}",
                                   "check the mic is connected and Windows mic permission is on") from e
        logs.debug("audio", "MIC_OPEN", "microphone opened")

    def stop_capture(self) -> np.ndarray:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
            logs.debug("audio", "MIC_CLOSED", "microphone closed")
        with self._lock:
            out = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        return out

    async def play(self, samples: np.ndarray, rate: int) -> bool:
        sd = self._sd()
        self._stop_play.clear()
        try:
            sd.play(np.clip(samples * self.volume, -1, 1).astype(np.float32), rate, device=self.out)
        except Exception as e:
            raise AudioUnavailable("AUDIO_SPEAKER_UNAVAILABLE", f"cannot play audio: {e}",
                                   "check the speaker/output device") from e
        dur = len(samples) / rate
        loop = asyncio.get_running_loop()
        end = loop.time() + dur + 0.3
        while loop.time() < end:
            if self._stop_play.is_set():
                sd.stop()
                return False
            await asyncio.sleep(0.03)
        return True

    def stop_playback(self) -> None:
        self._stop_play.set()


class StreamingAudio:
    """Continuous PCM16 mic/speaker streams for live voice sessions (GPT-Live / Realtime)."""

    def __init__(self, input_device=None, output_device=None) -> None:
        self.inp, self.out = input_device, output_device
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._open = True

    async def mic_chunks(self, rate: int, chunk_bytes: int):
        import sounddevice as sd
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[bytes] = asyncio.Queue()

        def cb(indata, frames, t, status):
            loop.call_soon_threadsafe(q.put_nowait, bytes(indata))
        try:
            stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=chunk_bytes // 2,
                                       device=self.inp, callback=cb)
        except Exception as e:
            raise AudioUnavailable("AUDIO_MIC_UNAVAILABLE", f"cannot open the microphone: {e}",
                                   "check the mic and OS microphone permission") from e
        with stream:
            while self._open:
                yield await q.get()

    def enqueue(self, pcm: bytes) -> None:
        self._q.put_nowait(pcm)

    def flush(self) -> None:
        while not self._q.empty():
            self._q.get_nowait()

    async def run_player(self, rate: int) -> None:
        import sounddevice as sd
        with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", device=self.out) as out:
            while True:
                data = await self._q.get()
                if data is None:
                    return
                await asyncio.to_thread(out.write, data)

    def close(self) -> None:
        self._open = False
        self._q.put_nowait(None)


class SimAudio:
    """Simulated audio device: returns a fixture recording, records what would be played."""

    def __init__(self, fixture: np.ndarray | None = None, present: bool = True) -> None:
        self.fixture = fixture if fixture is not None else np.zeros(SR // 2, np.float32)
        self.present = present
        self.capturing = False
        self.played: list[tuple[int, int]] = []     # (samples, rate)
        self._stop = asyncio.Event()

    def check(self) -> tuple[bool, str]:
        return (True, "simulated audio") if self.present else (False, "simulated audio device unplugged")

    def start_capture(self, rate: int = SR) -> None:
        if not self.present:
            raise AudioUnavailable("AUDIO_MIC_UNAVAILABLE", "simulated mic unplugged", "plug the mic in")
        self.capturing = True

    def stop_capture(self) -> np.ndarray:
        self.capturing = False
        return self.fixture

    async def play(self, samples: np.ndarray, rate: int) -> bool:
        if not self.present:
            raise AudioUnavailable("AUDIO_SPEAKER_UNAVAILABLE", "simulated speaker unplugged")
        self._stop.clear()
        self.played.append((len(samples), rate))
        try:
            await asyncio.wait_for(self._stop.wait(), min(len(samples) / rate, 0.2))
            return False
        except asyncio.TimeoutError:
            return True

    def stop_playback(self) -> None:
        self._stop.set()
