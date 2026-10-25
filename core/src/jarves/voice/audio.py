"""Audio adapters behind DevicePort: real PortAudio devices (Windows/Linux) and a simulated device for tests.

The mic is opened only while push-to-talk is held (or a realtime session runs) and never while muted.
Raw audio is never written to disk or logs.
"""
from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Protocol

import numpy as np

from .. import logs

SR = 16000
MIC_LOST_DETAIL = "The microphone stopped sending audio (unplugged, Bluetooth dropped or the audio route changed)."
SPEAKER_LOST_DETAIL = "The speaker stopped accepting audio (unplugged, Bluetooth dropped or the audio route changed)."
ROUTE_LOST_ACTION = ("Reconnect the headset or microphone, then press push-to-talk or say the wake word. "
                     "JARVES never reopens the microphone by itself.")
ROUTE_LOST_CODES = ("AUDIO_MIC_STALLED", "AUDIO_SPEAKER_UNAVAILABLE")
MIC_STALL_S = 5.0       # a live mic delivers a chunk every ~100 ms; 5 s of nothing means unplugged / Bluetooth dropped


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

    async def play_stream(self, chunks) -> bool:
        """One output stream for all deltas; never open a microphone or save audio."""
        self._stop_play.clear()
        out, stream_rate = None, None
        try:
            async for samples, rate in chunks:
                if self._stop_play.is_set():
                    return False
                try:
                    if out is None:
                        stream_rate = rate
                        out = self._sd().OutputStream(samplerate=rate, channels=1, dtype="float32", device=self.out)
                        out.start()
                    if rate != stream_rate:
                        raise ValueError("speech sample rate changed mid-stream")
                    data = np.clip(samples * self.volume, -1, 1).astype(np.float32).reshape(-1, 1)
                    for offset in range(0, len(data), max(1, rate // 10)):
                        if self._stop_play.is_set():
                            return False
                        await asyncio.to_thread(out.write, data[offset:offset + max(1, rate // 10)])
                except AudioUnavailable:
                    raise
                except Exception as e:
                    raise AudioUnavailable("AUDIO_SPEAKER_UNAVAILABLE", f"cannot stream audio: {e}",
                                           "check the speaker/output device") from e
            return True
        finally:
            if out is not None:
                try:
                    out.abort() if self._stop_play.is_set() else out.stop()
                finally:
                    out.close()


class StreamingAudio:
    """Continuous PCM16 mic/speaker streams for live voice sessions (GPT-Live / Realtime).

    Both directions are bounded (CR-06) so a slow speaker, network or Pi CPU can never grow memory or latency:
    - mic: ~mic_max_s of chunks; when the sender falls behind the OLDEST chunk is dropped (counted).
    - speaker: ~speaker_max_s of PCM; when playback falls behind the OLDEST audio is dropped (counted), so the
      user never hears ever-staler speech. flush() discards everything queued (barge-in / interruption).
    The player writes <=100 ms pieces in a worker thread (the blocking write is the natural backpressure),
    so a flush takes effect within ~100 ms. No busy loops: both sides wait on asyncio Events.
    """

    def __init__(self, input_device=None, output_device=None, *, rate: int = 24000, mic_max_s: float = 2.0,
                 speaker_max_s: float = 12.0, on_output_level=None, mic_stall_s: float = MIC_STALL_S,
                 backend=None) -> None:
        self.inp, self.out = input_device, output_device
        self.mic_stall_s = mic_stall_s                  # no mic chunk for this long = the route is gone (0 = off)
        self._backend = backend                         # sounddevice-compatible module; None = the real one
        self.rate, self.mic_max_s, self.speaker_max_s = rate, mic_max_s, speaker_max_s
        self.on_output_level = on_output_level
        self._open = True
        # mic (filled from the PortAudio thread via call_soon_threadsafe -> _mic_push on the loop thread)
        self._mic_q: deque[bytes] = deque()
        self._mic_max_chunks = max(1, int(mic_max_s * 10))          # default 100 ms chunks
        self._mic_ready = asyncio.Event()
        # speaker
        self._spk_q: deque[bytes] = deque()
        self._spk_bytes = 0
        self._spk_ready = asyncio.Event()
        self._m = {"mic_drops": 0, "mic_max_depth": 0, "speaker_drops": 0, "speaker_dropped_bytes": 0,
                   "speaker_max_bytes": 0, "speaker_flushes": 0, "speaker_flushed_bytes": 0}

    # ------------------------------------------------------------------ mic
    def _mic_push(self, data: bytes) -> None:
        if not self._open:
            return
        if len(self._mic_q) >= self._mic_max_chunks:
            self._mic_q.popleft()
            self._m["mic_drops"] += 1
        self._mic_q.append(data)
        self._m["mic_max_depth"] = max(self._m["mic_max_depth"], len(self._mic_q))
        self._mic_ready.set()

    async def _mic_get(self) -> bytes | None:
        while not self._mic_q:
            if not self._open:
                return None
            self._mic_ready.clear()
            try:
                if self.mic_stall_s > 0:
                    await asyncio.wait_for(self._mic_ready.wait(), self.mic_stall_s)
                else:
                    await self._mic_ready.wait()
            except asyncio.TimeoutError:
                if not self._open or self._mic_q:
                    continue
                self._m["route_losses"] = self._m.get("route_losses", 0) + 1
                raise AudioUnavailable("AUDIO_MIC_STALLED", MIC_LOST_DETAIL, ROUTE_LOST_ACTION) from None
        return self._mic_q.popleft()

    def _sd(self):
        if self._backend is not None:
            return self._backend
        import sounddevice as sd
        return sd

    async def mic_chunks(self, rate: int, chunk_bytes: int):
        sd = self._sd()
        loop = asyncio.get_running_loop()
        self._mic_max_chunks = max(1, int(self.mic_max_s * rate * 2 / chunk_bytes))

        def cb(indata, frames, t, status):
            try:
                loop.call_soon_threadsafe(self._mic_push, bytes(indata))
            except RuntimeError:            # loop already closed during shutdown
                pass
        try:
            stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=chunk_bytes // 2,
                                       device=self.inp, callback=cb)
        except Exception as e:
            raise AudioUnavailable("AUDIO_MIC_UNAVAILABLE", f"cannot open the microphone: {e}",
                                   "check the mic and OS microphone permission") from e
        with stream:
            while self._open:
                chunk = await self._mic_get()
                if chunk is None:
                    return
                yield chunk

    # ------------------------------------------------------------------ speaker
    def enqueue(self, pcm: bytes) -> None:
        if not self._open or not pcm:
            return
        self._spk_q.append(pcm)
        self._spk_bytes += len(pcm)
        limit = int(self.speaker_max_s * self.rate * 2)
        while self._spk_bytes > limit and len(self._spk_q) > 1:
            old = self._spk_q.popleft()
            self._spk_bytes -= len(old)
            self._m["speaker_drops"] += 1
            self._m["speaker_dropped_bytes"] += len(old)
        self._m["speaker_max_bytes"] = max(self._m["speaker_max_bytes"], self._spk_bytes)
        self._spk_ready.set()

    def flush(self) -> int:
        """Discard all queued speaker audio (interruption/barge-in). Returns the bytes discarded."""
        n = self._spk_bytes
        self._spk_q.clear()
        self._spk_bytes = 0
        if n:
            self._m["speaker_flushes"] += 1
            self._m["speaker_flushed_bytes"] += n
        return n

    def queued_s(self) -> float:
        return self._spk_bytes / 2 / self.rate

    async def _spk_get(self, max_bytes: int) -> bytes | None:
        while not self._spk_q:
            if not self._open:
                return None
            self._spk_ready.clear()
            await self._spk_ready.wait()
        if not self._open:
            return None
        head = self._spk_q[0]
        if len(head) > max_bytes:                   # split big deltas so flush() acts within ~100 ms
            piece, self._spk_q[0] = head[:max_bytes], head[max_bytes:]
        else:
            piece = self._spk_q.popleft()
        self._spk_bytes -= len(piece)
        return piece

    async def run_player(self, rate: int) -> None:
        sd = self._sd()
        self.rate = rate
        piece = max(2, (rate // 10) * 2)            # 100 ms of PCM16 mono
        try:
            with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", device=self.out) as out:
                while True:
                    data = await self._spk_get(piece)
                    if data is None:
                        return
                    if self.on_output_level is not None:
                        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
                        level = min(1.0, float(np.sqrt(np.mean(samples ** 2))) * 5) if len(samples) else 0.0
                        await self.on_output_level(level)
                    await asyncio.to_thread(out.write, data)
        except AudioUnavailable:
            raise
        except Exception as e:
            if not self._open:                      # closing the session while the device went away: not an error
                return
            self._m["route_losses"] = self._m.get("route_losses", 0) + 1
            logs.warn("audio", "SPEAKER_LOST", "the speaker stopped accepting audio", error_type=type(e).__name__)
            raise AudioUnavailable("AUDIO_SPEAKER_UNAVAILABLE", SPEAKER_LOST_DETAIL, ROUTE_LOST_ACTION) from e

    # ------------------------------------------------------------------ lifecycle / health
    def stats(self) -> dict:
        m = dict(self._m)
        return {"mic_drops": m["mic_drops"], "mic_max_depth_s": round(m["mic_max_depth"] * self.mic_max_s
                                                                      / self._mic_max_chunks, 2),
                "mic_depth": len(self._mic_q),
                "speaker_drops": m["speaker_drops"],
                "speaker_dropped_s": round(m["speaker_dropped_bytes"] / 2 / self.rate, 2),
                "speaker_max_depth_s": round(m["speaker_max_bytes"] / 2 / self.rate, 2),
                "speaker_depth_s": round(self.queued_s(), 2),
                "route_losses": m.get("route_losses", 0),
                "speaker_flushes": m["speaker_flushes"],
                "speaker_flushed_s": round(m["speaker_flushed_bytes"] / 2 / self.rate, 2)}

    def close(self) -> None:
        """Idempotent: stop both streams and clear every buffer (no stale audio survives a session)."""
        self._open = False
        self._mic_q.clear()
        self._spk_q.clear()
        self._spk_bytes = 0
        self._mic_ready.set()
        self._spk_ready.set()


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
