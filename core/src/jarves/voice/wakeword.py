"""Hands-free wake word ("hey jarvis") — local, offline, no account or key.

Engine: pyopen-wakeword (OHF-Voice/rhasspy, Apache-2.0) — the openWakeWord models (melspectrogram -> embedding ->
"hey_jarvis" classifier) run through a TFLite C library bundled in the wheel (win_amd64, linux aarch64/x86_64).
The models ship inside the wheel, so nothing is downloaded at runtime and nothing leaves the machine.

Privacy: the listener keeps at most one partial 80 ms frame in memory, never writes audio to disk/logs and never
sends it anywhere. Only `capture_until_silence` returns audio (the utterance after the wake word, for STT).

Pure logic here; the Core owns the mic stream and wires `WakeListener` to it (pause while JARVES speaks/listens).
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

SR = 16000
FRAME_SAMPLES = 1280            # openWakeWord frame: 80 ms @ 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2  # int16 mono
BUILTIN_MODELS = ("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy", "okay_nabu")


class WakeWordUnavailable(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


@runtime_checkable
class WakeWordDetector(Protocol):
    name: str
    threshold: float

    def feed(self, pcm16_mono_16k: bytes) -> float | None:
        """Feed one 80 ms frame (int16 LE mono 16 kHz). Returns the score when it crosses `threshold`, else None."""
        ...

    def reset(self) -> None: ...


# --------------------------------------------------------------------------------------------- detectors


class OpenWakeWordDetector:
    """openWakeWord models via pyopen-wakeword. `model` is a built-in name or a path to a custom .tflite
    (e.g. a self-trained "hey jarves"). Load is lazy; call `await load()` to do it off the event loop."""

    blocking = True     # feed() does real inference (~a few ms) -> WakeListener runs it in a worker thread

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5) -> None:
        self.model, self.threshold = model, float(threshold)
        self.name = Path(model).stem if model.endswith(".tflite") else model
        self._features = None
        self._ww = None
        self.last_score = 0.0

    def _mod(self):
        try:
            import pyopen_wakeword
            return pyopen_wakeword
        except (ImportError, OSError) as e:
            raise WakeWordUnavailable("WAKE_NO_ENGINE", "wake word engine (pyopen-wakeword) is not installed",
                                      "install the voice extra: uv sync --extra voice") from e

    def _load_sync(self) -> None:
        if self._ww is not None:
            return
        m = self._mod()
        try:
            if self.model.endswith(".tflite"):
                ww = m.OpenWakeWord.from_model(self.model)
            elif self.model in BUILTIN_MODELS:
                ww = m.OpenWakeWord.from_builtin(m.Model(self.model))
            else:
                raise WakeWordUnavailable("WAKE_BAD_MODEL", f"unknown wake word model '{self.model}'",
                                          f"use one of {', '.join(BUILTIN_MODELS)} or a .tflite path")
            self._features = m.OpenWakeWordFeatures.from_builtin()
            self._ww = ww
        except WakeWordUnavailable:
            raise
        except Exception as e:      # missing tflite C lib for this platform, corrupt model, ...
            raise WakeWordUnavailable("WAKE_LOAD_FAILED", f"cannot load the wake word model: {e}",
                                      "check the platform is win_amd64 / linux aarch64 / linux x86_64") from e

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def check(self) -> tuple[bool, str]:
        try:
            self._load_sync()
            return True, f"wake word '{self.name}' (openWakeWord, threshold {self.threshold})"
        except WakeWordUnavailable as e:
            return False, e.message

    def feed(self, pcm16_mono_16k: bytes) -> float | None:
        self._load_sync()
        best = 0.0
        for emb in self._features.process_streaming(pcm16_mono_16k):
            for p in self._ww.process_streaming(emb):
                best = max(best, float(p))
        self.last_score = best
        return best if best >= self.threshold else None

    def reset(self) -> None:
        if self._ww is not None:
            self._features.reset()
            self._ww.reset()


class FakeDetector:
    """Test double: scores `score` on any frame that contains the int16 sample value `MARKER`."""

    MARKER = 12345
    blocking = False

    def __init__(self, threshold: float = 0.5, score: float = 0.9, name: str = "hey_jarvis") -> None:
        self.name, self.threshold, self.score = name, threshold, score
        self.frame_sizes: list[int] = []    # sizes only — never the audio itself
        self.resets = 0

    @classmethod
    def marker_chunk(cls, n: int = FRAME_SAMPLES) -> bytes:
        a = np.zeros(n, np.int16)
        a[n // 2] = cls.MARKER
        return a.tobytes()

    def feed(self, pcm16_mono_16k: bytes) -> float | None:
        self.frame_sizes.append(len(pcm16_mono_16k))
        hit = bool(np.any(np.frombuffer(pcm16_mono_16k, np.int16) == self.MARKER))
        s = self.score if hit else 0.0
        return s if s >= self.threshold else None

    def reset(self) -> None:
        self.resets += 1


class HeadWakeDetector:
    """A custom phrase trained by `jarvis wake train` (voice/wake_train.py): the same openWakeWord melspectrogram +
    embedding front end as the built-in models, then a small classifier head stored as .npz (numpy only, so it runs
    wherever pyopen-wakeword runs: win_amd64, linux aarch64 / Pi). Scores 16 consecutive embeddings (~2 s)."""

    blocking = True

    def __init__(self, path: str, threshold: float = 0.5) -> None:
        self.path, self.threshold = path, float(threshold)
        self.name = Path(path).stem
        self.head: dict | None = None
        self._features = None
        self._ring: np.ndarray | None = None
        self._n = 0
        self.last_score = 0.0

    def _load_sync(self) -> None:
        if self.head is not None:
            return
        from .wake_train import load_head
        try:
            self.head = load_head(self.path)
        except (OSError, ValueError, KeyError) as e:
            raise WakeWordUnavailable("WAKE_BAD_MODEL", f"cannot read the custom wake model {Path(self.path).name}",
                                      "train it again: jarvis wake train \"<phrase>\"") from e
        try:
            import pyopen_wakeword as m
            self._features = m.OpenWakeWordFeatures.from_builtin()
        except (ImportError, OSError) as e:
            self.head = None
            raise WakeWordUnavailable("WAKE_NO_ENGINE", "wake word engine (pyopen-wakeword) is not installed",
                                      "install the voice extra: uv sync --extra voice") from e
        self._ring = np.zeros((self.head["windows"], 96), np.float32)
        self.name = self.head.get("phrase") or self.name

    async def load(self) -> None:
        await asyncio.to_thread(self._load_sync)

    def check(self) -> tuple[bool, str]:
        try:
            self._load_sync()
            return True, f"custom wake phrase '{self.name}' (trained head, threshold {self.threshold})"
        except WakeWordUnavailable as e:
            return False, e.message

    def feed(self, pcm16_mono_16k: bytes) -> float | None:
        from .wake_train import head_score
        self._load_sync()
        best = 0.0
        for emb in self._features.process_streaming(pcm16_mono_16k):
            for row in emb.reshape(-1, 96):
                self._ring = np.roll(self._ring, -1, axis=0)
                self._ring[-1] = row
                self._n += 1
                if self._n >= len(self._ring):
                    best = max(best, head_score(self.head, self._ring[None])[0])
        self.last_score = float(best)
        return self.last_score if self.last_score >= self.threshold else None

    def reset(self) -> None:
        if self._features is not None:
            self._features.reset()
            self._ring[:] = 0
            self._n = 0


def make_detector(wake_word: str = "hey_jarvis", threshold: float = 0.5) -> WakeWordDetector:
    """Built-in name ("hey_jarvis"), an openWakeWord .tflite, or a `jarvis wake train` head (.npz)."""
    if wake_word.endswith(".npz"):
        return HeadWakeDetector(wake_word, threshold)
    return OpenWakeWordDetector(wake_word, threshold)


def make_wake_detector(v) -> WakeWordDetector:
    """The Core's wake pipeline from VoiceConfig (docs/reviews/SWV3_WAKE_REPORT.md):
    AGC (voice.wake_agc) -> wake model -> optional local ASR confirmation of the name (voice.wake_confirm).
    The confirm stage needs faster-whisper (voice extra); when it cannot load, `check()` fails and the caller falls
    back to the single-stage detector at voice.wake_threshold (and says so)."""
    from .wake_confirm import AutoGain, ConfirmingDetector, GainDetector, WhisperConfirm
    agc = AutoGain(initial_gain_db=getattr(v, "wake_gain_db", 0.0) or 0.0) if getattr(v, "wake_agc", False) else None
    if getattr(v, "wake_confirm", False):
        base = make_detector(v.wake_word, 1.01)          # the gate: only its raw score is read
        return ConfirmingDetector(base, WhisperConfirm(v.wake_confirm_model), accept=max(v.wake_threshold,
                                  v.wake_accept), gate=v.wake_gate, agc=agc)
    det = make_detector(v.wake_word, v.wake_threshold)
    return GainDetector(det, agc) if agc is not None else det


# --------------------------------------------------------------------------------------------- levels


def _as_float(chunk: bytes | np.ndarray) -> np.ndarray:
    if isinstance(chunk, (bytes, bytearray, memoryview)):
        b = bytes(chunk)
        return np.frombuffer(b[: len(b) - len(b) % 2], np.int16).astype(np.float32) / 32768.0
    a = np.asarray(chunk)
    return a.astype(np.float32) / 32768.0 if a.dtype == np.int16 else a.astype(np.float32)


def rms(chunk: bytes | np.ndarray) -> float:
    a = _as_float(chunk)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


def level_meter(chunk: bytes | np.ndarray, floor_db: float = -60.0) -> float:
    """Mic bar level 0..1: RMS in dBFS mapped linearly from `floor_db` (0) to 0 dBFS (1)."""
    r = rms(chunk)
    if r <= 0:
        return 0.0
    db = 20 * math.log10(r)
    return float(min(1.0, max(0.0, (db - floor_db) / -floor_db)))


# --------------------------------------------------------------------------------------------- listener


@dataclass
class WakeStats:
    frames: int = 0
    triggers: int = 0
    false_triggers: int = 0
    dropped_frames: int = 0     # skipped while paused / muted / cooling down
    cpu_ms_total: float = 0.0
    last_score: float = 0.0

    @property
    def avg_cpu_ms(self) -> float:
        return self.cpu_ms_total / self.frames if self.frames else 0.0

    def as_dict(self) -> dict:
        return {"frames": self.frames, "triggers": self.triggers, "false_triggers": self.false_triggers,
                "dropped_frames": self.dropped_frames, "avg_cpu_ms": round(self.avg_cpu_ms, 3),
                "last_score": round(self.last_score, 3)}


class WakeListener:
    """Feeds mic chunks to a detector and calls `await on_wake(score)` on a trigger.

    - any chunk size in; reframed to 80 ms frames for the model
    - `cooldown_s` after a trigger (no re-trigger on the tail of the same phrase)
    - `pause()` while JARVES is speaking/listening (half duplex: no self-trigger from the speaker); `resume()`
      resets the detector so stale model state can't fire
    - `mute()` = software mute: processing stops immediately and the buffered partial frame is discarded
    """

    def __init__(self, detector: WakeWordDetector, on_wake: Callable[[float], Awaitable[None]],
                 cooldown_s: float = 2.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.detector, self.on_wake, self.cooldown_s, self.clock = detector, on_wake, cooldown_s, clock
        self.stats = WakeStats()
        self._buf = bytearray()
        self._paused = False
        self._muted = False
        self._cool_until = 0.0
        self._need_reset = False

    # --- control (called by the Core) ---
    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def muted(self) -> bool:
        return self._muted

    @property
    def active(self) -> bool:
        return not (self._paused or self._muted)

    def pause(self) -> None:
        self._paused = True
        self._buf.clear()

    def resume(self) -> None:
        self._paused = False
        self._need_reset = True

    def mute(self) -> None:
        self._muted = True
        self._buf.clear()

    def unmute(self) -> None:
        self._muted = False
        self._need_reset = True

    def note_false_trigger(self) -> None:
        """Hook: the Core calls this when a wake led to no speech / the user said it wasn't for JARVES."""
        self.stats.false_triggers += 1

    # --- processing ---
    async def run(self, chunks: AsyncIterator[bytes]) -> None:
        """Consume int16 mono 16 kHz chunks until the iterator ends (or the task is cancelled)."""
        load = getattr(self.detector, "load", None)
        if load is not None:
            await load()
        try:
            async for chunk in chunks:
                await self.process(chunk)
        finally:
            self._buf.clear()

    async def process(self, chunk: bytes) -> float | None:
        """Process one mic chunk. Returns the trigger score if this chunk woke JARVES."""
        if not self.active:
            self.stats.dropped_frames += len(chunk) // FRAME_BYTES
            return None
        self._buf += chunk
        hit = None
        while len(self._buf) >= FRAME_BYTES and self.active:
            frame = bytes(self._buf[:FRAME_BYTES])
            del self._buf[:FRAME_BYTES]
            if self.clock() < self._cool_until:
                self.stats.dropped_frames += 1
                self._need_reset = True
                continue
            if self._need_reset:
                self.detector.reset()
                self._need_reset = False
            t0 = time.perf_counter()
            if getattr(self.detector, "blocking", False):
                score = await asyncio.to_thread(self.detector.feed, frame)
            else:
                score = self.detector.feed(frame)
            self.stats.cpu_ms_total += (time.perf_counter() - t0) * 1000
            self.stats.frames += 1
            self.stats.last_score = float(getattr(self.detector, "last_score", score or 0.0))
            if score is not None and self.active:
                self.stats.triggers += 1
                self._cool_until = self.clock() + self.cooldown_s
                self._need_reset = True
                self._buf.clear()
                hit = score
                await self.on_wake(score)
                break
        if not self.active:
            self._buf.clear()
        return hit


# --------------------------------------------------------------------------------------------- end of speech


@dataclass
class Capture:
    audio: np.ndarray = field(repr=False)   # float32 mono 16 kHz, for STT
    peak_level: float                       # level_meter units (0..1)
    avg_level: float
    speech_ms: int
    reason: str                             # "silence" | "max_len" | "no_speech" | "eof"

    @property
    def too_quiet(self) -> bool:
        """True when the mic is basically silent -> Core should say "can't hear you, check the mic level"."""
        return self.peak_level < 0.1


async def capture_until_silence(chunks: AsyncIterator[bytes], max_s: float = 10, silence_ms: int = 800,
                                min_speech_ms: int = 300, no_speech_s: float = 5.0,
                                rate: int = SR) -> Capture:
    """Record after the wake word until the user stops talking. Energy VAD on 30 ms frames with an adaptive
    noise floor: speech = RMS > max(3x floor (~+10 dB), -45 dBFS). Ends after `silence_ms` of silence once at
    least `min_speech_ms` of speech was heard, at `max_s`, or after `no_speech_s` with no speech at all."""
    vad = int(rate * 0.03)
    vad_b = vad * 2
    abs_min = 10 ** (-45 / 20)
    floor_cap = 10 ** (-50 / 20)
    floor = None
    buf = bytearray()
    out: list[np.ndarray] = []
    total = speech = trailing = 0
    peak = 0.0
    lvl_sum, lvl_n = 0.0, 0
    reason = "eof"
    max_n, sil_n, min_n, nos_n = int(max_s * rate), rate * silence_ms // 1000, rate * min_speech_ms // 1000, \
        int(no_speech_s * rate)
    done = False
    async for chunk in chunks:
        buf += chunk
        while len(buf) >= vad_b:
            f = _as_float(bytes(buf[:vad_b]))
            del buf[:vad_b]
            out.append(f)
            total += vad
            r = float(np.sqrt(np.mean(f * f)))
            lv = level_meter(f)
            peak = max(peak, lv)
            lvl_sum += lv
            lvl_n += 1
            if floor is None:   # capped so speech starting on frame 1 isn't taken as the noise floor
                floor = min(max(r, 1e-5), floor_cap)
            is_speech = r > max(3 * floor, abs_min)
            if is_speech:
                speech += vad
                trailing = 0
                floor = 0.995 * floor + 0.005 * r   # slow creep: steady loud noise stops counting as speech
            else:
                trailing += vad
                floor = 0.9 * floor + 0.1 * max(r, 1e-5)
            if speech >= min_n and trailing >= sil_n:
                reason, done = "silence", True
            elif total >= max_n:
                reason, done = "max_len", True
            elif speech < min_n and total >= nos_n:
                reason, done = "no_speech", True
            if done:
                break
        if done:
            break
    audio = np.concatenate(out) if out else np.zeros(0, np.float32)
    return Capture(audio=audio, peak_level=peak, avg_level=lvl_sum / lvl_n if lvl_n else 0.0,
                   speech_ms=speech * 1000 // rate, reason=reason)
