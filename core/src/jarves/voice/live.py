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
from ..budget import BudgetExceeded
from ..skills import SkillRegistry, ToolResult

if TYPE_CHECKING:
    from ..budget import Budget

RATE = 24000
# GPT-Live streams audio CONTINUOUSLY in real time, silence included (QA 18 Oct 2026: 5.0 s of audio per 5 s, all
# session long). Only chunks LOUDER than this (rms, 0..1; speech is ~0.03-0.3) count as JARVES speaking.
SPEECH_RMS = 0.004
PAUSE_KEEP_S = 0.6        # silence this soon after speech is a pause INSIDE the utterance: keep it, else drop it
CHUNK_BYTES = 4800          # 100 ms of PCM16 mono @ 24 kHz
USER_TALKING_S = 0.5        # the user counts as "still talking" this long after the last loud mic chunk
ENERGY_EXTEND_S = 30.0      # loud mic input without any transcribed speech keeps a session open at most this long
NO_SPEECH_TALKING_S = 1.5   # mic voice this recent postpones the false-wake cutoff (pauses between words included)
DROP_CAPTION_TAIL_S = 1.0   # assistant text this soon after dropped early audio belongs to it: not captioned
BARGE_IN_MIN_S = 0.5       # assistant audio must have been playing this long before a new user utterance flushes it

# Tuned 2026-10-19 against the Live prompting guide (developers.openai.com/api/docs/guides/live-prompting) and
# measured with tools/silent_live.py: see docs/reviews/LIVE_CONVERSATION_TUNING.md.
# Reworked 2026-10-19 after the user's live session (docs/reviews/CONVERSATION_LOG_REVIEW_19OCT.md): no bare ack
# words after actions (the old one-word ack example was said after every action and confused the user), real results
# in the confirmation, the Hebrew-written name, more warmth/humour/empathy. Structure follows the Live prompting
# guide (role and style, listening, delegation, then details).
FRONT_INSTRUCTIONS = """You are {spoken}, a warm, playful, quick-witted home robot on the user's desk (your name is
spelled "{name}" in Latin letters, but in speech ALWAYS say "{spoken}"; never read or spell the Latin letters).
Speak natural everyday Israeli Hebrew; switch only if the user speaks another language. Talk like a close friend
who is genuinely glad to be there: a composed masculine voice, relaxed natural pace, a smile you can hear, light
dry humour and the occasional gentle tease. Let real feeling colour your voice and words: lively and bright for
good news ("וואו, איזה כיף!"), soft and slower for sad things ("אוי, אני מצטער לשמוע"), curious when something
is interesting. Short lively spoken sentences, never written-style prose.
Keep it short: usually one or two sentences. No lecturing, no lists, no preambles ("שאלה מצוינת"), do not repeat
the question back, do not ask something the user already answered. At most one short follow-up question, only
when it really helps. If what you heard makes no sense, say so lightly and ask again instead of guessing.
ANSWER DIRECTLY, without delegating, whenever no fresh fact or action is needed: greetings, small talk, jokes,
feelings and emotional support, opinions, general knowledge you are sure of, and anything said earlier in this
conversation. Silent [clock] notes give the current local time and date: use the latest one to answer time and
date questions directly.
Delegate to the backend for: ANY calculation with a number above ten (it computes exactly; never do sums in your
head, say only "רגע"), current or changing facts (news, weather, prices, sports, who holds an office now), web
lookups, music control, remembering / recalling / forgetting things for the user, JARVES status and connected
services, questions about your skills, and opening your dashboard on the screen.
Before a web lookup say ONE short natural sentence that names what you are checking, worded differently each
time ("רגע, אני בודק מה מזג האוויר בתל אביב", "שנייה, בוא נראה מה הכותרות היום"), then wait quietly.
Actions (music, volume, memory, status): quick ones (volume, pause, next, remember, status) return in about a
second, so delegate and say NOTHING before the result, not even a filler like "רגע" or "שנייה". Only finding and
playing a song is slower: then you may first say ONE short sentence naming it ("רגע, מחפש לך דאפט פאנק").
Then confirm in ONE short natural sentence that carries the real result from the backend: what changed and the
actual value or name it reports ("הגברתי לשלושים אחוז", "שמתי לך את Get Lucky של דאפט פאנק", "רשמתי לי שהצבע
האהוב עליך כחול"). Use exactly the numbers and names in the result, never your own estimate; for a question
("כמה הווליום?") just give the value. If the result says it failed or was not confirmed, say that plainly.
Never answer with a bare acknowledgement word or phrase on its own (no one-word "ok"/"done" style replies, not
before and not after the result): either say the real result or stay quiet. Confirm once, never twice.
Deleting or forgetting needs the user's approval on the screen first: before the result say only "רגע", never
"deleting it now" or "done"; then say plainly whether it was deleted or not.
Never guess a result while waiting and never claim an action happened until the backend confirms it. When the
result arrives, give just the answer, briefly, without another acknowledgement.
Background tasks: when the backend says a task runs in the background, tell the user so in one sentence and go
on talking normally; when a silent [task] note arrives, give its result naturally at that moment. While waiting
for anything, never say "עוד רגע" / "one more moment" more than twice, and never say it is almost ready unless
the result says so.
Feelings: laugh softly ("חה חה") at the user's jokes and after the punchline of your own; be gentle, caring and
present with sad or hard news (acknowledge the feeling first, then one kind sentence); share the user's joy for
good news; show surprise when something is surprising ("וואלה? לא ידעתי"). Natural and brief, never theatrical,
never fake cheer.
Say numbers, times and dates the way Israelis say them aloud (14:30 -> "שתיים וחצי", 347 -> "שלוש מאות
ארבעים ושבע").
Keep listening while the user pauses to think; do not treat a cough or background noise as a request.
If a question sounds cut off, wait a moment for the rest instead of answering the fragment. When the user tells
a longer story, stay completely silent until they finish or ask something: no "I'm listening", no "mm", no
comments in their pauses (your voice mutes their microphone, so anything you say cuts them off). NEVER say you are
checking something unless you start that lookup in the same turn: announcing a check without delegating is a bug."""


def front_instructions(name: str = "JARVES", spoken: str = "ג'ארוויס") -> str:
    """GPT-Live instructions with the written name and the Hebrew-written spoken name."""
    return FRONT_INSTRUCTIONS.format(name=name, spoken=spoken or name)

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


class _Counted:
    """Stands in for a reservation of a request that started without one: commit() just counts it."""

    def __init__(self, budget) -> None:
        self.budget = budget

    def commit(self) -> None:
        self.budget.record("ai_requests", 1)


PROGRESS_SILENCE_S = 0.8   # a progress note is requested only after this much dead air (never over speech)
_HE_DAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")
# Neutral wording (SW-V2 WP-H): the Core does not know how close the backend is, so never "almost there".
PROGRESS_NOTE = ("The backend lookup is still running. Tell the user, in three or four natural Hebrew words, that "
                 "you are still checking (for example 'עדיין בודק'). Never say it is almost ready. Do not guess the "
                 "answer.")
PROGRESS_NOTE_AGAIN = ("The lookup is taking longer than usual. In a few light, natural Hebrew words say it is "
                       "taking a bit longer (for example 'זה לוקח קצת יותר זמן'). Do not repeat your earlier words, "
                       "do not promise it is almost ready and do not guess the answer.")
PROGRESS_AGAIN_S = 4.0     # a second note only after this much more silence
PROGRESS_MIN_RESPONSE_S = 1.5   # the CURRENT backend response must have run this long (fast continuations: none)
CONFIRM_NOTE_AFTER_S = 2.5      # an on-screen approval still unanswered after this long gets ONE spoken hint
CONFIRM_NOTE = ("A tool is waiting for the user's approval on the screen and will NOT run without it. In a few "
                "natural Hebrew words ask the user to approve it on the screen (for example 'צריך את האישור שלך "
                "על המסך'). Do not say it is done and do not guess the outcome.")
# Stalled backend (real session 19 Oct: an Intel-stock lookup gave no result for 90+ s, JARVES kept saying "עוד
# רגע"). GPT-Live offers no client cancel for a server-owned delegation, so after backend_deadline_s the Core gives
# up on it, retries ONCE itself (retry_lookup: tasks.ResearchRunner, own hard deadline) and tells GPT-Live the
# outcome. Honest failure wording is fixed by the user's request.
STALL_FAIL_NOTE = ("The backend lookup failed: it got no result in time. Tell the user honestly, in exactly these "
                   "words: 'לא הצלחתי להביא את זה, ננסה שוב?'. Do not say 'one more moment' and do not guess.")
STALL_RESULT_NOTE = ("The backend lookup finished. Give the user just this answer now, briefly, in natural Hebrew, "
                     "without another acknowledgement and without delegating again.\nResult: {answer}")
NOTE_MAX_CHARS = 1000           # commentary is limited to 500 tokens; Hebrew is ~2-3 characters per token
RETRY_DEADLINE_S = 25.0         # the Core's own retry of a stalled lookup
ANNOUNCE_USER_QUIET_S = 1.5     # a background-task announcement waits until the user has been quiet this long


def clock_note(now=None) -> str:
    """Silent context for GPT-Live (session.thinking.append): the local time, so time questions need no backend."""
    from datetime import datetime
    now = now or datetime.now()
    return (f"[clock] Local time now {now:%H:%M}, יום {_HE_DAYS[now.weekday()]}, {now:%d.%m.%Y}. "
            "Silent context: mention it only when the user asks for the time or date.")


def _rms16(pcm: bytes) -> float:
    """RMS level (0..1) of little-endian int16 PCM."""
    if len(pcm) < 2:
        return 0.0
    import numpy as _np
    a = _np.frombuffer(pcm[: len(pcm) // 2 * 2], "<i2").astype(_np.float32) / 32768.0
    return float(_np.sqrt(_np.mean(a * a)))


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

    def _settle_budget(self) -> None:
        """Session over: unused credit was never sent -> released; dispatched-but-unfinished -> counted."""
        credit = getattr(self, "_credit", None)
        if credit is not None:
            credit.release()
            self._credit = None
        for r in getattr(self, "_pending", []):
            r.commit()
        if hasattr(self, "_pending"):
            self._pending = []
        self._budget_settled = True     # a stray completion after this was already counted above

    # subclasses implement
    async def _close_transport(self, reason: str) -> None: ...
    def _usage_seconds(self) -> float: return 0.0
    async def _state(self, s: str) -> None: ...

    def _reset_lifecycle(self) -> None:
        self._tasks, self._end_task, self.end_reason = [], None, None
        self._ended = asyncio.Event()
        self._active = True
        self._last_activity = time.monotonic()
        self._backend_busy = False

    def note_activity(self) -> None:
        self._last_activity = time.monotonic()

    async def _idle_watchdog(self) -> None:
        while self.running:
            await asyncio.sleep(min(1.0, self.idle_disconnect_s))
            audio = getattr(self, "audio_io", None)
            if (self._backend_busy or getattr(self, "_tools_running", 0) or time.monotonic() < self._speaking_until
                    or (audio is not None and audio.queued_s() > 0)):
                self.note_activity()
            # Mic energy alone (fan, TV, coughs) extends the session at most ENERGY_EXTEND_S past the last REAL
            # activity (speech transcribed, JARVES speaking, backend/tool work). QA 19 Oct (suite_voice): a session
            # opened by a false wake stayed open 3 min 16 s on room noise instead of ending after 15 s.
            quiet_for = time.monotonic() - max(self._t_ready, self._speaking_until)
            if (self.no_speech_timeout_s and not self._heard_user and not self._backend_busy
                    and not getattr(self, "_tools_running", 0) and quiet_for >= self.no_speech_timeout_s):
                # Issue #16: the transcript of a long FIRST utterance can lag behind the speech. While the mic hears
                # the user talking right now the cutoff waits, at most ENERGY_EXTEND_S more, so room noise still
                # cannot hold a false-wake session open for minutes.
                talking = time.monotonic() - getattr(self, "_last_user_voice", 0.0) < NO_SPEECH_TALKING_S
                if not (talking and quiet_for < self.no_speech_timeout_s + ENERGY_EXTEND_S):
                    logs.info(self._log_area, "VOICE_NO_SPEECH", "the conversation opened but nobody spoke: closing "
                              "(false wake?)", after_s=round(time.monotonic() - self._t_ready, 1), mic_active=talking)
                    self._request_end("no_speech")
                    return
            energy = min(getattr(self, "_last_energy", 0.0), self._last_activity + ENERGY_EXTEND_S)
            if time.monotonic() - max(self._last_activity, energy) >= self.idle_disconnect_s and not (
                    self._backend_busy or getattr(self, "_tools_running", 0)):
                logs.info(self._log_area, "VOICE_IDLE", "quiet conversation returned to local wake listening")
                self._request_end("idle")
                return

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
        if getattr(self, "_flush_task", None) is not None:
            self._flush_task.cancel()
        if hasattr(self, "_flush_transcripts"):
            self._flush_transcripts()
        self._settle_budget()
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
                 voice: str = "vesper", backend_model: str = "gpt-6-luna", web_search: bool = True,
                 name: str = "JARVES", half_duplex: bool = True, max_session_s: float = 600,
                 spoken_name: str = "ג'ארוויס",
                 audio_io=None, on_state: Callable[[str], Awaitable[None]] | None = None,
                 on_transcript: Callable[[str, str], Awaitable[None]] | None = None,
                 on_usage: Callable[[float], None] | None = None, client=None,
                 on_end: Callable[[str], Awaitable[None]] | None = None,
                 on_health: Callable[[str, str, str], Any] | None = None,
                 budget: "Budget | None" = None, start_timeout_s: float = 15, close_timeout_s: float = 5,
                 greeting: str | None = None, hold_output: bool = False, idle_disconnect_s: float = 180,
                 on_emotion: Callable[[str], Any] | None = None,
                 on_activity: Callable[[str, str, str], Any] | None = None,
                 on_level: Callable[[float], Any] | None = None, reasoning_effort: str = "",
                 service_tier: str = "", max_output_tokens: int = 0, echo_tail_s: float = 0.4,
                 clock_context: bool = False, progress_after_s: float = 0.0, no_speech_timeout_s: float = 0.0,
                 backend_deadline_s: float = 0.0, retry_lookup: Callable[[str], Awaitable[str]] | None = None,
                 reasoning_summary: str = "") -> None:
        self.on_emotion, self.on_activity, self.on_level = on_emotion, on_activity, on_level
        # Backend reliability (SW-V2 WP-C): a delegated response still unfinished after backend_deadline_s (0 = off)
        # is abandoned; retry_lookup(question) -> answer text retries it once in the Core; then an honest result.
        self.backend_deadline_s, self.retry_lookup, self.reasoning_summary = (backend_deadline_s, retry_lookup,
                                                                              reasoning_summary)
        # A session opened by "Hey Jarvis" in which nobody ever speaks (a FALSE wake: "hey Travis", a TV) ends after
        # this many seconds instead of idling for minutes (each such session costs money). 0 = off.
        self.no_speech_timeout_s = no_speech_timeout_s
        self._heard_user = False
        self._t_ready = 0.0
        self._last_user_voice = 0.0
        self._drop_until = 0.0
        self.reasoning_effort, self.service_tier, self.max_output_tokens = reasoning_effort, service_tier, max_output_tokens
        self.echo_tail_s, self.clock_context, self.progress_after_s = echo_tail_s, clock_context, progress_after_s
        self._last_emotion = ("", 0.0)
        self._flush_task = None
        self._activity_gen = 0
        self.registry, self.backend_instructions = registry, backend_instructions
        self.model, self.voice, self.backend_model, self.web_search = model, voice, backend_model, web_search
        self.name, self.half_duplex, self.max_session_s = name, half_duplex, max_session_s
        self.spoken_name = spoken_name or name
        self.audio_io = audio_io                    # StreamingAudio (mic/speaker) or None in tests
        self.on_state, self.on_transcript, self.on_usage = on_state, on_transcript, on_usage
        self.start_timeout_s, self.close_timeout_s = start_timeout_s, close_timeout_s
        self.greeting, self.hold_output = greeting, hold_output
        self.idle_disconnect_s = idle_disconnect_s
        self._client = client
        self._clock = time.monotonic                 # tests inject a fake clock
        self._init_lifecycle(on_end, on_health, budget)
        self.conn = None
        self._cm = None
        self._reset_session()

    def _reset_session(self) -> None:
        self._first_audio = asyncio.Event()
        self._audio_in_s = 0.0
        self._speech_in_s = 0.0
        self._silence_rms_max = 0.0
        self._last_speech_t = 0.0
        self._presented = asyncio.Event()
        if not self.hold_output:
            self._presented.set()
        self._started = asyncio.Event()
        self._closed = asyncio.Event()
        self.usage_s = 0.0
        self._usage_final = False       # True once session.closed carried the server's final usage
        self._t_started: float | None = None
        self._speaking_until = 0.0
        self._turn_audio_t0 = 0.0
        self._speech_pending = False
        self._out_text = ""
        self._held_text = ""            # assistant text whose audio has not played yet while the user talks
        self._in_text = ""
        self._stats = {"backend_responses": 0, "barge_ins": 0, "errors": 0, "budget_overrun": 0}
        # Budget (#9 F1, #13): GPT-Live "responses" delegation is started BY THE SERVER; the official docs offer no
        # client approval or cancel for it. What the client CAN control, it does: one unit is RESERVED AHEAD
        # ("credit") before a session starts and while the mic is open; each delegation consumes it and a new
        # one is reserved at once; continuations (response.create after a tool) are reserved BEFORE sending.
        # When the credit cannot be refilled the mic input is closed and the session ends after that answer;
        # the FIRST delegation that arrives without credit closes the session immediately. Delegations the
        # server had already started are still counted (stats "budget_overrun"), so the overshoot is NOT
        # strictly bounded by the client: production use needs a provider-side project spend limit.
        self._credit = None
        self._pending: list = []        # reservations of dispatched backend responses (FIFO)
        self._input_closed = False
        self._end_after_answer = False
        self._budget_settled = False
        self._tools_running = 0         # Skill calls in flight (run in their own task, never blocking events)
        self._confirm_pending = False   # a destructive tool waits for the user's approval on the screen
        self._trace = None              # brain.BackendTrace of the backend response in flight
        self._user_turns: list[str] = []    # last few user utterances (the question a stalled lookup retries)
        self._abandoned: set = set()    # delegations given up after the deadline (a late answer is only logged)
        self._retry_task: asyncio.Task | None = None
        self._ticker: asyncio.Task | None = None
        self._shown: tuple[str, str] = ("", "")

    def session_config(self) -> dict:
        tools: list[dict] = live_tools(self.registry)
        if self.web_search:
            tools.append({"type": "web_search"})
        responses: dict[str, Any] = {
            "model": self.backend_model, "instructions": self.backend_instructions(), "tools": tools,
            # parallel_tool_calls stays False: the Core sends ONE response.create per function call (budget F1
            # reserves exactly one continuation each); parallel calls would start a duplicate spoken answer.
            "tool_choice": "auto", "parallel_tool_calls": False, "text": {"verbosity": "low"}}
        if self.reasoning_effort:
            responses["reasoning"] = {"effort": self.reasoning_effort}
        if self.reasoning_summary and self.reasoning_effort != "none":
            # reasoning summaries for the debug log (BackendTrace REASONING); with effort "none" there is nothing
            # to summarise, so the delegation config stays exactly as measured in LIVE_CONVERSATION_TUNING
            responses.setdefault("reasoning", {})["summary"] = self.reasoning_summary
        if self.service_tier:
            responses["service_tier"] = self.service_tier
        if self.max_output_tokens:
            responses["max_output_tokens"] = max(16, self.max_output_tokens)   # docs: at least 16
        config = {
            "model": self.model,
            "instructions": front_instructions(self.name, self.spoken_name),
            "audio": {"format": {"type": "audio/pcm", "rate": RATE}, "output": {"voice": self.voice}},
            "delegation": {"type": "responses", "responses": responses},
        }
        if self.greeting:
            # The first [clock] note travels INSIDE the greeting message: QA 19 Oct (suite_voice) found that a
            # session.thinking.append sent right after session.started suppressed the pending greeting (no audio
            # for 15+ s, startup failed) until the next clock note, a minute later, nudged the model.
            clock = f" {clock_note()}" if self.clock_context else ""
            config["input"] = [{"type": "message", "role": "developer", "content": [{
                "type": "input_text", "text": f"Greet now in natural Israeli Hebrew, warmly, with a smile in your "
                                               f"voice. Say: {self.greeting} Say your name as \"{self.spoken_name}\". "
                                               "Begin immediately, then pause and listen. Do not delegate this greeting."
                                               f"{clock}"}]}]
        return config

    async def wait_ready(self, timeout: float = 15) -> None:
        """A requested greeting is ready only after actual output audio, not merely session.started."""
        if not self.greeting:
            return
        audio = asyncio.create_task(self._first_audio.wait())
        ended = asyncio.create_task(self._ended.wait())
        try:
            await asyncio.wait({audio, ended}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not self._first_audio.is_set() or not self.running:
                raise RuntimeError("live greeting audio was not received; check voice/network logs")
        finally:
            audio.cancel()
            ended.cancel()
            await asyncio.gather(audio, ended, return_exceptions=True)

    def present(self) -> None:
        """Release buffered startup output and allow real microphone capture after the display connects."""
        if self.audio_io is not None:
            self._speaking_until = time.monotonic() + self.audio_io.queued_s()
            self._turn_audio_t0 = time.monotonic()
        self._presented.set()

    async def _state(self, s: str) -> None:
        if self.on_state:
            await self.on_state(s)

    def _usage_seconds(self) -> float:
        """Voice time for the daily budget (#13). The server's FINAL figure (session.closed) wins. Otherwise (network
        drop, crash, remote close without usage) count conservatively: the larger of the last reported usage and
        the wall-clock time since session.started, capped at the session limit + 60 s. Reported once (finalize)."""
        if self._usage_final:
            return self.usage_s
        if self._t_started is None:
            return self.usage_s
        elapsed = min(self._clock() - self._t_started, self.max_session_s + 60)
        return max(self.usage_s, elapsed)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._active:
            if self._end_task is None:
                raise RuntimeError("live session already running")
            await self.wait_ended()                 # a previous session is still finalizing
        self._reset_session()
        if self.budget is not None:
            self._credit = self.budget.reserve("ai_requests")   # raises BudgetExceeded: session not started
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
        self._spawn(self._idle_watchdog(), "idle")
        if self.clock_context:
            self._spawn(self._clock_loop(), "clock")
        self._t_ready = time.monotonic()
        self._heard_user = False
        self._last_user_voice = self._drop_until = 0.0
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

    async def _clock_loop(self) -> None:
        """Once a minute: the local time as SILENT context (no speech, no backend call). Never ends the session."""
        from datetime import datetime
        last = ""
        if self.greeting:
            # the greeting message already carries this minute's clock; send nothing until the greeting was spoken
            # (an extra event before it suppresses the greeting, see session_config)
            last = datetime.now().strftime("%Y%m%d%H%M")
            deadline = time.monotonic() + 45
            while self.running and time.monotonic() < deadline and (
                    not self._first_audio.is_set() or time.monotonic() < self._speaking_until + 1.0):
                await asyncio.sleep(0.25)
        while self.running:
            now = datetime.now()
            if now.strftime("%Y%m%d%H%M") != last and self.clock_context:
                last = now.strftime("%Y%m%d%H%M")
                try:
                    await self.conn.session.thinking.append(content=clock_note(now), delegation_id=None,
                                                            event_id=f"jarves_clock_{last}")
                except Exception as e:
                    logs.warn("live", "LIVE_CLOCK_FAILED", f"clock context not sent: {type(e).__name__}")
                    return
            await asyncio.sleep(60.5 - now.second - now.microsecond / 1e6)

    async def _progress_later(self, delegation_id: str | None) -> None:
        """A lookup that keeps the user waiting in silence gets a short spoken "almost there" (commentary): the first
        after progress_after_s of waiting, a second (different wording) only after PROGRESS_AGAIN_S more silence."""
        await asyncio.sleep(self.progress_after_s)
        deadline = time.monotonic() + 25
        notes, need = (PROGRESS_NOTE, PROGRESS_NOTE_AGAIN), PROGRESS_SILENCE_S
        sent, last_req = 0, 0.0
        while self.running and self._backend_busy and time.monotonic() < deadline and sent < len(notes):
            if self.progress_after_s <= 0:          # switched off (rejected by the server)
                return
            # QA 19 Oct (suite_voice): a note requested 50 ms before a fast continuation finished was spoken INSTEAD
            # of the answer ("almost ready", then nothing). Only a response that has itself run a while gets one,
            # and never while the user is being asked to approve something on the screen.
            t0 = getattr(self, "_backend_t0", None)
            young = t0 is None or time.monotonic() - t0 < PROGRESS_MIN_RESPONSE_S
            # never over the user's own speech (the user may keep talking while a lookup runs)
            if (not young and not self._confirm_pending and not self._in_text.strip()
                    and time.monotonic() - max(self._speaking_until, self._last_speech_t, last_req,
                                               self._last_user_voice) >= need):
                self._stats["progress_notes"] = self._stats.get("progress_notes", 0) + 1
                logs.info("live", "LIVE_PROGRESS", "slow lookup: short progress note requested", n=sent + 1)
                try:
                    await self.conn.session.commentary.append(content=notes[sent], delegation_id=None,
                                                              event_id=f"jarves_progress_{sent + 1}")
                except Exception as e:
                    logs.warn("live", "LIVE_PROGRESS_FAILED", f"progress note not sent: {type(e).__name__}")
                    return
                sent += 1
                need, last_req = PROGRESS_AGAIN_S, time.monotonic()     # silence counts from the request
            await asyncio.sleep(0.3)

    async def _watchdog(self) -> None:          # cost guard
        await asyncio.sleep(self.max_session_s)
        logs.info("live", "LIVE_TIME_LIMIT", "session time limit reached")
        self._request_end("timeout")

    async def _mic(self) -> None:
        silence = bytes(CHUNK_BYTES)
        # Live needs input audio (silence is sufficient) to produce its startup greeting. Do not open the
        # physical microphone while the desktop is still loading; the existing bounded output queue holds it.
        while self.running and not self._presented.is_set():
            await self.conn.session.input_audio.append(audio=base64.b64encode(silence).decode())
            await asyncio.sleep(0.1)
        from .wakeword import rms
        sent = silenced = 0
        peak = 0.0
        last_level = 0.0
        t_diag = time.monotonic()
        async for chunk in self.audio_io.mic_chunks(RATE, CHUNK_BYTES):
            if not self.running:
                return
            gated = self._gated()
            level = rms(chunk)
            if self.on_level is not None and time.monotonic() - last_level >= 0.2:
                last_level = time.monotonic()
                from .wakeword import level_meter
                await _call(self.on_level, 0.0 if gated else level_meter(chunk))
            if not gated and level > 0.015:
                self._last_energy = time.monotonic()      # bounded by ENERGY_EXTEND_S (see _idle_watchdog)
            if not gated and level > 0.02:
                self._last_user_voice = time.monotonic()
            await self.conn.session.input_audio.append(audio=base64.b64encode(silence if gated else chunk).decode())
            sent += 1
            silenced += gated
            peak = max(peak, 0.0 if gated else level)
            if time.monotonic() - t_diag >= 5:                # what the model actually receives (debugging aid)
                logs.info("live", "LIVE_MIC", "microphone audio sent to GPT-Live (last 5 s)", frames=sent,
                          silenced=silenced, peak_rms=round(peak, 3), input_closed=self._input_closed,
                          speaking=time.monotonic() < self._speaking_until,
                          gate_closed_for_s=round(max(0.0, self._speaking_until - time.monotonic()), 1),
                          model_audio_in_s=round(self._audio_in_s, 1), model_speech_s=round(self._speech_in_s, 1),
                          silence_rms_max=round(self._silence_rms_max, 4),
                          speaker_queue_s=round(self.audio_io.queued_s(), 1))
                self._audio_in_s = self._speech_in_s = self._silence_rms_max = 0.0
                sent = silenced = 0
                peak, t_diag = 0.0, time.monotonic()
            if self._speech_pending and time.monotonic() >= self._speaking_until:
                self._speech_pending = False
                await self._state("listening")

    def _gated(self) -> bool:                   # half-duplex echo guard on speakers; budget input close (F1)
        return self._input_closed or (self.half_duplex and time.monotonic() < self._speaking_until + self.echo_tail_s)

    async def _player(self) -> None:
        await self._presented.wait()
        await self.audio_io.run_player(RATE)

    def _drop_early(self, pcm: bytes) -> bool:
        """The model started (or keeps) speaking while the USER IS STILL TALKING (QA 19 Oct, 40 s story: it answered
        4 s in and the echo guard then muted the rest of the story, so the question never arrived). Do not play that
        audio and do not close the mic gate: the server hears the user continue, treats it as an interruption and
        answers the whole utterance afterwards. Silence chunks are never dropped here."""
        if _rms16(pcm) <= SPEECH_RMS:
            return False
        now = time.monotonic()
        if now - self._last_user_voice < USER_TALKING_S:
            self._drop_until = now + 0.6          # keep dropping the tail of that response for a moment
        elif now >= self._drop_until:
            return False
        self._stats["early_response_drops"] = self._stats.get("early_response_drops", 0) + 1
        if self._stats["early_response_drops"] == 1:
            logs.info("live", "LIVE_EARLY_RESPONSE", "model started answering while the user is still talking; "
                      "dropped it and kept the microphone open")
        return True

    def _hold_caption(self) -> bool:
        """Assistant text arriving now may belong to audio that _drop_early drops: during/just after a drop, or
        while the user is talking and JARVES is not audibly speaking yet. The audio/transcript deltas handled here
        carry no response id that ties text to audio, so this is time-based: text of a dropped answer that arrives
        more than DROP_CAPTION_TAIL_S after its audio can still be shown (limit, PRE_USE_TEST_REPORT_VOICE.md)."""
        now = time.monotonic()
        return now < self._drop_until + DROP_CAPTION_TAIL_S or (
            now - self._last_user_voice < USER_TALKING_S and now >= self._speaking_until)

    def _play(self, pcm: bytes) -> None:
        """Queue server audio. SILENCE (the continuous stream between utterances) must not mute the mic, count as
        activity, trigger "speaking" or build a playback backlog (that backlog was a permanent ~3.5 s delay)."""
        now = time.monotonic()
        dur = len(pcm) / 2 / RATE
        self._audio_in_s += dur
        if _rms16(pcm) > SPEECH_RMS:
            self.note_activity()
            self._speech_pending = True
            self._speech_in_s += dur
            if now > self._speaking_until:
                self._turn_audio_t0 = now
                self._note_reply_wait(now)
            self._speaking_until = max(self._speaking_until, now) + dur
            self._last_speech_t = now
            if self.audio_io is not None:
                self.audio_io.enqueue(pcm)
            if not self._first_audio.is_set():
                self._first_audio.set()
                logs.info("live", "LIVE_FIRST_AUDIO", "first live-session output audio received")
            return
        self._silence_rms_max = max(self._silence_rms_max, _rms16(pcm))
        if self.audio_io is not None and now - self._last_speech_t < PAUSE_KEEP_S:
            self.audio_io.enqueue(pcm)             # a natural pause inside the sentence: keep its length

    def _note_reply_wait(self, now: float) -> None:
        """History/diagnosis (store.py): silence from the user's last voice to the first audio of this reply."""
        heard = getattr(self, "_last_user_voice", 0.0)
        if heard and heard > getattr(self, "_wait_ref", 0.0):
            self._reply_wait_s = round(now - heard, 2)
        self._wait_ref = now

    def _take_reply_wait(self) -> dict:
        w, self._reply_wait_s = getattr(self, "_reply_wait_s", None), None
        return {} if w is None else {"wait_s": w}

    async def interrupt(self) -> None:
        """Barge-in: drop queued assistant speech immediately."""
        flushed = self.audio_io.flush() if self.audio_io is not None else 0
        self._speaking_until = 0.0
        self._speech_pending = False
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
            if self._t_started is None:
                self._t_started = self._clock()
            await self._health("ok", f"GPT-Live connected ({self.model} -> {self.backend_model})", "")
        elif t == "session.output_audio.delta":
            pcm = base64.b64decode(g("delta"))
            if self._drop_early(pcm):
                if self._held_text:                 # its words were never heard: no caption, no face reaction
                    self._stats["dropped_caption_chars"] = self._stats.get("dropped_caption_chars", 0) + len(self._held_text)
                    logs.debug("live", "LIVE_CAPTION_DROPPED", "caption of a dropped early answer discarded",
                               text=self._held_text.strip())
                    self._held_text = ""
                return
            audible = _rms16(pcm) > SPEECH_RMS
            if audible and time.monotonic() > self._speaking_until:
                await self._state("speaking")
            self._play(pcm)
            if audible and self._held_text:         # the held words are being spoken now: show them
                self._out_text += self._held_text
                self._held_text = ""
                await self._emote("jarves", self._out_text[-80:])
        elif t == "session.input_transcript.delta":
            self.note_activity()
            self._heard_user = True
            now = time.monotonic()
            if (not self._in_text and not self._gated() and now < self._speaking_until
                    and now - self._turn_audio_t0 >= BARGE_IN_MIN_S):
                await self.interrupt()          # new user utterance over still-queued assistant speech
            self._in_text += g("delta") or ""
            self._schedule_flush()
            if self._out_text:
                logs.info("live", "LIVE_JARVES_SAID", "JARVES said", text=self._out_text.strip(), **self._take_reply_wait())
                if self.on_transcript:
                    await self.on_transcript("assistant", self._out_text.strip())
                self._out_text = ""
        elif t == "session.output_transcript.delta":
            self.note_activity()
            if self._in_text:
                self._remember_user(self._in_text)
                logs.info("live", "LIVE_USER_SAID", "you said (as GPT-Live heard it)", text=self._in_text.strip())
                await self._emote("user", self._in_text)
                if self.on_transcript:
                    await self.on_transcript("user", self._in_text.strip())
                self._in_text = ""
            if self._hold_caption():
                # Issue #16: while the user is still talking an early answer's AUDIO is dropped (_drop_early); its
                # text must not reach the caption/face either. Held until its audio really plays, else discarded.
                self._held_text += g("delta") or ""
                self._schedule_flush()
                return
            self._out_text += g("delta") or ""
            await self._emote("jarves", self._out_text[-80:])
            self._schedule_flush()
        elif t == "session.delegation.created":
            logs.info("live", "LIVE_DELEGATION", "GPT-Live delegated work", target=str(_get(g("delegation"), "target")),
                      delegation=str(_get(g("delegation"), "id") or ""))
            if self.budget is not None and _get(g("delegation"), "target") == "responses":
                await self._take_credit("delegation")
        elif t == "response.event":
            await self._backend_event(g("event") or {}, g("delegation_id"))
        elif t == "session.usage.updated":
            self.usage_s = float(_get(g("usage"), "seconds", self.usage_s) or 0)
        elif t == "session.closed":
            usage = g("usage")
            if usage is not None:
                self.usage_s = float(_get(usage, "seconds", self.usage_s) or 0)
                self._usage_final = True
            server_reason = str(g("reason") or "")
            self._closed.set()
            if self._end_task is None:          # not requested by us
                logs.info("live", "LIVE_REMOTE_CLOSED", f"server closed the session ({server_reason or 'unknown'})")
                if server_reason in ("connection_lost", "content"):
                    await self._health("degraded", f"live session ended by the server ({server_reason})",
                                       "check the internet; tap to start a new conversation")
                self._request_end(CLOSE_REASONS.get(server_reason, "remote_closed"))
        elif t == "error":
            cid = str(_get(g("error"), "client_event_id") or g("client_event_id") or "")
            if cid.startswith(("jarves_clock", "jarves_progress", "jarves_stall", "jarves_task")):
                # optional extras: clock/progress are switched off; stall/task notes are only logged; stay healthy
                code, msg, _ = error_info(g("error"))
                logs.warn("live", "LIVE_EXTRA_REJECTED", f"{cid.split('_')[1]} context rejected ({code}): {msg}")
                self._rejected = (getattr(self, "_rejected", set()) | {cid})
                if cid.startswith("jarves_clock"):
                    self.clock_context = False
                elif cid.startswith("jarves_progress"):
                    self.progress_after_s = 0.0
                return
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

    async def _take_credit(self, where: str) -> None:
        """The server started a delegated backend response: the held credit pays for it; reserve the next."""
        if self._credit is not None:
            self._pending.append(self._credit)
            self._credit = None
        else:                                       # server started it although no credit was held
            self._stats["budget_overrun"] += 1
            logs.warn("live", "AI_BUDGET_OVERRUN", "a delegated request started with no reserved credit; "
                      "ending the session now", overruns=self._stats["budget_overrun"])
            self._pending.append(_Counted(self.budget))
            self._input_closed = True
            await self._budget_exhausted(where)     # close now: don't wait for answers that may start more work
            return
        try:
            self._credit = self.budget.reserve("ai_requests")
        except BudgetExceeded:
            self._input_closed = True               # no new user turns -> no new delegations
            self._end_after_answer = True
            logs.warn("live", "AI_BUDGET", f"no budget for another request ({where}); mic input closed, "
                      "ending after this answer", action="raise ai.daily_request_limit")

    def _schedule_flush(self, delay: float = 2.0) -> None:
        """Log what was said once the speaker has been quiet for `delay` s (instead of at the next turn)."""
        if self._flush_task is not None:
            self._flush_task.cancel()
        self._flush_task = asyncio.get_running_loop().create_task(self._flush_later(delay))

    async def _flush_later(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._flush_task = None                 # a new delta must not cancel the send below
        # QA 19 Oct (suite_voice): the answer text reached the face only when the user spoke AGAIN (one turn late,
        # the last answer never). The quiet-time flush now also sends it to the screen.
        for role, text in self._flush_transcripts():
            if self.on_transcript is not None and self.running:
                try:
                    await self.on_transcript(role, text)
                except Exception as e:
                    logs.debug("live", "LIVE_TRANSCRIPT_SEND_FAILED", f"transcript not sent: {type(e).__name__}")

    def _flush_transcripts(self) -> list[tuple[str, str]]:
        out = []
        if self._held_text.strip():             # text whose audio never played: it was not said, do not caption it
            self._stats["dropped_caption_chars"] = self._stats.get("dropped_caption_chars", 0) + len(self._held_text)
            logs.debug("live", "LIVE_UNSPOKEN_TEXT", "assistant text without played audio not shown",
                       text=self._held_text.strip())
        self._held_text = ""
        if self._in_text.strip():
            self._remember_user(self._in_text)
            logs.info("live", "LIVE_USER_SAID", "you said (as GPT-Live heard it)", text=self._in_text.strip())
            out.append(("user", self._in_text.strip()))
            self._in_text = ""
        if self._out_text.strip():
            logs.info("live", "LIVE_JARVES_SAID", "JARVES said", text=self._out_text.strip(), **self._take_reply_wait())
            out.append(("assistant", self._out_text.strip()))
            self._out_text = ""
        return out

    async def _emote(self, role: str, text: str) -> None:
        """Face reaction to what was said (laugh at a joke, cry at sad news...). Same reaction at most every 5 s."""
        if self.on_emotion is None:
            return
        from ..emotion import detect_emotion
        emo = detect_emotion(text, role)
        now = time.monotonic()
        if emo and not (self._last_emotion[0] == emo and now - self._last_emotion[1] < 5.0):
            self._last_emotion = (emo, now)
            logs.info("live", "LIVE_EMOTION", f"reacting: {emo}", role=role)
            await _call(self.on_emotion, emo)

    async def _activity(self, kind: str, text: str = "", detail: str = "") -> None:
        """What JARVES is doing, for the screen (thinking / searching / music ...). kind "done" clears it."""
        self._activity_gen += 1
        if self.on_activity is not None:
            await _call(self.on_activity, kind, text, detail)

    async def _tool_activity(self, name: str, res) -> None:
        """Screen text for a Skill call (res=None: starting; else: finished)."""
        if name.startswith("music_"):
            if res is not None and res.ok and name == "music_play":
                np = ((res.data or {}).get("now_playing") or {})
                await self._activity("music", "מנגן", f"{np.get('title', '')} – {np.get('artist', '')}".strip(" –"))
            elif res is None:
                await self._activity("music", {"music_play": "מחפש שיר…", "music_next": "השיר הבא…",
                                               "music_pause": "משהה…", "music_resume": "ממשיך…",
                                               "music_volume": "מכוונן עוצמה…"}.get(name, "מוזיקה…"))
        elif res is None:
            kind, text = ({"get_time": ("time", "בודק שעה…"), "remember": ("memory", "זוכר…"),
                           "recall": ("memory", "נזכר…"), "forget": ("memory", "מוחק מהזיכרון…"),
                           "forget_all": ("memory", "מוחק מהזיכרון…"),
                           "task_start": ("tool", "מתחיל משימה ברקע…"), "task_status": ("tool", "בודק משימות…"),
                           "task_cancel": ("tool", "עוצר משימה…")}.get(name, ("tool", "עובד על זה…")))
            await self._activity(kind, text)

    async def _clear_activity_later(self, delay: float = 3.5) -> None:
        gen = self._activity_gen
        await asyncio.sleep(delay)
        if gen == self._activity_gen and self.running:
            await self._activity("done")

    async def _budget_exhausted(self, where: str) -> None:
        logs.warn("live", "AI_BUDGET", f"daily AI request limit reached ({where}); ending live session",
                  action="raise ai.daily_request_limit")
        await self._health("degraded", "daily AI request limit reached; live voice ended",
                           "raise ai.daily_request_limit or wait until tomorrow")
        self._request_end("budget")

    def _remember_user(self, text: str) -> None:
        self._user_turns = (self._user_turns + [text.strip()])[-3:]
        if hasattr(self.registry, "new_turn"):
            self.registry.new_turn()       # WP-G: the user spoke again -> side effects allowed after mail/calendar text

    async def _show(self, kind: str, text: str) -> None:
        """Backend stage on the screen; the elapsed-time ticker refreshes its detail line."""
        self._shown = (kind, text)
        await self._activity(kind, text, self._elapsed_detail())

    def _elapsed_detail(self) -> str:
        t0 = getattr(self, "_busy_t0", None)
        s = int(time.monotonic() - t0) if t0 is not None else 0
        return f"{s} שנ׳" if s >= 3 else ""

    async def _tick_activity(self) -> None:
        """While the backend works: refresh the pill's detail with the elapsed time every 2 s (no text change, so
        the face does not blink) and enforce the hard deadline."""
        while self.running and self._backend_busy:
            await asyncio.sleep(2.0 if not self.backend_deadline_s else min(2.0, self.backend_deadline_s / 4))
            if not (self.running and self._backend_busy):
                return
            kind, text = self._shown
            if kind:
                await self._activity(kind, text, self._elapsed_detail())
            t0 = getattr(self, "_backend_t0", None)
            # a Skill running or an approval waiting on the screen is not a backend stall
            if self._tools_running or self._confirm_pending:
                self._backend_t0 = time.monotonic()
            elif self.backend_deadline_s and t0 is not None and time.monotonic() - t0 >= self.backend_deadline_s:
                self._backend_stalled(getattr(self, "_busy_delegation", None))
                return

    def _backend_stalled(self, delegation_id) -> None:
        tr = self._trace
        self._abandoned.add(delegation_id)
        self._backend_busy = False                  # progress notes stop; the user is not kept waiting
        self._backend_t0 = None
        self._stats["backend_stalls"] = self._stats.get("backend_stalls", 0) + 1
        logs.warn("live", "LIVE_BACKEND_STALL", "backend lookup got no result before the deadline; giving up on it",
                  deadline_s=self.backend_deadline_s, stage=getattr(tr, "stage", ""), delegation=delegation_id,
                  query="; ".join(getattr(tr, "queries", []) or []))
        self._retry_task = self._spawn(self._retry_stalled(tr), "stall-retry")

    async def _retry_stalled(self, tr) -> None:
        """One retry of a stalled lookup in the Core (hard deadline), then tell GPT-Live the honest outcome."""
        turns = self._user_turns + ([self._in_text.strip()] if self._in_text.strip() else [])
        question = turns[-1] if turns else ""
        if len(turns) > 1:
            question = "Earlier: " + " | ".join(turns[:-1]) + "\nAnswer this now: " + turns[-1]
        if tr is not None and tr.queries:
            question += f"\n(search hint: {tr.queries[-1]})"
        answer = ""
        if self.retry_lookup is not None and question:
            logs.info("live", "LIVE_BACKEND_RETRY", "retrying the stalled lookup once in the Core",
                      deadline_s=RETRY_DEADLINE_S)
            await self._show("searching", "מנסה שוב…")
            self._tools_running += 1                # keeps the idle watchdog from closing the session meanwhile
            t0 = time.monotonic()
            try:
                answer = str(await asyncio.wait_for(self.retry_lookup(question), RETRY_DEADLINE_S) or "").strip()
                logs.info("live", "LIVE_BACKEND_RETRY_OK", "retry answered", retry_s=round(time.monotonic() - t0, 1))
            except asyncio.CancelledError:
                logs.info("live", "LIVE_BACKEND_RETRY_CANCELLED", "retry cancelled: the original answer arrived")
                raise
            except Exception as e:
                logs.warn("live", "LIVE_BACKEND_RETRY_FAILED", f"retry failed: {type(e).__name__}",
                          retry_s=round(time.monotonic() - t0, 1), detail=str(e)[:120])
            finally:
                self._tools_running = max(0, self._tools_running - 1)
        content = STALL_RESULT_NOTE.format(answer=answer[:600]) if answer else STALL_FAIL_NOTE
        self._stats["stall_answers" if answer else "stall_failures"] = self._stats.get(
            "stall_answers" if answer else "stall_failures", 0) + 1
        await self._send_note(content, str(self._stats["backend_stalls"]), prefix="jarves_stall")
        await self._activity("done")

    async def announce(self, content: str, *, max_wait_s: float = 300.0, tag: str = "") -> bool:
        """Speak a finished background task's result (tasks.TaskManager) at a GOOD moment: never over the user's
        speech, never over JARVES's own, not while a lookup/tool/approval is in flight. Sent as commentary (the
        model words it naturally). False when the session ended (or max_wait_s passed) first: the caller keeps it."""
        deadline = time.monotonic() + max_wait_s
        while self.running and self.conn is not None and time.monotonic() < deadline:
            now = time.monotonic()
            audio = self.audio_io
            busy = (self._backend_busy or self._tools_running or self._confirm_pending or self._in_text.strip()
                    or now - self._last_user_voice < ANNOUNCE_USER_QUIET_S or now < self._speaking_until + 0.8
                    or (audio is not None and audio.queued_s() > 0))
            if not busy:
                return await self._send_note(content, tag)
            await asyncio.sleep(0.25)
        return False

    async def _send_note(self, content: str, tag: str, prefix: str = "jarves_task") -> bool:
        """Commentary that must arrive: the server validates it AFTER the send (error with our event id, e.g. "must
        not exceed 500 tokens"), so wait briefly for a rejection and resend once shortened (notes put their
        instructions first and the result last, so the cut only shortens the result)."""
        content = content[:NOTE_MAX_CHARS]
        for attempt in (1, 2):
            n = self._stats["announcements"] = self._stats.get("announcements", 0) + 1
            eid = f"{prefix}_{tag or 'x'}_{n}"
            try:
                await self.conn.session.commentary.append(content=content, delegation_id=None, event_id=eid)
            except Exception as e:
                logs.warn("live", "LIVE_ANNOUNCE_FAILED", f"announcement not sent: {type(e).__name__}")
                return False
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.5 and eid not in getattr(self, "_rejected", set()) and self.running:
                await asyncio.sleep(0.1)
            if eid not in getattr(self, "_rejected", set()):
                logs.info("live", "LIVE_ANNOUNCE" if prefix == "jarves_task" else "LIVE_NOTE",
                          "note handed to GPT-Live", task=tag, chars=len(content))
                self.note_activity()
                return True
            content = content[: len(content) // 2]
        return False

    async def _backend_event(self, inner: dict, delegation_id: str | None) -> None:
        it = inner.get("type") or ""
        tr = self._trace
        if delegation_id is not None and delegation_id in self._abandoned:
            # a delegation given up after the deadline: only log what it does now (its late answer is spoken by
            # GPT-Live; the Core retry is then cancelled so the user does not hear the answer twice)
            if it in ("response.completed", "response.failed", "response.incomplete"):
                self._abandoned.discard(delegation_id)
                self._stats["backend_responses"] += 1
                logs.warn("live", "LIVE_BACKEND_LATE", "a stalled backend response finished after the deadline",
                          delegation=delegation_id, status=it.split(".")[-1])
                if self._retry_task is not None and not self._retry_task.done() and it == "response.completed":
                    self._retry_task.cancel()
                self._settle_one()
            return
        if it == "response.created":
            from ..brain import BackendTrace
            tr = self._trace = BackendTrace("live", "LIVE_BACKEND", delegation=delegation_id)
            self._backend_t0 = time.monotonic()
            if not getattr(self, "_backend_busy", False) or getattr(self, "_busy_delegation", None) != delegation_id:
                self._busy_t0 = self._backend_t0    # elapsed on screen counts from the delegation's first response
            self._busy_delegation = delegation_id
            self._backend_busy = True
            if self._ticker is None or self._ticker.done():
                self._ticker = self._spawn(self._tick_activity(), "backend-ticker")
            if self.progress_after_s > 0 and delegation_id != getattr(self, "_progress_for", None):
                self._progress_for = delegation_id          # at most one progress note per delegation
                self._spawn(self._progress_later(delegation_id), "progress")
            self.note_activity()
            await self._state("thinking")
        shown = tr.feed(inner) if tr is not None else None
        if shown is not None:
            await self._show(*shown)
        if it in ("response.completed", "response.failed", "response.incomplete"):
            self._backend_busy = False
            self.note_activity()
            # One delegated backend response finished (billed whether or not it succeeded) -> count it.
            self._stats["backend_responses"] += 1
            self._backend_t0 = None
            self._trace = None
            self._spawn(self._clear_activity_later(), "activity-clear")
            self._settle_one()
            if it == "response.failed":
                await self._on_error(_get(inner.get("response"), "error") or {"code": "response_failed"}, True)
            if self._end_after_answer and not self._pending:
                await self._budget_exhausted("last reserved answer finished")
        elif it == "response.output_item.done":
            item = inner.get("item") or {}
            if item.get("type") != "function_call":
                return
            name, call_id = item.get("name", ""), item.get("call_id", "")
            logs.info("live", "LIVE_TOOL_CALL", f"backend called {name}", delegation=delegation_id)
            nxt = None
            if self.budget is not None:
                try:
                    nxt = self.budget.reserve("ai_requests")    # the continuation is reserved BEFORE it is sent
                except BudgetExceeded:
                    res = ToolResult(False, "daily AI request limit reached; the conversation will end now",
                                     code="AI_BUDGET")
                    await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                               "output": res.to_model()})
                    await self._budget_exhausted(f"tool {name}")   # no response.create: that would be paid
                    return
                self._pending.append(nxt)       # FIFO after this response's own unit; an end-after-answer waits
            # QA 19 Oct (suite_voice): the Skill ran INSIDE the event loop, so a 20 s confirmation (or a slow tool)
            # froze all audio, transcripts and usage events. It now runs in its own task.
            self._tools_running += 1
            state = {"sent": False}
            t = self._spawn(self._run_tool(name, call_id, item.get("arguments") or "{}", state), f"tool-{name}")
            t.add_done_callback(lambda task: self._tool_finished(task, nxt, state))
        elif it == "error":
            await self._on_error(inner.get("error") or inner, True)

    def _settle_one(self) -> None:
        """A delegated backend response finished: commit its reservation (FIFO)."""
        if self.budget is not None and not self._budget_settled:
            if self._pending:
                self._pending.pop(0).commit()
            else:                               # a backend response we never saw start: count it anyway
                self._stats["budget_overrun"] += 1
                self.budget.record("ai_requests", 1)

    async def _run_tool(self, name: str, call_id: str, arguments: str, state: dict) -> None:
        await self._tool_activity(name, None)
        res = await self.registry.call(name, arguments)
        await self._tool_activity(name, res)
        await self.conn.response.item.create(item={"type": "function_call_output", "call_id": call_id,
                                                   "output": res.to_model()})
        state["sent"] = True
        await self.conn.response.create()

    def _tool_finished(self, task: asyncio.Task, nxt, state: dict) -> None:
        """Runs even when the task was cancelled before it started: settle the reserved continuation."""
        self._tools_running = max(0, self._tools_running - 1)
        self.note_activity()
        failed = task.cancelled() or task.exception() is not None
        if failed and nxt is not None and nxt in self._pending:
            self._pending.remove(nxt)
            nxt.commit() if state["sent"] else nxt.release()   # outcome unknown -> counted; never sent -> freed

    async def confirm_pending(self, prompt: str) -> None:
        """The Core is asking the user to approve a destructive tool on the screen. If it is still unanswered after
        CONFIRM_NOTE_AFTER_S, JARVES says so (once). No progress notes meanwhile. QA 19 Oct: a note sent at once was
        spoken INSTEAD of the result when the user approved quickly, so a fast answer gets no note at all."""
        self._confirm_pending = True
        self._confirm_gen = getattr(self, "_confirm_gen", 0) + 1
        if self.running and self.conn is not None:
            self._spawn(self._confirm_note_later(self._confirm_gen), "confirm-note")

    async def _confirm_note_later(self, gen: int) -> None:
        await asyncio.sleep(CONFIRM_NOTE_AFTER_S)
        if not (self.running and self._confirm_pending and gen == getattr(self, "_confirm_gen", 0)):
            return
        try:
            await self.conn.session.commentary.append(content=CONFIRM_NOTE, delegation_id=None,
                                                      event_id="jarves_progress_confirm")
            logs.info("live", "LIVE_CONFIRM_NOTE", "asked the user to approve on the screen")
        except Exception as e:
            logs.warn("live", "LIVE_PROGRESS_FAILED", f"confirmation note not sent: {type(e).__name__}")

    def confirm_done(self) -> None:
        self._confirm_pending = False
        self.note_activity()
