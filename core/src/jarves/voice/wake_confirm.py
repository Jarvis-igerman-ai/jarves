"""Wake reliability helpers, all local (no audio leaves the device, nothing is written to disk):

- `AutoGain`: slow digital AGC for quiet mics (a webcam mic 2-3 m away), so the wake model and the confirm stage see
  speech at a normal level. Never boosts the noise floor above ~-50 dBFS, never clips.
- `name_match`: does an ASR transcript contain "<greeting> Jarvis" (hey/hi/ok/hello + jarvis/jarves/..., or the
  Hebrew spelling)? "hey Travis" / "hey Charles" / a bare name do not match.
- `WhisperConfirm`: short local ASR (faster-whisper, already in the voice extra) of the last ~2 s.
- `ConfirmingDetector`: two-stage wake. Stage 1 = the openWakeWord model as a cheap gate (runs on every 80 ms frame).
  A score >= `accept` wakes at once; a score in [gate, accept) asks stage 2 (ASR) whether the name was said.
  Keeps a 2.4 s ring of audio in RAM only (needed for the ASR), overwritten continuously and dropped on reset.
- `SpeechMonitor`: notices speech-like audio that produced no wake score, so the Core can say
  "heard speech, no wake match: say 'Hey Jarvis'" (or "the mic is too quiet") instead of staying silent.
"""
from __future__ import annotations

import math
import re
import time
import unicodedata
from dataclasses import dataclass, field

import numpy as np

try:            # eagerly, before anything loads WinRT (voice/stt.py: faster-whisper after WinRT crashes the process)
    import faster_whisper as _fw  # noqa: F401
except ImportError:              # voice extra not installed: the confirm stage reports unavailable
    _fw = None

SR = 16000
FRAME_SAMPLES = 1280


def _db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-9))


# --------------------------------------------------------------------------------------------- AGC
class AutoGain:
    """Speech-level AGC on 80 ms int16 frames.

    noise floor: fast-down / slow-up tracker of the frame RMS. Speech level: frames >= floor + 9 dB update a peak
    envelope (fast attack, ~4 s release). gain = target / speech level, limited to [1, max_gain] and so that the
    noise floor stays below `max_floor_db`; changes are smoothed (no pumping inside a word)."""

    def __init__(self, target_db: float = -24.0, max_gain_db: float = 24.0, max_floor_db: float = -50.0,
                 initial_gain_db: float = 0.0) -> None:
        self.target_db, self.max_gain_db, self.max_floor_db = target_db, max_gain_db, max_floor_db
        self.floor_db = -70.0
        self.speech_db: float | None = None
        self.gain_db = float(initial_gain_db)

    def process(self, frame: bytes) -> bytes:
        a = np.frombuffer(frame, "<i2").astype(np.float32) / 32768.0
        if not a.size:
            return frame
        r = _db(float(np.sqrt(np.mean(a * a))))
        self.floor_db = r if r < self.floor_db else self.floor_db + min(0.05, r - self.floor_db)
        if r >= self.floor_db + 9:
            if self.speech_db is None or r > self.speech_db:
                self.speech_db = r if self.speech_db is None else self.speech_db + 0.5 * (r - self.speech_db)
            else:
                self.speech_db -= 0.02                               # ~4 s to forget a loud talker
        if self.speech_db is not None:
            want = min(self.target_db - self.speech_db, self.max_floor_db - self.floor_db, self.max_gain_db)
            want = max(0.0, want)
            self.gain_db += (want - self.gain_db) * (0.3 if want < self.gain_db else 0.05)
        if self.gain_db < 0.1:
            return frame
        g = 10 ** (self.gain_db / 20)
        peak = float(np.abs(a).max()) * g
        if peak > 0.98:                                              # hard limiter: never clip
            g *= 0.98 / peak
        return (np.clip(a * g, -1, 1) * 32767).astype("<i2").tobytes()

    def state(self) -> dict:
        return {"gain_db": round(self.gain_db, 1), "floor_db": round(self.floor_db, 1),
                "speech_db": None if self.speech_db is None else round(self.speech_db, 1)}


class GainDetector:
    """Any wake detector with `AutoGain` in front of it."""

    blocking = True

    def __init__(self, base, agc: AutoGain | None = None) -> None:
        self.base, self.agc = base, agc or AutoGain()
        self.name, self.threshold = base.name, base.threshold
        self.last_score = 0.0

    async def load(self) -> None:
        if hasattr(self.base, "load"):
            await self.base.load()

    def check(self):
        return self.base.check() if hasattr(self.base, "check") else (True, "")

    def feed(self, pcm: bytes):
        r = self.base.feed(self.agc.process(pcm))
        self.last_score = float(getattr(self.base, "last_score", r or 0.0))
        return r

    def reset(self) -> None:
        self.base.reset()


# --------------------------------------------------------------------------------------------- name matching
GREETINGS = {"hey", "hi", "hay", "hei", "heh", "hai", "ok", "okay", "oke", "okey", "hello", "hallo", "yo", "a",
             "היי", "הי", "הי", "אוקיי", "אוקי", "הלו", "שלום"}
# Hebrew/Arabic-accent variants too: y- for the ג'/J ("Yarvis"), b/w for the v ("Jarbis"), -sh/-z endings.
_NAME_LATIN = re.compile(r"^(j|g|y|dj|dzh|zh)[ae]+h?r?[vwb][aeiyu]+(s+|z|sh)$")
_NAME_HE = re.compile(r"^(ג'|ג׳|ג|דז'|ז')א?ר[וב]ו?י?ס$")
_JOINED = re.compile(r"^(hey|hi|ok|okay|hello)(j[ae]rv[aeiy]s)$")


def _tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text.lower())
    t = t.replace("׳", "'").replace("’", "'").replace("`", "'")
    t = re.sub(r"[^\w'֐-׿]+", " ", t)
    out = []
    for w in t.split():
        if not any("֐" <= c <= "׿" for c in w):
            w = w.replace("'", "")
            if w.endswith("s") and w[:-1].endswith("s"):          # "jarviss" (possessive/ASR doubling)
                w = w[:-1]
        m = _JOINED.match(w)
        out += [m.group(1), m.group(2)] if m else [w]
    return out


def is_name(w: str) -> bool:
    return bool(_NAME_LATIN.match(w) or _NAME_HE.match(w)) and w not in {"jarvie", "garvey"}


def name_match(text: str, require_greeting: bool = True) -> bool:
    """'hey jarvis', 'Hi, Javis.', 'ok jarves', 'hey Yarvis', 'היי ג'רוויס' -> True; 'hey Travis', 'Jarvis' (bare) -> False."""
    toks = _tokens(text)
    for i, w in enumerate(toks):
        if is_name(w):
            if not require_greeting:
                return True
            if any(t in GREETINGS for t in toks[max(0, i - 2):i]):
                return True
    return False


# --------------------------------------------------------------------------------------------- ASR confirm
class WhisperConfirm:
    """Local faster-whisper transcription of a ~2 s window, English decoding (a Hebrew speaker's "היי ג'רוויס"
    comes out as "Hey Jarvis"; Hebrew script is matched too). No prompt: an initial prompt with the name would bias
    the model towards hearing it."""

    def __init__(self, model: str = "base.en", compute: str = "int8", language: str | None = "en",
                 cpu_threads: int = 0) -> None:
        self.model_name, self.compute, self.language, self.cpu_threads = model, compute, language, cpu_threads
        self._m = None
        self.last_text = ""
        self.last_ms = 0.0

    def load(self) -> None:
        if self._m is None:
            from faster_whisper import WhisperModel
            self._m = WhisperModel(self.model_name, device="cpu", compute_type=self.compute,
                                   cpu_threads=self.cpu_threads)

    def check(self) -> tuple[bool, str]:
        try:
            self.load()
            return True, f"local ASR confirm ({self.model_name})"
        except Exception as e:          # not installed / model not downloadable
            return False, f"local ASR confirm unavailable: {type(e).__name__}: {str(e)[:80]}"

    def transcribe(self, audio: np.ndarray) -> str:
        self.load()
        t = time.perf_counter()
        segs, _ = self._m.transcribe(audio.astype(np.float32), language=self.language, beam_size=1,
                                     vad_filter=False, condition_on_previous_text=False, without_timestamps=True,
                                     temperature=0.0)
        self.last_text = " ".join(s.text.strip() for s in segs).strip()
        self.last_ms = (time.perf_counter() - t) * 1000
        return self.last_text

    def __call__(self, audio: np.ndarray) -> bool:
        return name_match(self.transcribe(audio))


@dataclass
class ConfirmStats:
    frames: int = 0
    fast_accepts: int = 0
    asks: int = 0
    confirmed: int = 0
    rejected: int = 0
    asr_ms_total: float = 0.0
    last_text: str = field(default="", repr=False)

    def as_dict(self) -> dict:
        return {"fast_accepts": self.fast_accepts, "asr_asks": self.asks, "confirmed": self.confirmed,
                "rejected": self.rejected, "asr_ms_avg": round(self.asr_ms_total / max(1, self.asks), 1),
                "asr_asks_per_hour": round(self.asks / max(1e-9, self.frames * 0.08 / 3600), 1)}


class ConfirmingDetector:
    """Two-stage wake: openWakeWord gate + local ASR confirmation of the name (see module doc).

    `threshold` (what the listener/near-miss logic compares with) is `accept`. Stage-2 latency: the window is
    checked `settle_frames` x 80 ms after the gate opened (the end of the phrase), then one ASR call."""

    blocking = True

    def __init__(self, base, confirm, *, accept: float = 0.5, gate: float = 0.02, window_s: float = 2.0,
                 settle_frames: int = 4, agc: AutoGain | None = None) -> None:
        self.base, self.confirm, self.agc = base, confirm, agc
        self.accept, self.gate, self.settle_frames = float(accept), float(gate), settle_frames
        self.threshold = self.accept
        self.name = getattr(base, "name", "hey_jarvis")
        self._ring = np.zeros(int(window_s * SR), np.int16)
        self._filled = 0
        self._pending = 0
        self._pend_score = 0.0
        self._quiet_until = 0          # frames: no new ask right after one (the same phrase)
        self._frame = 0
        self.last_score = 0.0
        self.st = ConfirmStats()
        self.last_confirm: dict | None = None

    async def load(self) -> None:
        import asyncio
        if hasattr(self.base, "load"):
            await self.base.load()
        if hasattr(self.confirm, "load"):
            await asyncio.to_thread(self.confirm.load)

    def check(self) -> tuple[bool, str]:
        ok, msg = self.base.check() if hasattr(self.base, "check") else (True, "")
        if not ok:
            return ok, msg
        c_ok, c_msg = self.confirm.check() if hasattr(self.confirm, "check") else (True, "ASR confirm")
        if not c_ok:
            return False, c_msg
        return True, f"{msg}; two-stage: gate {self.gate}, instant {self.accept}, {c_msg}"

    def stats(self) -> dict:
        return self.st.as_dict()

    def _push(self, pcm: bytes) -> None:
        a = np.frombuffer(pcm, "<i2")
        n = len(a)
        self._ring = np.roll(self._ring, -n)
        self._ring[-n:] = a
        self._filled = min(len(self._ring), self._filled + n)

    def _ask(self) -> bool:
        audio = self._ring[-self._filled:].astype(np.float32) / 32768.0
        t = time.perf_counter()
        ok = bool(self.confirm(audio))
        ms = (time.perf_counter() - t) * 1000
        self.st.asks += 1
        self.st.asr_ms_total += ms
        self.st.confirmed += ok
        self.st.rejected += not ok
        self.last_confirm = {"ok": ok, "ms": round(ms), "score": round(self._pend_score, 3),
                             "text": getattr(self.confirm, "last_text", "")}
        return ok

    def feed(self, pcm: bytes) -> float | None:
        if self.agc is not None:
            pcm = self.agc.process(pcm)
        self._frame += 1
        self.st.frames += 1
        self._push(pcm)
        self.base.feed(pcm)
        s = float(getattr(self.base, "last_score", 0.0))
        self.last_score = s
        if s >= self.accept:
            self._pending = 0
            self.st.fast_accepts += 1
            return s
        if self._pending:
            self._pend_score = max(self._pend_score, s)
            self._pending -= 1
            if self._pending == 0:
                self._quiet_until = self._frame + 12
                if self._ask():
                    return max(self.accept, self._pend_score)
            return None
        if s >= self.gate and self._frame >= self._quiet_until:
            self._pending, self._pend_score = self.settle_frames, s
        return None

    def reset(self) -> None:
        self.base.reset()
        self._ring[:] = 0
        self._filled = 0
        self._pending = 0


# --------------------------------------------------------------------------------------------- speech, no match
@dataclass
class SpeechEvent:
    kind: str            # "no_match" | "too_quiet"
    speech_ms: int
    peak_score: float
    level_db: float
    floor_db: float


class SpeechMonitor:
    """Energy VAD over the wake mic's 80 ms frames. When a speech burst (>= `min_ms`) ends and the wake score never
    reached `min_score` during it, `feed` returns a SpeechEvent ('too_quiet' when the speech was under
    `quiet_db` or barely above the noise floor). Rate-limited by the caller."""

    def __init__(self, min_ms: int = 400, min_score: float = 0.05, quiet_db: float = -45.0) -> None:
        self.min_ms, self.min_score, self.quiet_db = min_ms, min_score, quiet_db
        self.floor_db = -70.0
        self._speech_ms = 0
        self._tail_ms = 0
        self._peak_score = 0.0
        self._peak_db = -120.0
        self.bursts = 0
        self.last_burst_db: float | None = None
        self.frames = 0

    def feed(self, frame: bytes, score: float) -> SpeechEvent | None:
        self.frames += 1
        a = np.frombuffer(frame, "<i2").astype(np.float32) / 32768.0
        if not a.size:
            return None
        r = _db(float(np.sqrt(np.mean(a * a))))
        speech = r >= max(self.floor_db + 9, -60.0)
        self.floor_db = r if r < self.floor_db else self.floor_db + min(0.05, r - self.floor_db)
        ms = int(len(a) * 1000 / SR)
        if speech:
            self._speech_ms += ms
            self._tail_ms = 0
            self._peak_db = max(self._peak_db, r)
        elif self._speech_ms:
            self._tail_ms += ms
        if self._speech_ms:
            self._peak_score = max(self._peak_score, score)
        if self._speech_ms and self._tail_ms >= 400:
            ev = None
            self.bursts += 1
            self.last_burst_db = round(self._peak_db, 1)
            if self._speech_ms >= self.min_ms and self._peak_score < self.min_score:
                quiet = self._peak_db < self.quiet_db or self._peak_db - self.floor_db < 12
                ev = SpeechEvent("too_quiet" if quiet else "no_match", self._speech_ms, round(self._peak_score, 3),
                                 round(self._peak_db, 1), round(self.floor_db, 1))
            self._speech_ms = self._tail_ms = 0
            self._peak_score, self._peak_db = 0.0, -120.0
            return ev
        return None
