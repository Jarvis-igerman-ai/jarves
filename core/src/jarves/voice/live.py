"""Primary voice: OpenAI GPT-Live (gpt-live-1) full-duplex conversation over the Live WebSocket.

GPT-Live handles listening, turn-taking, interruption and speech; anything needing reasoning, current facts or
actions is delegated (Responses delegation) to a GPT-6 backend model with hosted web search and the JARVES
Skills as function tools. Function calls are executed here, in the Core, through the SkillRegistry.
Docs (checked 2026-10-18): developers.openai.com/api/docs/guides/live, /live-delegation, /voice-websockets?api=live,
/voice-server-controls?api=live; SDK types openai.types.live (ServerEvent union).
Billing: voice $0.05/min (per second) + backend tokens. Hebrew voice quality: UNVERIFIED until tested with a key.

Lifecycle (CR-02): every way a session can end (user stop, remote `session.closed`, transport failure, a crashed
background task, the max-duration watchdog, the daily budget) goes through one idempotent `_finalize(reason)`:
cancel+await tasks, close the connection and audio, report `on_usage(seconds)` once, state idle, `on_end(reason)`
once. Reasons: "user", "remote_closed", "timeout", "budget", "error:<code>".

Errors (CR-05), per the docs: `error` events carry error.code/message/type; delegation settings are validated
asynchronously after `session.started` and a failure arrives as an `error` with code `responses_validation_failed`
(the session stays open). Both mark voice health "degraded" with an actionable reason; only fatal codes end it.

Interruption (CR-06): GPT-Live's primary WebSocket has NO interruption / output-audio-done event (docs: "Track
playback with your application's audio queue"). The model stops generating on barge-in by itself, so the Core
flushes its own queued speech when the user starts a new utterance while assistant audio is still queued
(full-duplex only; in half-duplex the mic is gated so that cannot happen). `interrupt()` is also public.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import json
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .. import logs
from ..skills import SkillRegistry, ToolResult

if TYPE_CHECKING:
    from ..budget import Budget

RATE = 24000
CHUNK_BYTES = 4800          # 100 ms of PCM16 mono @ 24 kHz
BARGE_IN_MIN_S = 0.5        # assistant audio must have been playing this long before a new user utterance flushes it

FRONT_INSTRUCTIONS = """You are {name}, a warm, witty home robot on the user's desk. Speak natural Israeli Hebrew by
default; switch only if the user speaks another language. Keep turns short and conversational.
Delegate to the backend for: current information (news, weather, prices, facts you are unsure of), music control,
remembering/forgetting things, the time, and JARVES status. While the backend works, say a very short filler
("רגע, בודק"). Never claim an action happened until the backend confirms it."""

# Errors after which the session cannot do useful work -> end it (others: log + degraded + continue).
FATAL_ERROR_TYPES = {"authentication_error", "permission_error"}
FATAL_ERROR_CODES = {"invalid_api_key", "insufficient_quota", "billing_hard_limit_reached", "account_deactivated",
                     "session_expired", "session_not_found"}
ERROR_ACTIONS = {
    "responses_validation_failed": "check ai.text_model, web search and Skill tool settings; spoken chat still "
                                   "works but backend answers/actions will fail until fixed",
    "invalid_api_key": "check OPENAI_API_KEY (jarvis key)",
    "insufficient_quota": "check OpenAI billing / project spend limit",
    "billing_hard_limit_reached": "check OpenAI billing / project spend limit",
    "tool_outputs_required": "a tool result was not returned; report this bug (see core logs)",
    "function_call_outputs_required": "a tool result was not returned; report this bug (see core logs)",
}
GENERIC_ACTION = "see core logs; if it repeats, end and restart the live conversation"

# session.closed reason (docs) -> our end reason
CLOSE_REASONS = {"expired": "timeout", "close_requested": "remote_closed", "remote_hangup": "remote_closed",
                 "connection_lost": "remote_closed", "content": "remote_closed"}


def live_tools(registry: SkillRegistry) -> list[dict]:
    return [{"type": "function", "name": t.name, "description": t.description,
             "parameters": t.args.model_json_schema(), "strict": False} for t in registry.tools()]


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def _call(cb: Callable | None, *args: Any) -> None:
    """Invoke an optional sync-or-async callback."""
    if cb is None:
        return
    r = cb(*args)
    if inspect.isawaitable(r):
        await r


def error_info(err: Any) -> tuple[str, str, str]:
    """(code, message, type) from a Live/Realtime error object or dict (shape: openai.types.live.Error)."""
    if isinstance(err, dict) and isinstance(err.get("error"), dict):     # raw {"type":"error","error":{...}}
        err = err["error"]
    code = str(_get(err, "code") or "unknown")
    msg = str(_get(err, "message") or err or "")[:200]
    return code, msg, str(_get(err, "type") or "")


def is_fatal(code: str, etype: str) -> bool:
    return code in FATAL_ERROR_CODES or etype in FATAL_ERROR_TYPES


class SessionLifecycle:
    """Idempotent end-of-session handling shared by LiveVoice and RealtimeVoice (CR-02)."""
    _log_area = "voice"

    def _init_lifecycle(self, on_end, on_health, budget) -> None:
        self.on_end: Callable[[str], Awaitable[None]] | None = on_end
        self.on_health: Callable[[str, str, str], Any] | None = on_health
        self.budget: Budget | None = budget
        self._tasks: list[asyncio.Task] = []
        self._end_task: asyncio.Task | None = None
        self._ended = asyncio.Event()
        self._active = False            # between start() and the end of _finalize()
        self.end_reason: str | None = None
        self.running = False

    # subclasses implement
    async def _close_transport(self, reason: str) -> None: ...
    def _usage_seconds(self) -> float: return 0.0
    async def _state(self, s: str) -> None: ...

    def _reset_lifecycle(self) -> None:
        self._tasks, self._end_task, self.end_reason = [], None, None
        self._ended = asyncio.Event()
        self._active = True

    def _spawn(self, coro, name: str) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro, name=f"{self._log_area}.{name}")
        t.add_done_callback(self._task_done)
        self._tasks.append(t)
        return t

    def _task_done(self, t: asyncio.Task) -> None:
        if t.cancelled() or not self._active or self._end_task is not None:
            return
        e = t.exception()
        if e is None:
            return
        code = getattr(e, "code", None) or type(e).__name__
        logs.error(self._log_area, "VOICE_TASK_CRASHED", f"{t.get_name()} crashed: {type(e).__name__}: {str(e)[:150]}")
        self._health_soon("degraded", f"live voice stopped: {t.get_name()} failed ({type(e).__name__})",
                          getattr(e, "action", "") or GENERIC_ACTION)
        self._request_end(f"error:{code}")

    def _health_soon(self, status: str, detail: str, action: str) -> None:
        if self.on_health is not None:
            asyncio.get_running_loop().create_task(self._health(status, detail, action))

    async def _health(self, status: str, detail: str, action: str = "") -> None:
        try:
            await _call(self.on_health, status, detail, action)
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", f"on_health failed: {type(e).__name__}")

    def _request_end(self, reason: str) -> asyncio.Task | None:
        """Start finalization (once) in its own task, so a task that triggers it is never cancelling itself."""
        if self._end_task is None:
            if not self._active:
                return None
            self.running = False
            self._ended.set()
            self._end_task = asyncio.get_running_loop().create_task(self._finalize(reason))
        return self._end_task

    async def stop(self, reason: str = "user") -> None:
        """End the session (idempotent; safe after a remote close, and from inside on_end)."""
        t = self._request_end(reason)
        if t is None or t is asyncio.current_task():
            return
        await asyncio.shield(t)

    async def wait_ended(self) -> str | None:
        """Wait until the current session has fully finalized (returns at once if none is running)."""
        if self._end_task is None and self._active:
            await self._ended.wait()
        if self._end_task is not None:
            await asyncio.shield(self._end_task)
        return self.end_reason

    async def _finalize(self, reason: str) -> None:
        self.end_reason = reason
        self.running = False
        try:
            await self._close_transport(reason)
        except Exception as e:
            logs.debug(self._log_area, "VOICE_CLOSE_FAILED", f"close failed: {type(e).__name__}")
        tasks, self._tasks = [t for t in self._tasks if t is not asyncio.current_task()], []
        for t in tasks:
            t.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=3)
            if pending:
                logs.warn(self._log_area, "VOICE_TASK_STUCK", f"{len(pending)} voice task(s) did not stop")
        try:
            await self._release()
        except Exception as e:
            logs.debug(self._log_area, "VOICE_RELEASE_FAILED", f"release failed: {type(e).__name__}")
        audio = getattr(self, "audio_io", None)
        if audio is not None:
            try:
                audio.close()
            except Exception:
                pass
        seconds = self._usage_seconds()
        try:
            await _call(getattr(self, "on_usage", None), seconds)
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", f"on_usage failed: {type(e).__name__}")
        stats = self.stats()
        logs.info(self._log_area, "VOICE_ENDED", f"voice session ended ({reason})", reason=reason,
                  voice_seconds=round(seconds, 1), **{k: v for k, v in stats.items() if k.endswith(("drops", "_s"))})
        try:
            await self._state("idle")
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", f"on_state failed: {type(e).__name__}")
        self._active = False
        try:
            await _call(self.on_end, reason)
        except Exception as e:
            logs.error(self._log_area, "VOICE_CALLBACK_FAILED", f"on_end failed: {type(e).__name__}")

    async def _release(self) -> None: ...

    def stats(self) -> dict:
        audio = getattr(self, "audio_io", None)
        s = dict(audio.stats()) if audio is not None and hasattr(audio, "stats") else {}
        s.update(getattr(self, "_stats", {}))
        return s


class LiveVoice(SessionLifecycle):
    _log_area = "live"

    def __init__(self, registry: SkillRegistry, backend_instructions: Callable[[], str], *, model: str = "gpt-live-1",
                 voice: str = "marin", backend_model: str = "gpt-6-luna", web_search: bool = True,
                 name: str = "JARVES", half_duplex: bool = True, max_session_s: float = 600,
                 audio_io=None, on_state: Callable[[str], Awaitable[None]] | None = None,
                 on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
                 on_usage: Callable[[float], None] | None = None, client=None,
                 on_end: Callable[[str], Awaitable[None]] | None = None,
                 on_health: Callable[[str, str, str], Any] | None = None,
                 budget: "Budget | None" = None, start_timeout_s: float = 15, close_timeout_s: float = 5) -> None:
        self.registry, self.backend_instructions = registry, backend_instructions
        self.model, self.voice, self.backend_model, self.web_search = model, voice, backend_model, web_search
        self.name, self.half_duplex, self.max_session_s = name, half_duplex, max_session_s
        self.audio_io = audio_io                    # StreamingAudio (mic/speaker) or None in tests
        self.on_state, self.on_transcript, self.on_usage = on_state, on_transcript, on_usage
        self.start_timeout_s, self.close_timeout_s = start_timeout_s, close_timeout_s
        self._client = client
        self._init_lifecycle(on_end, on_health, budget)
        self.conn = None
        self._cm = None
        self._reset_session()

    def _reset_session(self) -> None:
        self._started = asyncio.Event()
        self._closed = asyncio.Event()
        self.usage_s = 0.0
        self._speaking_until = 0.0
        self._turn_audio_t0 = 0.0
        self._out_text = ""
        self._in_text = ""
        self._stats = {"backend_responses": 0, "barge_ins": 0, "errors": 0}

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

    def _usage_seconds(self) -> float:
        return self.usage_s

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._active:
            if self._end_task is None:
                raise RuntimeError("live session already running")
            await self.wait_ended()                 # a previous session is still finalizing
        self._reset_session()
        self._reset_lifecycle()
        try:
            if self._client is None:
                import openai
                self._client = openai.AsyncOpenAI()
            self._cm = self._client.live.connect()
            self.conn = await self._cm.__aenter__()
            self.running = True
            self._spawn(self._events(), "events")
            await self.conn.session.start(session=self.session_config(), event_id="jarves_start")
            started = asyncio.ensure_future(self._started.wait())
            ended = asyncio.ensure_future(self._ended.wait())
            try:
                await asyncio.wait({started, ended}, timeout=self.start_timeout_s,
                                   return_when=asyncio.FIRST_COMPLETED)
            finally:
                started.cancel()
                ended.cancel()
            if self._end_task is not None:
                reason = await self.wait_ended()
                raise RuntimeError(f"live session ended during start ({reason})")
            if not self._started.is_set():
                await self.stop("error:start_timeout")
                raise asyncio.TimeoutError("GPT-Live session.started not received")
        except BaseException as e:
            if self._end_task is None and self._active:
                await self.stop(f"error:{getattr(e, 'code', None) or type(e).__name__}")
            raise
        if self.audio_io is not None:
            self._spawn(self._mic(), "mic")
            self._spawn(self._player(), "player")
        self._spawn(self._watchdog(), "watchdog")
        logs.info("live", "LIVE_STARTED", f"GPT-Live session started ({self.model} -> {self.backend_model})")
        await self._state("listening")

    async def _close_transport(self, reason: str) -> None:
        if self.conn is None or self._closed.is_set():
            return
        # Graceful close: keep receiving until session.closed (final usage), bounded wait (docs).
        await asyncio.wait_for(self.conn.session.close(event_id="jarves_close"), self.close_timeout_s)
        try:
            await asyncio.wait_for(self._closed.wait(), self.close_timeout_s)
        except asyncio.TimeoutError:
            logs.warn("live", "LIVE_CLOSE_UNCONFIRMED", "session.closed not received; final usage unconfirmed")

    async def _release(self) -> None:
        cm, self._cm, self.conn = self._cm, None, None
        if cm is not None:
            await cm.__aexit__(None, None, None)

    async def _watchdog(self) -> None:          # cost guard
        await asyncio.sleep(self.max_session_s)
        logs.info("live", "LIVE_TIME_LIMIT", "session time limit reached")
        self._request_end("timeout")

    async def _mic(self) -> None:
        silence = bytes(CHUNK_BYTES)
        async for chunk in self.audio_io.mic_chunks(RATE, CHUNK_BYTES):
            if not self.running:
                return
            gated = self._gated()
            await self.conn.session.input_audio.append(audio=base64.b64encode(silence if gated else chunk).decode())

    def _gated(self) -> bool:                   # half-duplex echo guard on speakers
        return self.half_duplex and time.monotonic() < self._speaking_until + 0.4

    async def _player(self) -> None:
        await self.audio_io.run_player(RATE)

    def _play(self, pcm: bytes) -> None:
        now = time.monotonic()
        if now > self._speaking_until:
            self._turn_audio_t0 = now
        self._speaking_until = max(self._speaking_until, now) + len(pcm) / 2 / RATE
        if self.audio_io is not None:
            self.audio_io.enqueue(pcm)

    async def interrupt(self) -> None:
        """Barge-in: drop queued assistant speech immediately."""
        flushed = self.audio_io.flush() if self.audio_io is not None else 0
        self._speaking_until = 0.0
        self._stats["barge_ins"] += 1
        logs.info("live", "LIVE_BARGE_IN", "user interrupted JARVES", flushed_bytes=flushed)
        await self._state("listening")

    async def _events(self) -> None:
        in_handler = False
        try:
            async for ev in self.conn:
                in_handler = True
                await self.handle_event(ev)
                in_handler = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.running:
                code = getattr(e, "code", None) or type(e).__name__
                if in_handler:                  # our bug, not the network: still end cleanly
                    logs.error("live", "LIVE_EVENT_HANDLER_CRASHED",
                               f"event handler crashed: {type(e).__name__}: {str(e)[:150]}")
                    detail, action = f"live voice stopped: internal error ({type(e).__name__})", GENERIC_ACTION
                else:
                    logs.error("live", "LIVE_CONNECTION_LOST", f"Live connection lost: {type(e).__name__}")
                    detail = f"live connection lost ({type(e).__name__})"
                    action = "check the internet; tap to start a new conversation"
                await self._health("degraded", detail, action)
                self._request_end(f"error:{code}")
        else:
            if self.running:                    # stream ended without session.closed
                logs.warn("live", "LIVE_STREAM_ENDED", "Live connection closed without session.closed")
                self._request_end("remote_closed")
        finally:
            self._closed.set()

    async def handle_event(self, ev) -> None:
        t = _get(ev, "type")
        if t != "session.output_audio.delta":
            logs.verbose("live", "LIVE_EVENT", str(t))
        g = lambda k, d=None: _get(ev, k, d)    # noqa: E731
        if t == "session.started":
            self._started.set()
            await self._health("ok", f"GPT-Live connected ({self.model} -> {self.backend_model})", "")
        elif t == "session.output_audio.delta":
            if time.monotonic() > self._speaking_until:
                await self._state("speaking")
            self._play(base64.b64decode(g("delta")))
        elif t == "session.input_transcript.delta":
            now = time.monotonic()
            if (not self._in_text and not self._gated() and now < self._speaking_until
                    and now - self._turn_audio_t0 >= BARGE_IN_MIN_S):
                await self.interrupt()          # new user utterance over still-queued assistant speech
            self._in_text += g("delta") or ""
            if self._out_text and self.on_transcript:
                await self.on_transcript("assistant", self._out_text.strip())
                self._out_text = ""
        elif t == "session.output_transcript.delta":
            if self._in_text and self.on_transcript:
                await self.on_transcript("user", self._in_text.strip())
                self._in_text = ""
            self._out_text += g("delta") or ""
        elif t == "session.delegation.created":
            if (self.budget is not None and _get(g("delegation"), "target") == "responses"
                    and self.budget.exhausted("ai_requests")):
                await self._budget_exhausted("delegation")
        elif t == "response.event":
            await self._backend_event(g("event") or {}, g("delegation_id"))
        elif t == "session.usage.updated":
            self.usage_s = float(_get(g("usage"), "seconds", self.usage_s) or 0)
        elif t == "session.closed":
            usage = g("usage")
            if usage is not None:
                self.usage_s = float(_get(usage, "seconds", self.usage_s) or 0)
            server_reason = str(g("reason") or "")
            self._closed.set()
            if self._end_task is None:          # not requested by us
                logs.info("live", "LIVE_REMOTE_CLOSED", f"server closed the session ({server_reason or 'unknown'})")
                if server_reason in ("connection_lost", "content"):
                    await self._health("degraded", f"live session ended by the server ({server_reason})",
                                       "check the internet; tap to start a new conversation")
                self._request_end(CLOSE_REASONS.get(server_reason, "remote_closed"))
        elif t == "error":
            await self._on_error(g("error"))
        elif t == "info":
            logs.debug("live", "LIVE_INFO", str(g("message"))[:200])

    async def _on_error(self, err, backend: bool = False) -> None:
        code, msg, etype = error_info(err)
        self._stats["errors"] += 1
        fatal = is_fatal(code, etype)
        logs.error("live", "LIVE_BACKEND_ERROR" if backend else "LIVE_ERROR",
                   f"{'backend' if backend else 'Live'} error {code}: {msg}", fatal=fatal)
        if code == "responses_validation_failed":
            detail = f"backend delegation settings rejected: {msg}"
        else:
            detail = f"{'backend' if backend else 'live voice'} error {code}: {msg}"
        await self._health("degraded", detail, ERROR_ACTIONS.get(code, GENERIC_ACTION))
        if fatal:
            self._request_end(f"error:{code}")

    async def _budget_exhausted(self, where: str) -> None:
        logs.warn("live", "AI_BUDGET", f"daily AI request limit reached ({where}); ending live session",
                  action="raise ai.daily_request_limit")
        await self._health("degraded", "daily AI request limit reached; live voice ended",
                           "raise ai.daily_request_limit or wait until tomorrow")
        self._request_end("budget")

    async def _backend_event(self, inner: dict, delegation_id: str | None) -> None:
        it = inner.get("type")
        if it == "response.created":
            await self._state("thinking")
        elif it in ("response.completed", "response.failed", "response.incomplete"):
            # One delegated backend response finished (billed whether or not it succeeded) -> count it.
            self._stats["backend_responses"] += 1
            if self.budget is not None:
                self.budget.record("ai_requests", 1)
            if it == "response.failed":
                await self._on_error(_get(inner.get("response"), "error") or {"code": "response_failed"}, True)
        elif it == "response.output_item.done":
            item = inner.get("item") or {}
            if item.get("type") != "function_call":
                return
            name, call_id = item.get("name", ""), item.get("call_id", "")
            logs.info("live", "LIVE_TOOL_CALL", f"backend called {name}", delegation=delegation_id)
            if self.budget is not None and self.budget.exhausted("ai_requests"):
                res = ToolResult(False, "daily AI request limit reached; the conversation will end now",
                                 code="AI_BUDGET")
                await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                           "output": res.to_model()})
                await self._budget_exhausted(f"tool {name}")   # no response.create: that would be another paid call
                return
            res = await self.registry.call(name, item.get("arguments") or "{}")
            await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                       "output": res.to_model()})
            await self.conn.response.create()
        elif it == "error":
            await self._on_error(inner.get("error") or inner, True)
