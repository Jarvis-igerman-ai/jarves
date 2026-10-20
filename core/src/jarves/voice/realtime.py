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
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import numpy as np

from .. import logs
from .live import ERROR_ACTIONS, GENERIC_ACTION, SessionLifecycle, error_info, is_fatal

if TYPE_CHECKING:
    from ..budget import Budget

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


class RealtimeVoice(SessionLifecycle):
    """Lifecycle (CR-02) is shared with LiveVoice: one idempotent finalize for user stop, remote close (the
    session's event stream ends), transport exception, task crash and the watchdog. Usage = wall-clock seconds."""
    _log_area = "realtime"

    def __init__(self, model: str, instructions: Callable[[], str], tools: list, language: str = "he",
                 voice: str = "marin", half_duplex: bool = True, audio_io=None,
                 on_state: Callable[[str], Awaitable[None]] | None = None,
                 on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
                 on_usage: Callable[[float], None] | None = None, max_session_s: float = 600, *,
                 on_end: Callable[[str], Awaitable[None]] | None = None,
                 on_health: Callable[[str, str, str], Any] | None = None,
                 budget: "Budget | None" = None, runner_factory: Callable | None = None,
                 idle_disconnect_s: float = 180) -> None:
        self.model, self.instructions, self.tools = model, instructions, tools
        self.language, self.voice, self.half_duplex = language, voice, half_duplex
        self.audio_io, self.on_state, self.on_transcript, self.on_usage = audio_io, on_state, on_transcript, on_usage
        self.max_session_s = max_session_s
        self.idle_disconnect_s = idle_disconnect_s
        self._runner_factory = runner_factory or _runner
        self._init_lifecycle(on_end, on_health, budget)
        self.session = None
        self._speaking_until = 0.0
        self._t0 = 0.0
        self._stats = {"barge_ins": 0, "errors": 0}

    async def _state(self, s: str) -> None:
        if self.on_state:
            await self.on_state(s)

    def _usage_seconds(self) -> float:
        return time.monotonic() - self._t0 if self._t0 else 0.0

    async def start(self) -> None:
        if self._active:
            if self._end_task is None:
                raise RuntimeError("realtime session already running")
            await self.wait_ended()
        self._reset_lifecycle()
        self._speaking_until, self._t0 = 0.0, 0.0
        self._stats = {"barge_ins": 0, "errors": 0}
        try:
            runner = self._runner_factory(self.model, self.voice, self.language, self.instructions(), self.tools)
            self.session = await runner.run()
            await self.session.enter()
        except BaseException as e:
            await self.stop(f"error:{getattr(e, 'code', None) or type(e).__name__}")
            raise
        self.running, self._t0 = True, time.monotonic()
        self._spawn(self._events(), "events")
        self._spawn(self._watchdog(), "watchdog")
        self._spawn(self._idle_watchdog(), "idle")
        if self.audio_io is not None:
            self._spawn(self._mic(), "mic")
            self._spawn(self.audio_io.run_player(RATE), "player")
        logs.info("realtime", "RT_STARTED", "realtime session started", model=self.model)
        await self._health("ok", f"realtime voice connected ({self.model})", "")
        await self._state("listening")

    async def _release(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            await session.close()

    async def _watchdog(self) -> None:
        await asyncio.sleep(self.max_session_s)
        logs.info("realtime", "RT_TIME_LIMIT", "session time limit reached")
        self._request_end("timeout")

    async def _mic(self) -> None:
        silence = bytes(CHUNK_BYTES)
        async for chunk in self.audio_io.mic_chunks(RATE, CHUNK_BYTES):
            if not self.running:
                return
            gated = self.half_duplex and time.monotonic() < self._speaking_until + 0.4
            if not gated:
                from .wakeword import rms
                if rms(chunk) > 0.015:
                    self.note_activity()
            await self.session.send_audio(silence if gated else chunk)

    async def _events(self) -> None:
        in_handler = False
        try:
            async for ev in self.session:
                in_handler = True
                await self.handle_event(ev)
                in_handler = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.running:
                code = getattr(e, "code", None) or type(e).__name__
                what = "internal error" if in_handler else "connection lost"
                logs.error("realtime", "RT_CONNECTION_LOST", "realtime connection lost", stage=what, **logs.error_fields(e))
                await self._health("degraded", f"realtime voice stopped: {what} ({type(e).__name__})",
                                   "check the internet; tap to start a new conversation")
                self._request_end(f"error:{code}")
        else:
            if self.running:
                logs.warn("realtime", "RT_REMOTE_CLOSED", "realtime session closed by the server")
                self._request_end("remote_closed")

    async def handle_event(self, ev) -> None:
        t = ev.type
        if t != "audio":
            logs.verbose("realtime", "RT_EVENT", "realtime event", event=str(t)[:64])
        if t == "audio":
            self.note_activity()
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
            self._stats["barge_ins"] += 1
            logs.info("realtime", "RT_BARGE_IN", "user interrupted JARVES")
            await self._state("listening")
        elif t == "audio_end":
            await self._state("listening")
        elif t == "tool_start":
            self._backend_busy = True
            self.note_activity()
            await self._state("thinking")
        elif t == "tool_end":
            self._backend_busy = False
            self.note_activity()
        elif t == "history_added" and self.on_transcript:
            self.note_activity()
            item = ev.item
            for c in getattr(item, "content", []) or []:
                txt = getattr(c, "transcript", None) or getattr(c, "text", None)
                if txt:
                    await self.on_transcript(getattr(item, "role", ""), txt)
        elif t == "error":
            code, msg, etype = error_info(ev.error)
            self._stats["errors"] += 1
            fatal = is_fatal(code, etype)
            logs.error("realtime", "RT_ERROR", "realtime provider error", error_code=code, provider_error=msg, fatal=fatal)
            await self._health("degraded", f"realtime voice error {code}: {msg}",
                               ERROR_ACTIONS.get(code, GENERIC_ACTION))
            if fatal:
                self._request_end(f"error:{code}")


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
        from contextlib import aclosing
        chunks = []
        async with aclosing(self.stream(text)) as source:
            async for samples, _rate in source:
                chunks.append(samples)
        return np.concatenate(chunks) if chunks else np.zeros(0, np.float32), RATE

    async def stream(self, text: str):
        from .tts import TTSUnavailable
        from .. import pronounce
        text = pronounce.apply(text)             # user pronunciation dictionary (PRONUNCIATIONS.md)
        instr = ("You are a text-to-speech voice. Read the user's message aloud EXACTLY as written, in natural, warm "
                 "Israeli Hebrew (or the message's language). Do not add, answer or comment on anything.")
        received = False
        t0 = time.monotonic()
        try:
            runner = _runner(self.model, self.voice, self.language, instr, [])
            async with await runner.run() as session:
                t_conn = time.monotonic()
                await session.send_message(text)

                async with asyncio.timeout(self.timeout):
                    async for ev in session:
                        if ev.type == "audio":
                            if not received:
                                logs.info("tts", "TTS_CLOUD_PHASES", "cloud speech phases",
                                          connect_ms=round((t_conn - t0) * 1000),
                                          first_audio_ms=round((time.monotonic() - t_conn) * 1000))
                            received = True
                            yield np.frombuffer(ev.audio.data, "<i2").astype(np.float32) / 32768.0, RATE
                        elif ev.type == "audio_end" and received:
                            return
                        elif ev.type == "error":
                            raise TTSUnavailable("TTS_FAILED", f"realtime speech error: {str(ev.error)[:150]}")
        except TTSUnavailable:
            raise
        except asyncio.TimeoutError as e:
            raise TTSUnavailable("TTS_TIMEOUT", "cloud speech timed out", "check the internet") from e
        except Exception as e:
            raise TTSUnavailable("TTS_OFFLINE", f"cloud speech unavailable ({type(e).__name__})",
                                 "check OPENAI_API_KEY and the internet") from e
        if not received:
            raise TTSUnavailable("TTS_NO_AUDIO", "cloud speech ended without audio", "check the speech model")
