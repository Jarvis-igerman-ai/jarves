"""OpenAI Realtime API via the Agents SDK (RealtimeAgent): alternate live voice + cloud speech for text replies.

- RealtimeVoice: speech-to-speech conversation with gpt-realtime-2.1 (alternative to GPT-Live, e.g. if its Hebrew
  is better for this user), same Skill tools, server-side barge-in (audio_interrupted).
- RealtimeSpeaker: speaks a given text with gpt-realtime-2.1-mini. OpenAI's deprecation notice names
  gpt-realtime-2.1-mini as the replacement for tts-1 / gpt-4o-mini-tts (removal 2027-01-06).
Requires OPENAI_API_KEY. Docs checked 2026-10-18.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import numpy as np

from .. import logs

RATE = 24000
CHUNK_BYTES = 4800


def _runner(model: str, voice: str, language: str, instructions: str, tools: list, audio_only_out: bool = True):
    from agents.realtime import RealtimeAgent, RealtimeRunner
    agent = RealtimeAgent(name="JARVES", instructions=instructions, tools=tools)
    return RealtimeRunner(agent, config={
        "tracing_disabled": True,
        "model_settings": {
            "model_name": model, "voice": voice, "modalities": ["audio"],
            "input_audio_format": "pcm16", "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "gpt-live-transcribe", "language": language},
            "turn_detection": {"type": "semantic_vad", "interrupt_response": True},
        }})


class RealtimeVoice:
    def __init__(self, model: str, instructions: Callable[[], str], tools: list, language: str = "he",
                 voice: str = "marin", half_duplex: bool = True, audio_io=None,
                 on_state: Callable[[str], Awaitable[None]] | None = None,
                 on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
                 on_usage: Callable[[float], None] | None = None, max_session_s: float = 600) -> None:
        self.model, self.instructions, self.tools = model, instructions, tools
        self.language, self.voice, self.half_duplex = language, voice, half_duplex
        self.audio_io, self.on_state, self.on_transcript, self.on_usage = audio_io, on_state, on_transcript, on_usage
        self.max_session_s = max_session_s
        self.session = None
        self._tasks: list[asyncio.Task] = []
        self._speaking_until = 0.0
        self._t0 = 0.0
        self.running = False

    async def _state(self, s: str) -> None:
        if self.on_state:
            await self.on_state(s)

    async def start(self) -> None:
        runner = _runner(self.model, self.voice, self.language, self.instructions(), self.tools)
        self.session = await runner.run()
        await self.session.enter()
        self.running, self._t0 = True, time.monotonic()
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._events()), loop.create_task(self._watchdog())]
        if self.audio_io is not None:
            self._tasks += [loop.create_task(self._mic()), loop.create_task(self.audio_io.run_player(RATE))]
        logs.info("realtime", "RT_STARTED", f"realtime session started ({self.model})")
        await self._state("listening")

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        if self.session is not None:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
        if self.audio_io is not None:
            self.audio_io.close()
        if self.on_usage:
            self.on_usage(time.monotonic() - self._t0)
        logs.info("realtime", "RT_STOPPED", "realtime session stopped")
        await self._state("idle")

    async def _watchdog(self) -> None:
        await asyncio.sleep(self.max_session_s)
        asyncio.get_running_loop().create_task(self.stop())

    async def _mic(self) -> None:
        silence = bytes(CHUNK_BYTES)
        async for chunk in self.audio_io.mic_chunks(RATE, CHUNK_BYTES):
            gated = self.half_duplex and time.monotonic() < self._speaking_until + 0.4
            await self.session.send_audio(silence if gated else chunk)

    async def _events(self) -> None:
        async for ev in self.session:
            t = ev.type
            if t == "audio":
                if time.monotonic() > self._speaking_until:
                    await self._state("speaking")
                data = ev.audio.data
                self._speaking_until = max(self._speaking_until, time.monotonic()) + len(data) / 2 / RATE
                if self.audio_io is not None:
                    self.audio_io.enqueue(data)
            elif t == "audio_interrupted":
                if self.audio_io is not None:
                    self.audio_io.flush()
                self._speaking_until = 0.0
                logs.info("realtime", "RT_BARGE_IN", "user interrupted JARVES")
                await self._state("listening")
            elif t == "audio_end":
                await self._state("listening")
            elif t == "tool_start":
                await self._state("thinking")
            elif t == "history_added" and self.on_transcript:
                item = ev.item
                for c in getattr(item, "content", []) or []:
                    txt = getattr(c, "transcript", None) or getattr(c, "text", None)
                    if txt:
                        await self.on_transcript(getattr(item, "role", ""), txt)
            elif t == "error":
                logs.error("realtime", "RT_ERROR", f"realtime error: {str(ev.error)[:200]}")
                await self._state("error")


class RealtimeSpeaker:
    """Cloud TTS: one short realtime session that reads the given text aloud (no tools)."""
    name = "openai-realtime"
    quality = "cloud"

    def __init__(self, model: str = "gpt-realtime-2.1-mini", voice: str = "marin", language: str = "he",
                 timeout_s: float = 30) -> None:
        self.model, self.voice, self.language, self.timeout = model, voice, language, timeout_s

    def check(self) -> tuple[bool, str]:
        import os
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return False, "OPENAI_API_KEY not set"
        return True, f"{self.model} voice {self.voice}"

    async def synth(self, text: str) -> tuple[np.ndarray, int]:
        from .tts import TTSUnavailable
        instr = ("You are a text-to-speech voice. Read the user's message aloud EXACTLY as written, in natural, warm "
                 "Israeli Hebrew (or the message's language). Do not add, answer or comment on anything.")
        chunks: list[bytes] = []
        try:
            runner = _runner(self.model, self.voice, self.language, instr, [])
            async with await runner.run() as session:
                await session.send_message(text)

                async def collect():
                    async for ev in session:
                        if ev.type == "audio":
                            chunks.append(ev.audio.data)
                        elif ev.type == "audio_end" and chunks:
                            return
                        elif ev.type == "error":
                            raise TTSUnavailable("TTS_FAILED", f"realtime speech error: {str(ev.error)[:150]}")
                await asyncio.wait_for(collect(), self.timeout)
        except TTSUnavailable:
            raise
        except asyncio.TimeoutError as e:
            raise TTSUnavailable("TTS_TIMEOUT", "cloud speech timed out", "check the internet") from e
        except Exception as e:
            raise TTSUnavailable("TTS_OFFLINE", f"cloud speech unavailable ({type(e).__name__})",
                                 "check OPENAI_API_KEY and the internet") from e
        pcm = b"".join(chunks)
        return np.frombuffer(pcm, "<i2").astype(np.float32) / 32768.0, RATE
