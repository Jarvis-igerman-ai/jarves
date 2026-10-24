"""Background tasks (SW-V2 WP-C, Issue #17 item 6): long research / web lookups run in the Core while the live
conversation goes on; the result is announced at a good moment and listed on the screen and the dashboard.

- `ResearchRunner`: one backend request (OpenAI Responses API + hosted web search) with a HARD per-attempt deadline
  and ONE retry. Every stage is traced (brain.BackendTrace) to the log and reported as progress. It also answers
  the GPT-Live stall retry (voice/live.py `retry_lookup`). `PlanResearchRunner` does the same on the ChatGPT plan.
- `TaskManager`: bounded concurrency (default 3 running + 5 queued), one budget unit RESERVED at start (refused
  when the daily ceiling is reached), cancellation, an overall timeout, finished tasks appended to
  `<home>/tasks.jsonl` (one JSON object per line; the dashboard reads it with `read_history`).
- `TaskSkill`: the tools `task_start(description)`, `task_status(task_id?)`, `task_cancel(task_id?)` for the
  backend model (GPT-Live delegation, typed chat).

Screen message (Core -> face), sent on every change: `tasks.update {"tasks": [view, ...]}` where a view is
`{id, description, status, step, elapsed_s, created, finished, result, error}`; status is one of
queued | running | done | failed | timeout | cancelled. See docs/reviews/SWV2_WP-C.md.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from . import logs
from .brain import BackendTrace, _d, short, speakable, usage_fields
from .budget import BudgetExceeded
from .skills import Skill, Tool, ToolResult

ProgressFn = Callable[[str, str], None]          # (kind, text) - face pill kinds: thinking/searching/reading

RESEARCH_INSTRUCTIONS = """You are the research helper of {name}, a home robot that speaks Hebrew. You run in the
background: research the user's request properly with web search (several sources when it matters), then write
the result for SPEAKING: natural Hebrew, at most four short sentences, the key facts and numbers first, no lists,
no markdown, no URLs; name a source in two or three words only when it matters. If you could not find it, say so
plainly in one sentence."""

LOOKUP_INSTRUCTIONS = """Answer the user's question for {name}, a home robot that speaks Hebrew: use web search for
current facts, then reply in one or two short natural spoken Hebrew sentences, no markdown, no URLs, no "let me
check". If you cannot find it, say so in one sentence."""

ANNOUNCE_RESULT_CHARS = 600       # Hebrew is ~2-3 characters per token; the whole note must stay under 500 tokens
TEXT_KEYS = ("description", "result", "queries", "steps")   # what the user asked / what came back (content)
STORE_MAX_BYTES = 1_000_000     # tasks.jsonl is trimmed to its newest STORE_KEEP lines beyond this
STORE_KEEP = 500
ACTIVE = ("queued", "running")
FINISHED = ("done", "failed", "timeout", "cancelled")


class LookupFailed(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class LookupResult:
    text: str
    attempts: int = 1
    usage: dict = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)


async def _maybe(cb: Callable | None, *args: Any) -> None:
    if cb is None:
        return
    r = cb(*args)
    if inspect.isawaitable(r):
        await r


class ResearchRunner:
    """Backend research over the API key (OpenAI Responses API, streamed, store=false).

    Each attempt reserves one `ai_requests` unit BEFORE it is sent (the first may use a reservation the caller
    already holds) and commits it afterwards; it is bounded by `attempt_s`; a failed/timed-out attempt is retried
    `retries` times. Reasoning summaries (when the model reasons) are requested and logged at DEBUG."""

    def __init__(self, model: str, instructions: str, *, budget=None, client=None, web_search: bool = True,
                 reasoning_effort: str = "low", reasoning_summary: str = "auto", service_tier: str = "",
                 attempt_s: float = 60.0, retries: int = 1, area: str = "tasks", prefix: str = "TASK_BACKEND") -> None:
        self.model, self.instructions, self.budget, self._client = model, instructions, budget, client
        self.web_search, self.reasoning_effort, self.reasoning_summary = web_search, reasoning_effort, reasoning_summary
        self.service_tier, self.attempt_s, self.retries = service_tier, attempt_s, retries
        self.area, self.prefix = area, prefix

    def _body(self, question: str) -> dict:
        body: dict[str, Any] = {"model": self.model, "instructions": self.instructions, "input": question,
                                "store": False, "stream": True}
        if self.web_search:
            body["tools"] = [{"type": "web_search"}]
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
            if self.reasoning_summary and self.reasoning_effort != "none":
                body["reasoning"]["summary"] = self.reasoning_summary
        if self.service_tier:
            body["service_tier"] = self.service_tier
        return body

    async def __call__(self, question: str, on_progress: ProgressFn | None = None, *, reservation=None) -> LookupResult:
        last = LookupFailed("no_attempt")
        for n in range(1, self.retries + 2):
            res = reservation if n == 1 else None
            try:
                if res is None and self.budget is not None:
                    res = self.budget.reserve("ai_requests")
            except BudgetExceeded as e:
                raise LookupFailed("AI_BUDGET", str(e)) from e
            trace = BackendTrace(self.area, self.prefix, attempt=n)
            try:
                text = await asyncio.wait_for(self._attempt(question, trace, on_progress), self.attempt_s)
                return LookupResult(text, n, trace.usage, trace.queries, trace.reasoning)
            except asyncio.TimeoutError:
                trace.finish("timeout")
                last = LookupFailed("timeout", f"no answer within {self.attempt_s:g} s")
            except LookupFailed as e:
                trace.finish("failed")
                last = e
            except Exception as e:      # network / HTTP / SDK errors: retried once, then reported
                trace.finish("failed")
                last = LookupFailed(type(e).__name__, str(e)[:160])
            finally:
                if res is not None:
                    res.commit()        # sent or unknown -> counted (conservative)
            logs.warn(self.area, "LOOKUP_ATTEMPT_FAILED", "backend attempt failed", error_code=last.code,
                      attempt=n, retry=n <= self.retries)
            if n <= self.retries and on_progress is not None:
                on_progress("searching", "מנסה שוב…")
        raise last

    async def _attempt(self, question: str, trace: BackendTrace, on_progress: ProgressFn | None) -> str:
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI()
        stream = await self._client.responses.create(**self._body(question))
        parts: list[str] = []
        try:
            async for ev in stream:
                d = _d(ev)
                shown = trace.feed(d)
                if shown is not None and on_progress is not None:
                    on_progress(*shown)
                t = d.get("type")
                if t == "response.output_text.delta":
                    parts.append(str(d.get("delta") or ""))
                elif t in ("response.failed", "error"):
                    raise LookupFailed(trace.error or "response_failed")
                elif t == "response.incomplete" and not parts:
                    raise LookupFailed(trace.error or "incomplete")
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass
        text = speakable("".join(parts))
        if not text:
            raise LookupFailed("empty", "the backend returned no text")
        return text


class PlanResearchRunner:
    """The same contract on the user's ChatGPT plan (ai.connection = "chatgpt": no API-key spending at all).
    Budget is reserved per request inside ChatGPTBrain._stream; a caller's reservation is released unused."""

    def __init__(self, brain, instructions: str, *, attempt_s: float = 60.0, retries: int = 1) -> None:
        self.brain, self.instructions, self.attempt_s, self.retries = brain, instructions, attempt_s, retries

    async def __call__(self, question: str, on_progress: ProgressFn | None = None, *, reservation=None) -> LookupResult:
        if reservation is not None:
            reservation.release()
        last = LookupFailed("no_attempt")
        trace = BackendTrace("tasks", "TASK_PLAN", quiet=True)     # ChatGPTBrain._stream logs this stream

        def on_event(ev: dict) -> None:
            shown = trace.feed(ev)
            if shown is not None and on_progress is not None:
                on_progress(*shown)
        for n in range(1, self.retries + 2):
            try:
                text = await asyncio.wait_for(self.brain.lookup(question, self.instructions, on_event), self.attempt_s)
                if text:
                    return LookupResult(speakable(text), n, trace.usage, trace.queries, trace.reasoning)
                last = LookupFailed("empty")
            except asyncio.TimeoutError:
                last = LookupFailed("timeout")
            except BudgetExceeded as e:
                raise LookupFailed("AI_BUDGET", str(e)) from e
            except Exception as e:
                last = LookupFailed(getattr(e, "code", None) or type(e).__name__, str(e)[:160])
            logs.warn("tasks", "LOOKUP_ATTEMPT_FAILED", "plan attempt failed", error_code=last.code, attempt=n)
        raise last


# ------------------------------------------------------------------ tasks
@dataclass
class Task:
    id: str
    description: str
    status: str = "queued"
    created: float = 0.0
    started: float = 0.0
    finished: float = 0.0
    result: str = ""
    error: str = ""
    step: str = ""
    steps: list[str] = field(default_factory=list)
    attempts: int = 0
    usage: dict = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    announced: bool = False

    def elapsed(self, now: float | None = None) -> float:
        if not self.started:
            return 0.0
        return round((self.finished or (now or time.time())) - self.started, 1)

    def view(self) -> dict:
        return {"id": self.id, "description": short(self.description, 120), "status": self.status, "step": self.step,
                "elapsed_s": self.elapsed(), "created": self.created, "finished": self.finished,
                "result": short(self.result, 300), "error": self.error}

    def record(self) -> dict:
        d = asdict(self)
        d["duration_s"] = self.elapsed()
        return d


class TaskRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def announcement(task: Task) -> str:
    """Commentary for GPT-Live (voice/live.py announce): it words the result naturally."""
    # Instructions FIRST, the result LAST: commentary is limited to 500 tokens (real GPT-Live run 19 Oct rejected
    # a 1200-character Hebrew result), and live.announce() shortens a rejected note from the end.
    if task.status == "done":
        return ("[task] A background task the user asked for has finished. At this natural pause tell the user, in "
                "one to three short natural Hebrew sentences, that the background check is ready and give the "
                "result. Do not delegate it again and do not search again.\n"
                f"Task: {short(task.description, 120)}\nResult: {task.result[:ANNOUNCE_RESULT_CHARS]}")
    return ("[task] A background task could not be finished. Tell the user briefly and honestly in Hebrew, and ask "
            f"whether to try again. Do not delegate now.\nTask: {short(task.description, 120)} "
            f"({task.error or task.status})")


class TaskManager:
    def __init__(self, runner: Callable[..., Awaitable[LookupResult]], *, budget=None, store: Path | None = None,
                 max_running: int = 3, max_queued: int = 5, timeout_s: float = 180.0,
                 on_update: Callable[[list[dict]], Any] | None = None,
                 on_finished: Callable[[Task], Any] | None = None, clock=time.time) -> None:
        self.runner, self.budget, self.store = runner, budget, store
        self.max_running, self.max_queued, self.timeout_s = max_running, max_queued, timeout_s
        self.on_update, self.on_finished, self._clock = on_update, on_finished, clock
        self.tasks: dict[str, Task] = {}
        self._jobs: dict[str, asyncio.Task] = {}
        self._callbacks: set[asyncio.Task] = set()
        self._sem = asyncio.Semaphore(max_running)
        self._closed = False

    # ------------------------------------------------------------------ queries
    def active(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status in ACTIVE]

    def find(self, task_id: str = "") -> Task | None:
        """By id (prefix ok); "" = the newest active task, else the newest task."""
        if task_id:
            hits = [t for t in self.tasks.values() if t.id.startswith(task_id.strip())]
            return hits[-1] if hits else None
        act = self.active()
        return act[-1] if act else (list(self.tasks.values())[-1] if self.tasks else None)

    def views(self, recent: int = 5) -> list[dict]:
        done = [t for t in self.tasks.values() if t.status in FINISHED][-recent:]
        return [t.view() for t in self.active() + done]

    def unannounced(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status in ("done", "failed", "timeout") and not t.announced]

    def mark_announced(self, task: Task) -> None:
        task.announced = True
        logs.info("tasks", "TASK_ANNOUNCED", "background task result announced", task_id=task.id)
        self._persist(task)

    # ------------------------------------------------------------------ control
    async def start(self, description: str) -> Task:
        description = " ".join(str(description or "").split())
        if self._closed:
            raise TaskRefused("TASK_SHUTDOWN", "JARVES is shutting down")
        if not description:
            raise TaskRefused("TASK_EMPTY", "the task needs a description")
        if len(self.active()) >= self.max_running + self.max_queued:
            from . import limits
            limits.report("TASK_LIMIT", f"{len(self.active())} active, max {self.max_running} running + {self.max_queued} queued",
                          self.max_running)
            raise TaskRefused("TASK_LIMIT", f"already {len(self.active())} background tasks; wait for one to finish "
                                            "or cancel one")
        res = None
        if self.budget is not None:
            try:
                res = self.budget.reserve("ai_requests")     # the first attempt is paid for before it is queued
            except BudgetExceeded as e:
                raise TaskRefused("AI_BUDGET", f"daily AI request limit reached ({e})") from e
        task = Task(uuid.uuid4().hex[:6], description, created=self._clock())
        self.tasks[task.id] = task
        self._jobs[task.id] = asyncio.get_running_loop().create_task(self._run(task, res), name=f"task.{task.id}")
        logs.info("tasks", "TASK_STARTED", "background task queued", task_id=task.id, text=short(description, 120),
                  active=len(self.active()))
        self._emit()
        return task

    async def cancel(self, task_id: str = "") -> Task | None:
        task = self.find(task_id)
        if task is None or task.status not in ACTIVE:
            return task
        job = self._jobs.get(task.id)
        if job is not None:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        return task

    async def shutdown(self) -> None:
        self._closed = True
        jobs = [j for j in self._jobs.values() if not j.done()]
        for j in jobs:
            j.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        cbs = [c for c in self._callbacks if not c.done()]
        for c in cbs:
            c.cancel()
        await asyncio.gather(*cbs, return_exceptions=True)

    # ------------------------------------------------------------------ running
    async def _run(self, task: Task, res) -> None:
        handed = False

        def progress(kind: str, text: str) -> None:
            if text and text != task.step:
                task.step = text
                task.steps = (task.steps + [text])[-20:]
                logs.debug("tasks", "TASK_PROGRESS", "background task progress", task_id=task.id, text=text,
                           elapsed_s=task.elapsed())
                self._emit()
        try:
            async with self._sem:
                task.status, task.started = "running", self._clock()
                logs.info("tasks", "TASK_RUNNING", "background task running", task_id=task.id)
                self._emit()
                handed = True               # the runner owns (and settles) the reservation from here on
                out = await asyncio.wait_for(self.runner(task.description, progress, reservation=res), self.timeout_s)
                task.status, task.result = "done", out.text
                task.attempts, task.usage, task.queries = out.attempts, out.usage, out.queries
        except asyncio.CancelledError:
            task.status, task.error = "cancelled", "cancelled"
        except asyncio.TimeoutError:
            task.status, task.error = "timeout", f"no result within {self.timeout_s:g} s"
            from . import limits
            limits.report("TASK_TIMEOUT", f"{self.timeout_s:g} s", self.timeout_s)
        except LookupFailed as e:
            task.status, task.error = "failed", e.code
        except Exception as e:
            task.status, task.error = "failed", type(e).__name__
            logs.error("tasks", "TASK_CRASHED", "background task crashed", **logs.error_fields(e))
        finally:
            if res is not None and not handed:
                res.release()               # never sent (cancelled while queued)
            task.finished = self._clock()
            task.step = ""
            self._jobs.pop(task.id, None)
        level = logs.info if task.status == "done" else logs.warn
        level("tasks", f"TASK_{task.status.upper()}", "background task finished", status=task.status, task_id=task.id,
              duration_s=task.elapsed(), attempts=task.attempts, error_code=task.error, **usage_fields(task.usage))
        self._persist(task)
        self._emit()
        if task.status != "cancelled" and self.on_finished is not None and not self._closed:
            self._callback(self.on_finished, task)

    def _callback(self, cb: Callable, *args: Any) -> None:
        try:
            c = asyncio.get_running_loop().create_task(_maybe(cb, *args))
        except RuntimeError:
            return
        self._callbacks.add(c)
        c.add_done_callback(self._callback_done)

    def _callback_done(self, c: asyncio.Task) -> None:
        self._callbacks.discard(c)
        if not c.cancelled() and c.exception() is not None:
            logs.warn("tasks", "TASK_CALLBACK_FAILED", "task callback failed", error_type=type(c.exception()).__name__)

    def _emit(self) -> None:
        if self.on_update is not None:
            self._callback(self.on_update, self.views())

    def _persist(self, task: Task) -> None:
        """Append the finished task to tasks.jsonl (an update of an already stored task appends a newer line;
        readers keep the last line per id)."""
        if self.store is None or task.status not in FINISHED:
            return
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            rec = logs.redact_deep(task.record())          # never a secret, whatever the mode
            if logs.privacy():      # SW-V4 B: content reaches disk only with privacy off (lengths kept)
                rec = {**rec, **{k: logs._mask(rec[k]) for k in TEXT_KEYS if rec.get(k)}}
            with self.store.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if self.store.stat().st_size > STORE_MAX_BYTES:
                lines = self.store.read_text(encoding="utf-8").splitlines()[-STORE_KEEP:]
                self.store.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as e:
            logs.warn("tasks", "TASK_STORE_FAILED", "could not save the task", error_type=type(e).__name__)


def read_history(store: Path, limit: int = 50) -> list[dict]:
    """Finished tasks from tasks.jsonl, newest first, one entry per task id (for the dashboard)."""
    try:
        lines = store.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: dict[str, dict] = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out.pop(rec["id"], None)
            out[rec["id"]] = rec
    return list(reversed(list(out.values())))[:limit]


# ------------------------------------------------------------------ Core wiring
def make_task_manager(cfg, agent, budget, home: Path, *, on_update=None, on_finished=None,
                      client=None) -> TaskManager | None:
    """Background tasks on the user's chosen connection (None when AI is off, tasks are disabled or no route):
    ai.connection "chatgpt" -> the ChatGPT plan (never the API key); otherwise the API key, like the GPT-Live
    backend."""
    from .config import openai_key_present
    ai = cfg.ai
    if agent is None or ai.provider != "openai" or not ai.tasks_enabled:
        return None
    instructions = RESEARCH_INSTRUCTIONS.format(name=cfg.assistant_name)
    attempt_s = max(5.0, ai.task_timeout_s / 2 - 1)          # two attempts fit inside the task timeout
    if ai.connection == "chatgpt":
        if not hasattr(agent, "lookup"):
            return None
        runner: Any = PlanResearchRunner(agent, instructions, attempt_s=attempt_s)
    elif openai_key_present():
        runner = ResearchRunner(ai.text_model, instructions, budget=budget, client=client, web_search=ai.web_search,
                                reasoning_effort=ai.research_reasoning_effort, reasoning_summary=ai.reasoning_summary,
                                attempt_s=attempt_s, retries=1)
    else:
        return None
    return TaskManager(runner, budget=budget, store=Path(home) / "tasks.jsonl", max_running=ai.task_max_running,
                       timeout_s=ai.task_timeout_s, on_update=on_update, on_finished=on_finished)


def make_retry_lookup(cfg, budget, *, client=None, attempt_s: float = 24.0):
    """The GPT-Live stall retry (voice/live.py retry_lookup): one fast spoken-style lookup on the API key (the
    live backend's own route), no further retry. None without a key."""
    from .config import openai_key_present
    ai = cfg.ai
    if ai.provider != "openai" or not openai_key_present():
        return None
    runner = ResearchRunner(ai.text_model, LOOKUP_INSTRUCTIONS.format(name=cfg.assistant_name), budget=budget,
                            client=client, web_search=ai.web_search, reasoning_effort=ai.voice_reasoning_effort,
                            reasoning_summary=ai.reasoning_summary, service_tier=ai.voice_service_tier,
                            attempt_s=attempt_s, retries=0, area="live", prefix="LIVE_RETRY_BACKEND")

    async def lookup(question: str) -> str:
        return (await runner(question)).text
    return lookup


# ------------------------------------------------------------------ Skill
class StartArgs(BaseModel):
    description: str = Field(..., min_length=3, max_length=600,
                             description="self-contained, in the user's language")


class TaskIdArgs(BaseModel):
    task_id: str = Field("", max_length=40, description='empty = newest')


class TaskSkill(Skill):
    name = "tasks"
    description = "background research tasks"

    def __init__(self, manager: TaskManager) -> None:
        self.manager = manager

    def tools(self) -> list[Tool]:
        return [
            Tool("task_start", "Start a LONG background research task (comparisons, deep checks, >15 s): 'research in depth', 'תחקור לעומק'. Returns at once; the result is announced automatically. Not for quick facts.", self._start, StartArgs, permission="action"),
            Tool("task_status", "Background task status and results: 'what about the task', 'מה עם המשימה'.", self._status,
                 TaskIdArgs, permission="read"),
            Tool("task_cancel", "Cancel a background task (empty id = newest): 'cancel the task', 'תבטל את המשימה'.", self._cancel,
                 TaskIdArgs, permission="action"),
        ]

    async def _start(self, a: StartArgs) -> ToolResult:
        try:
            t = await self.manager.start(a.description)
        except TaskRefused as e:
            return ToolResult(False, e.message, code=e.code)
        n = len(self.manager.active())
        return ToolResult(True, "background task started; tell the user in one short sentence that you are on it "
                                "and will say when it is ready", {"task_id": t.id, "active_tasks": n})

    async def _status(self, a: TaskIdArgs) -> ToolResult:
        tasks = [self.manager.find(a.task_id)] if a.task_id else (self.manager.active()
                                                                  + [t for t in self.manager.tasks.values()
                                                                     if t.status in FINISHED][-3:])
        tasks = [t for t in tasks if t is not None]
        if not tasks:
            return ToolResult(True, "no background tasks", {"tasks": []})
        return ToolResult(True, f"{len(tasks)} task(s)", {"tasks": [
            {"id": t.id, "description": short(t.description, 120), "status": t.status, "step": t.step,
             "elapsed_s": t.elapsed(), "result": short(t.result, 600), "error": t.error} for t in tasks]})

    async def _cancel(self, a: TaskIdArgs) -> ToolResult:
        t = await self.manager.cancel(a.task_id)
        if t is None:
            return ToolResult(False, "no such task", code="TASK_NOT_FOUND")
        if t.status != "cancelled":
            return ToolResult(False, f"task {t.id} is already {t.status}", {"task_id": t.id}, code="TASK_NOT_ACTIVE")
        return ToolResult(True, f"task {t.id} cancelled", {"task_id": t.id})
