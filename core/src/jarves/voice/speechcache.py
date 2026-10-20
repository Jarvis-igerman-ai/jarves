"""Disk cache for FIXED phrases only (the start-up greeting): the cloud voice needs ~2 s (13 s on a cold, busy PC),
a cached copy plays at once. Never used for user content: keys are the phrase + voice + model, files hold only
the generated speech of that fixed sentence."""
from __future__ import annotations

import hashlib
import wave
from pathlib import Path

import numpy as np


class SpeechCache:
    def __init__(self, home: Path) -> None:
        self.dir = home / "cache"

    @staticmethod
    def key(text: str, *parts: str) -> str:
        return hashlib.sha256("\n".join((text, *parts)).encode("utf-8")).hexdigest()[:24]

    def _path(self, key: str) -> Path:
        return self.dir / f"speech_{key}.wav"

    def get(self, key: str) -> tuple[np.ndarray, int] | None:
        try:
            with wave.open(str(self._path(key)), "rb") as w:
                rate, raw = w.getframerate(), w.readframes(w.getnframes())
            a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
            return (a, rate) if len(a) > rate // 4 else None
        except (OSError, EOFError, wave.Error):
            return None

    def put(self, key: str, samples: np.ndarray, rate: int) -> None:
        if len(samples) < rate // 4:
            return                                  # too short to be a real phrase: do not cache a failure
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        tmp.replace(self._path(key))
