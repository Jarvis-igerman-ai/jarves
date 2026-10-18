"""Speech for replies outside a live voice session (typed questions, spoken command confirmations).

Quality order (auto): OpenAI gpt-realtime-2.1-mini speech (cloud; OpenAI's named successor to its TTS models)
-> local FALLBACK only: WinRT OneCore 'Microsoft Asaf' (Windows) / espeak-ng (Pi, robotic).
"""
from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import sys
import wave
from contextlib import aclosing

import numpy as np

from .. import logs


class TTSUnavailable(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


def wav_to_np(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise TTSUnavailable("TTS_BAD_AUDIO", f"unsupported sample width {width}")
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, rate


class WinRTTTS:
    name = "winrt"
    quality = "local-fallback"

    def __init__(self, lang_hint: str = "he-IL") -> None:
        self.lang = lang_hint.lower()

    def _voices(self):
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        return [v for v in SpeechSynthesizer.all_voices if v.language.lower().startswith(self.lang[:2])]

    def check(self) -> tuple[bool, str]:
        try:
            voices = self._voices()
        except Exception as e:
            return False, f"WinRT speech not available: {e}"
        if not voices:
            return False, f"no {self.lang} voice installed (Settings > Time & language > Speech)"
        return True, f"voice {voices[0].display_name}"

    async def synth(self, text: str) -> tuple[np.ndarray, int]:
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        from winrt.windows.storage.streams import Buffer, InputStreamOptions
        voices = self._voices()
        if not voices:
            raise TTSUnavailable("TTS_NO_VOICE", f"no {self.lang} voice installed")
        s = SpeechSynthesizer()
        s.voice = voices[0]
        stream = await s.synthesize_text_to_stream_async(text)
        buf = Buffer(stream.size)
        await stream.read_async(buf, stream.size, InputStreamOptions.NONE)
        return wav_to_np(bytes(memoryview(buf)[: buf.length]))


class EspeakTTS:
    name = "espeak"
    quality = "local-fallback"

    def __init__(self, lang: str = "he") -> None:
        self.lang = lang[:2]
        self.exe = shutil.which("espeak-ng") or shutil.which("espeak")

    def check(self) -> tuple[bool, str]:
        return (True, f"{self.exe} -v {self.lang}") if self.exe else (False, "espeak-ng not installed (apt install espeak-ng)")

    async def synth(self, text: str) -> tuple[np.ndarray, int]:
        if not self.exe:
            raise TTSUnavailable("TTS_NO_ENGINE", "espeak-ng not installed", "apt install espeak-ng")
        r = await asyncio.to_thread(subprocess.run, [self.exe, "-v", self.lang, "--stdout", text],
                                    capture_output=True, timeout=30)
        if r.returncode != 0:
            raise TTSUnavailable("TTS_FAILED", "espeak-ng failed")
        return wav_to_np(r.stdout)


class MeteredTTS:
    """Cloud engine behind the shared Budget: 1 ai_requests unit reserved per synth, committed after the attempt
    (success or failure). Over budget -> TTSUnavailable("TTS_BUDGET") without contacting the provider."""

    def __init__(self, engine, budget) -> None:
        self.engine, self.budget = engine, budget
        self.name, self.quality = engine.name, engine.quality

    def check(self) -> tuple[bool, str]:
        return self.engine.check()

    async def synth(self, text: str) -> tuple[np.ndarray, int]:
        from ..budget import BudgetExceeded
        try:
            res = self.budget.reserve("ai_requests")
        except BudgetExceeded as e:
            raise TTSUnavailable("TTS_BUDGET", f"cloud speech skipped: {e}", e.action) from e
        try:
            return await self.engine.synth(text)
        finally:
            res.commit()

    async def stream(self, text: str):
        from ..budget import BudgetExceeded
        try:
            res = self.budget.reserve("ai_requests")
        except BudgetExceeded as e:
            raise TTSUnavailable("TTS_BUDGET", f"cloud speech skipped: {e}", e.action) from e
        try:
            if hasattr(self.engine, "stream"):
                async with aclosing(self.engine.stream(text)) as source:
                    async for chunk in source:
                        yield chunk
            else:
                yield await self.engine.synth(text)
        finally:
            res.commit()


def _metered(engines: list, budget) -> list:
    if budget is None:
        return engines
    return [MeteredTTS(e, budget) if e.quality == "cloud" and not isinstance(e, MeteredTTS) else e for e in engines]


def tts_chain(vc, allow_cloud: bool = True, budget=None) -> list:
    """Engines in quality order for VoiceConfig.tts_engine. allow_cloud=False (ChatGPT-plan-only connection)
    keeps speech local so nothing is billed to an API key. budget: the shared Budget meters cloud speech."""
    from .realtime import RealtimeSpeaker
    cloud = [RealtimeSpeaker(vc.speech_model, vc.realtime_voice, vc.language)] if allow_cloud else []
    local = ([WinRTTTS(vc.tts_voice_hint)] if sys.platform == "win32" else []) + [EspeakTTS(vc.language)]
    return _metered({"auto": cloud + local, "openai": cloud, "local": local, "none": []}[vc.tts_engine], budget)


def make_tts(cfg, budget) -> "FallbackTTS":
    """THE way to build speech output (Core and `jarvis say`, F2): honours the user's connection mode and meters
    cloud speech on the shared Budget. ai.connection = "chatgpt" never uses billable API speech, even if an
    OPENAI_API_KEY is present; ai.provider = "offline" is local only too."""
    allow_cloud = cfg.ai.provider == "openai" and cfg.ai.connection != "chatgpt"
    return FallbackTTS(tts_chain(cfg.voice, allow_cloud=allow_cloud, budget=budget), budget=budget)


class FallbackTTS:
    """Tries engines in quality order; if the cloud engine fails, that reply uses the next one.
    With a budget, cloud engines are metered; once over budget every cloud engine is skipped for that reply
    and a local engine speaks (TTS_BUDGET) - never another paid provider."""

    def __init__(self, engines: list, budget=None) -> None:
        self.engines = _metered(engines, budget)
        self.budget = budget
        self.usable: list = []
        self.last_used = ""

    def check(self) -> tuple[bool, str, str]:
        self.usable, notes = [], []
        for e in self.engines:
            ok, d = e.check()
            if ok:
                self.usable.append(e)
            notes.append(f"{e.name}: {d}")
        if not self.usable:
            return False, "none", "; ".join(notes) or "speech disabled in config"
        return True, self.usable[0].quality, "; ".join(notes)

    async def synth(self, text: str) -> tuple[np.ndarray, int]:
        err = None
        over_budget = False
        for e in self.usable or self.engines:
            if over_budget and e.quality == "cloud":
                continue
            try:
                out = await e.synth(text)
                if self.last_used != e.name:
                    logs.info("tts", "TTS_ENGINE", f"speaking with {e.name} ({e.quality})")
                self.last_used = e.name
                return out
            except TTSUnavailable as x:
                err = x
                over_budget = over_budget or x.code == "TTS_BUDGET"
                logs.warn("tts", x.code, f"{e.name}: {x.message}", action=x.action)
        raise err or TTSUnavailable("TTS_NONE", "no speech engine available", "set OPENAI_API_KEY")

    async def stream(self, text: str):
        """Fallback only before the first audio; never replay a partially spoken reply."""
        err, over_budget = None, False
        for e in self.usable or self.engines:
            if over_budget and e.quality == "cloud":
                continue
            emitted = False
            try:
                if hasattr(e, "stream"):
                    async with aclosing(e.stream(text)) as source:
                        async for chunk in source:
                            emitted = True
                            if self.last_used != e.name:
                                logs.info("tts", "TTS_ENGINE", f"speaking with {e.name} ({e.quality})")
                            self.last_used = e.name
                            yield chunk
                else:
                    chunk = await e.synth(text)
                    emitted = True
                    if self.last_used != e.name:
                        logs.info("tts", "TTS_ENGINE", f"speaking with {e.name} ({e.quality})")
                    self.last_used = e.name
                    yield chunk
                return
            except TTSUnavailable as e_error:
                if emitted:
                    raise
                err = e_error
                over_budget = over_budget or err.code == "TTS_BUDGET"
                logs.warn("tts", err.code, f"{e.name}: {err.message}", action=err.action)
        raise err or TTSUnavailable("TTS_NONE", "no speech engine available", "set OPENAI_API_KEY")
