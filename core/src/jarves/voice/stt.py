"""Offline speech-to-text with faster-whisper (CTranslate2). Hebrew default: ivrit-ai fine-tuned Whisper.

Models download once from Hugging Face into the local cache; afterwards no network is needed.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np

from .. import logs


class STTUnavailable(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


class WhisperSTT:
    def __init__(self, model: str, fallback: str | None = "small", compute: str = "int8", language: str = "he",
                 cpu_threads: int = 0) -> None:
        self.model_name, self.fallback, self.compute, self.language = model, fallback, compute, language
        self.cpu_threads = cpu_threads
        self._model = None
        self.loaded_name: str | None = None
        self._loading: asyncio.Task | None = None
        self.error: STTUnavailable | None = None

    def _load_sync(self):
        from faster_whisper import WhisperModel
        last = None
        for name in [self.model_name] + ([self.fallback] if self.fallback else []):
            try:
                t = time.time()
                m = WhisperModel(name, device="cpu", compute_type=self.compute, cpu_threads=self.cpu_threads)
                logs.info("stt", "STT_LOADED", f"model {name} loaded", seconds=round(time.time() - t, 1))
                self.loaded_name = name
                return m
            except Exception as e:     # download/network/disk errors
                last = e
                logs.warn("stt", "STT_LOAD_FAILED", f"model {name} failed to load: {type(e).__name__}: {str(e)[:150]}")
        raise STTUnavailable("STT_MODEL_UNAVAILABLE", f"no speech model could be loaded ({last})",
                             "connect to the internet once so the model can download, or set voice.stt_model")

    async def ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self._loading is None:
            self._loading = asyncio.create_task(asyncio.to_thread(self._load_sync))
        try:
            self._model = await self._loading
            self.error = None
        except STTUnavailable as e:
            self.error = e
            self._loading = None
            raise

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        segs, _info = self._model.transcribe(audio, language=self.language, beam_size=1, vad_filter=True,
                                             condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segs).strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < 16000 * 0.3:
            return ""
        await self.ensure_loaded()
        t = time.time()
        text = await asyncio.to_thread(self._transcribe_sync, audio.astype(np.float32))
        logs.info("stt", "STT_DONE", "transcribed", audio_s=round(len(audio) / 16000, 1),
                  seconds=round(time.time() - t, 2), text=text)
        return text
