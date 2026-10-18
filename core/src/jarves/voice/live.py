"""Primary voice: OpenAI GPT-Live (gpt-live-1) full-duplex conversation over the Live WebSocket.

GPT-Live handles listening, turn-taking, interruption and speech; anything needing reasoning, current facts or
actions is delegated (Responses delegation) to a GPT-6 backend model with hosted web search and the JARVES
Skills as function tools. Function calls are executed here, in the Core, through the SkillRegistry.
Docs (checked 2026-10-18): developers.openai.com/api/docs/guides/live, /live-delegation, /voice-websockets?api=live
Billing: voice $0.05/min (per second) + backend tokens. Hebrew voice quality: UNVERIFIED until tested with a key.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Awaitable, Callable

from .. import logs
from ..skills import SkillRegistry

RATE = 24000
CHUNK_BYTES = 4800          # 100 ms of PCM16 mono @ 24 kHz

FRONT_INSTRUCTIONS = """You are {name}, a warm, witty home robot on the user's desk. Speak natural Israeli Hebrew by
default; switch only if the user speaks another language. Keep turns short and conversational.
Delegate to the backend for: current information (news, weather, prices, facts you are unsure of), music control,
remembering/forgetting things, the time, and JARVES status. While the backend works, say a very short filler
("רגע, בודק"). Never claim an action happened until the backend confirms it."""


def live_tools(registry: SkillRegistry) -> list[dict]:
    return [{"type": "function", "name": t.name, "description": t.description,
             "parameters": t.args.model_json_schema(), "strict": False} for t in registry.tools()]


class LiveVoice:
    def __init__(self, registry: SkillRegistry, backend_instructions: Callable[[], str], *, model: str = "gpt-live-1",
                 voice: str = "marin", backend_model: str = "gpt-6-luna", web_search: bool = True,
                 name: str = "JARVES", half_duplex: bool = True, max_session_s: float = 600,
                 audio_io=None, on_state: Callable[[str], Awaitable[None]] | None = None,
                 on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
                 on_usage: Callable[[float], None] | None = None, client=None) -> None:
        self.registry, self.backend_instructions = registry, backend_instructions
        self.model, self.voice, self.backend_model, self.web_search = model, voice, backend_model, web_search
        self.name, self.half_duplex, self.max_session_s = name, half_duplex, max_session_s
        self.audio_io = audio_io                    # StreamingAudio (mic/speaker) or None in tests
        self.on_state, self.on_transcript, self.on_usage = on_state, on_transcript, on_usage
        self._client = client
        self.conn = None
        self._cm = None
        self._tasks: list[asyncio.Task] = []
        self._started = asyncio.Event()
        self._closed = asyncio.Event()
        self.running = False
        self.usage_s = 0.0
        self._speaking_until = 0.0
        self._out_text = ""
        self._in_text = ""

    def session_config(self) -> dict:
        tools: list[dict] = live_tools(self.registry)
        if self.web_search:
            tools.append({"type": "web_search"})
        return {
            "model": self.model,
            "instructions": FRONT_INSTRUCTIONS.format(name=self.name),
            "audio": {"format": {"type": "audio/pcm", "rate": RATE}, "output": {"voice": self.voice}},
            "delegation": {"type": "responses", "responses": {
                "model": self.backend_model, "instructions": self.backend_instructions(), "tools": tools,
                "tool_choice": "auto", "parallel_tool_calls": False, "text": {"verbosity": "low"}}},
        }

    async def _state(self, s: str) -> None:
        if self.on_state:
            await self.on_state(s)

    async def start(self) -> None:
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI()
        self._cm = self._client.live.connect()
        self.conn = await self._cm.__aenter__()
        self.running = True
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._events())]
        await self.conn.session.start(session=self.session_config(), event_id="jarves_start")
        try:
            await asyncio.wait_for(self._started.wait(), 15)
        except asyncio.TimeoutError:
            await self.stop()
            raise
        if self.audio_io is not None:
            self._tasks += [loop.create_task(self._mic()), loop.create_task(self._player())]
        self._tasks.append(loop.create_task(self._watchdog()))
        logs.info("live", "LIVE_STARTED", f"GPT-Live session started ({self.model} -> {self.backend_model})")
        await self._state("listening")

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        try:
            if self.conn is not None:
                await self.conn.session.close(event_id="jarves_close")
                await asyncio.wait_for(self._closed.wait(), 5)
        except Exception:
            pass
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self.conn = self._cm = None
        if self.audio_io is not None:
            self.audio_io.close()
        if self.on_usage:
            self.on_usage(self.usage_s)
        logs.info("live", "LIVE_STOPPED", "GPT-Live session closed", voice_seconds=round(self.usage_s, 1))
        await self._state("idle")

    async def _watchdog(self) -> None:          # cost guard
        await asyncio.sleep(self.max_session_s)
        logs.info("live", "LIVE_TIME_LIMIT", "session time limit reached")
        asyncio.get_running_loop().create_task(self.stop())

    async def _mic(self) -> None:
        silence = bytes(CHUNK_BYTES)
        async for chunk in self.audio_io.mic_chunks(RATE, CHUNK_BYTES):
            if not self.running:
                return
            gated = self.half_duplex and time.monotonic() < self._speaking_until + 0.4   # echo guard on speakers
            await self.conn.session.input_audio.append(audio=base64.b64encode(silence if gated else chunk).decode())

    async def _player(self) -> None:
        await self.audio_io.run_player(RATE)

    def _play(self, pcm: bytes) -> None:
        self._speaking_until = max(self._speaking_until, time.monotonic()) + len(pcm) / 2 / RATE
        if self.audio_io is not None:
            self.audio_io.enqueue(pcm)

    async def _events(self) -> None:
        try:
            async for ev in self.conn:
                await self.handle_event(ev)
        except Exception as e:
            if self.running:
                logs.error("live", "LIVE_CONNECTION_LOST", f"Live connection lost: {type(e).__name__}")
                await self._state("error")
        finally:
            self._closed.set()

    async def handle_event(self, ev) -> None:
        t = getattr(ev, "type", None) or ev.get("type")
        g = (lambda k, d=None: getattr(ev, k, d)) if not isinstance(ev, dict) else ev.get
        if t == "session.started":
            self._started.set()
        elif t == "session.output_audio.delta":
            if time.monotonic() > self._speaking_until:
                await self._state("speaking")
            self._play(base64.b64decode(g("delta")))
        elif t == "session.input_transcript.delta":
            self._in_text += g("delta") or ""
            if self._out_text and self.on_transcript:
                await self.on_transcript("assistant", self._out_text.strip())
                self._out_text = ""
        elif t == "session.output_transcript.delta":
            if self._in_text and self.on_transcript:
                await self.on_transcript("user", self._in_text.strip())
                self._in_text = ""
            self._out_text += g("delta") or ""
        elif t == "response.event":
            await self._backend_event(g("event") or {}, g("delegation_id"))
        elif t == "session.usage.updated":
            self.usage_s = float(getattr(g("usage"), "seconds", 0) if not isinstance(ev, dict) else ev["usage"]["seconds"])
        elif t == "session.closed":
            self._closed.set()
            self.running = False
        elif t == "error":
            err = g("error")
            logs.error("live", "LIVE_ERROR", f"Live error: {str(err)[:200]}")
        elif t == "info":
            logs.debug("live", "LIVE_INFO", str(g("message"))[:200])

    async def _backend_event(self, inner: dict, delegation_id: str | None) -> None:
        it = inner.get("type")
        if it == "response.created":
            await self._state("thinking")
        elif it == "response.output_item.done":
            item = inner.get("item") or {}
            if item.get("type") != "function_call":
                return
            name, call_id = item.get("name", ""), item.get("call_id", "")
            logs.info("live", "LIVE_TOOL_CALL", f"backend called {name}", delegation=delegation_id)
            res = await self.registry.call(name, item.get("arguments") or "{}")
            await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                       "output": res.to_model()})
            await self.conn.response.create()
        elif it in ("response.failed", "error"):
            logs.error("live", "LIVE_BACKEND_ERROR", f"backend error: {json.dumps(inner)[:200]}")
