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


def tts_chain(vc) -> list:
    """Engines in quality order for VoiceConfig.tts_engine."""
    from .realtime import RealtimeSpeaker
    cloud = RealtimeSpeaker(vc.speech_model, vc.voice, vc.language)
    local = ([WinRTTTS(vc.tts_voice_hint)] if sys.platform == "win32" else []) + [EspeakTTS(vc.language)]
    return {"auto": [cloud] + local, "openai": [cloud], "local": local, "none": []}[vc.tts_engine]


class FallbackTTS:
    """Tries engines in quality order; if the cloud engine fails, that reply uses the next one."""

    def __init__(self, engines: list) -> None:
        self.engines = engines
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
        for e in self.usable or self.engines:
            try:
                out = await e.synth(text)
                if self.last_used != e.name:
                    logs.info("tts", "TTS_ENGINE", f"speaking with {e.name} ({e.quality})")
                self.last_used = e.name
                return out
            except TTSUnavailable as x:
                err = x
                logs.warn("tts", x.code, f"{e.name}: {x.message}", action=x.action)
        raise err or TTSUnavailable("TTS_NONE", "no speech engine available", "set OPENAI_API_KEY")
