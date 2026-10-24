"""Client-mode GPT-Live delegation (SW-V4 package D, issue #14): JARVES itself runs every backend request.

Why: in Responses ("server") delegation OpenAI starts the backend requests itself; the Core can count them but cannot
refuse one before it is billed (#13). In CLIENT delegation (developers.openai.com/api/docs/guides/live-delegation,
re-checked 2026-10-20, research note docs/reviews/SWV4_D_DELEGATION.md) GPT-Live only emits
`session.delegation.created {delegation: {id, target: "client"}}` (metadata, NO task text) and waits; the app builds
the request from the transcript, runs it on any backend and returns the result with `session.commentary.append`
(spoken; GPT-Live paraphrases it) or `session.thinking.append` (silent), both carrying the delegation id unchanged.
There is no client "cancel" event: barge-in leaves backend work running, the app must cancel it itself.

Strict cap. Every backend request is RESERVED in the shared cross-process Budget (budget.py, one SQLite transaction)
BEFORE it is sent: 1 `ai_requests` unit + its worst-case API cost in micro-USD (`delegation_usd_micros`; input bytes
as tokens, max_output_tokens, at most `delegation_max_searches` web searches via `max_tool_calls`, the service-tier
multiplier). A request that does not fit is NEVER sent; JARVES says so honestly. Settlement: the real usage the
response reports (never more than once); a request that provably never left is released; a sent request whose usage
is unknown (cancel, network drop, timeout) is counted at its reserved estimate (conservative). The SDK's own retries
are off (max_retries=0) so no request is ever sent twice behind the reservation's back (no double billing).
Per turn: `delegation_turn_usd` and `delegation_turn_requests` bound all requests of ONE delegated turn.

Routes. `api_key` -> the Responses API on OPENAI_API_KEY (live tools + hosted web search). `chatgpt` / `combined` ->
the official ChatGPT-plan route (chatgpt_brain.ChatGPTBrain._request): plan-billed, $0 API for the reasoning; a plan
limit pauses AI and NEVER moves to the API by itself (owner rule since cc5d317).

Fallback ladder (DelegationState, every step logged and shown on health/status/dashboard/CLI):
client delegation -> (protocol failure, and only with ai.delegation_fallback = "server") server delegation with the
counting cap -> text-only ("I can't look that up now"). Never a silent route or billing change.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import limits, logs
from ..budget import MICRO, USD_KIND, BudgetExceeded

if TYPE_CHECKING:
    from ..budget import Budget, Reservation

STATE_FILE = "delegation_state.json"
STEPS = ("client", "server", "text_only")
NOTE_MAX_CHARS = 1000           # commentary is limited to 500 tokens; Hebrew is ~2-3 characters per token
HISTORY_ITEMS = 16              # transcript items sent with each delegated request
SEARCH_INPUT_TOKENS = 8000      # worst-case search-content tokens one hosted web search adds to the input
TOOL_SCHEMA_SLACK = 1.15        # JSON framing on top of the request body bytes
# A short utterance that only cancels ("עזוב", "never mind") while a lookup runs stops it.
# One cancel phrase (+ optional suffix). The utterance is one or more of them separated by punctuation/space; the loop is
# in `is_cancel` (a regex with the repetition and `\W*` inside it was exponential: CodeQL py/redos).
_CANCEL_ONE = re.compile(r"(?:ו?(?:תעזוב|עזוב|תבטל|בטל|לא\s+משנה|תפסיק|הפסק|תעצור|עצור|די|שכח\s+מזה|תשכח\s+מזה)|"
                         r"never\s*mind|cancel(?:\s+that|\s+it)?|stop(?:\s+that|\s+it)?|forget\s+it)"
                         r"(?:\s+(?:את\s+)?(?:זה|הבדיקה|החיפוש)|\s+please|\s+thanks)?", re.I)
CANCEL_MAX_CHARS = 120          # a spoken cancel is a few words; longer text is never one


def _is_word(c: str) -> bool:
    return c.isalnum() or c == "_"


def is_cancel(text: str) -> bool:
    """True when the whole utterance is only cancel phrases ("עזוב", "never mind", "לא משנה.", "cancel that please").
    Linear: greedy phrase by phrase, bounded input."""
    t = text or ""
    n = len(t)
    if n > CANCEL_MAX_CHARS:
        return False
    pos, found = 0, False
    while True:
        while pos < n and not _is_word(t[pos]):
            pos += 1
        if pos >= n:
            return found
        m = _CANCEL_ONE.match(t, pos)
        if m is None:
            return False
        found, pos = True, m.end()

# ------------------------------------------------------------------------------------------------ spoken notes
CAP_NOTE = {
    "he": ("Tell the user honestly, in one or two short natural Hebrew sentences: you reached today's budget for "
           "lookups, so you cannot check this now; you can still answer from what you already know without "
           "searching, or the owner can raise the limit in the settings. Do not guess the answer. For example: "
           "'הגעתי לתקציב היומי לחיפושים, אז את זה אני לא יכול לבדוק עכשיו. אפשר לשאול אותי משהו שאני יודע בלי "
           "לחפש.'"),
    "en": ("Tell the user honestly, in one or two short English sentences: you reached today's budget for lookups, "
           "so you cannot check this now; you can still answer from what you already know without searching, or the "
           "owner can raise the limit in the settings. Do not guess the answer."),
}
TURN_CAP_NOTE = ("This request needed more lookups than one answer is allowed (per-question cost limit), so it was "
                 "stopped. Tell the user honestly in one short sentence in the user's language that it was too big to "
                 "finish now (name the limit: the per-question limit, ai.delegation_turn_usd) and ask them to ask a narrower "
                 "question. Do not guess.{partial}")
PLAN_LIMIT_NOTE = ("The ChatGPT plan's usage limit is reached, so AI lookups are paused (JARVES never switches to the "
                   "paid API by itself). Tell the user that honestly in one short sentence in the user's language; "
                   "you can still talk and answer from what you already know.")
TIMEOUT_NOTE = ("The lookup took too long and was stopped; nothing more will come for it. Tell the user honestly, in "
                "one short sentence in the user's language, that you could not get it in time (the time limit, "
                "ai.delegation_timeout_s) and ask whether to try "
                "again (Hebrew: 'לא הצלחתי להביא את זה בזמן, לנסות שוב?'). Do not guess.")
ERROR_NOTE = ("The lookup failed ({why}). Tell the user honestly, in one short sentence in the user's language, that "
              "you could not get it right now. Do not guess and do not say it was done.")
CANCEL_NOTE = "Cancelled: the user stopped this request, the backend work was stopped and nothing more was done."
SUPERSEDED_NOTE = "Stopped: the user moved on to a newer request; this earlier lookup was cancelled."
LATER_NOTE = ("[JARVES] The result that follows answers only the request before this one. While it ran the user also "
              "asked: {text}. That part is NOT answered yet: say this result, then delegate that question now.")
UNSAID_INPUT = ("\nAn earlier lookup result was not said to the user yet; start your answer with it, briefly: {results}")
STALE_RESULT = ("[earlier lookup, finished after the user moved on; mention it only if the user asks] {answer}")
TEXT_ONLY_INSTRUCTIONS = ("\n\n# Lookups unavailable\nRight now you cannot look anything up or run any action (the "
                          "backend is unavailable). For anything that needs fresh facts or an action, say honestly in "
                          "one short sentence that you can't do that right now; never pretend you did it. Small talk "
                          "and what you already know still work.")
DELEGATE_INPUT = ("[JARVES Core] The voice assistant (GPT-Live) delegated the user's latest request to you. Handle the "
                  "newest user request in the conversation above (also any earlier one still unanswered), use your "
                  "tools when needed, then answer in one to three short spoken sentences in the language the user "
                  "spoke. Plain text only: no links, no markdown.")


class CapReached(Exception):
    """A request was refused BEFORE it was sent: the daily or per-turn cap would be exceeded."""

    def __init__(self, scope: str, detail: str = "") -> None:
        super().__init__(f"{scope} cap reached")
        self.scope, self.detail = scope, detail


# ------------------------------------------------------------------------------------------------ cost
class Costs:
    """Worst-case estimate (reserved before sending) and actual cost (settled after) in micro-USD."""

    def __init__(self, pricing, model: str, tier: str, max_output_tokens: int, max_searches: int) -> None:
        self.pricing, self.model, self.tier = pricing, model, tier
        self.max_output_tokens, self.max_searches = max_output_tokens, max_searches

    def _price(self, model: str):
        p = self.pricing.models.get(model) or self.pricing.models.get(self.model)
        if p is None:                              # unknown model: the most expensive known price (conservative)
            p = max(self.pricing.models.values(), key=lambda m: m.output)
        return p

    def _mult(self, tier: str) -> float:
        return float(self.pricing.tier_multiplier.get(tier or self.tier, 1.0))

    def estimate(self, body: dict, searches: int | None = None) -> int:
        """Upper bound for one request: every request byte as one input token (a token is at least one byte),
        the full output allowance, every allowed web search (call price + its search-content tokens).
        searches: bound the web searches (a request cancelled after N searches were seen) instead of the maximum."""
        n = self.max_searches if searches is None else min(self.max_searches, max(0, searches))
        raw = json.dumps({k: body.get(k) for k in ("instructions", "input", "tools")}, ensure_ascii=False)
        in_tok = int(len(raw.encode("utf-8")) * TOOL_SCHEMA_SLACK) + n * SEARCH_INPUT_TOKENS
        out_tok = self.max_output_tokens or 8000
        p = self._price(self.model)
        usd = (in_tok * p.input + out_tok * p.output) / 1e6 * self._mult(self.tier)
        usd += n * self.pricing.web_search_per_call
        return int(usd * MICRO) + 1

    def actual(self, usage: dict, searches: int, model: str = "", tier: str = "") -> int:
        p = self._price(model or self.model)
        cached = int(usage.get("cached_tokens") or 0)
        fresh = max(0, int(usage.get("input_tokens") or 0) - cached)
        out = int(usage.get("output_tokens") or 0)
        usd = (fresh * p.input + cached * (p.cached_input or p.input) + out * p.output) / 1e6 * self._mult(tier)
        usd += searches * self.pricing.web_search_per_call
        return int(round(usd * MICRO))


# ------------------------------------------------------------------------------------------------ ladder + status
class DelegationState:
    """The Core's delegation status: the configured mode, the fallback ladder step, the jobs in flight and the
    counters. Persisted (STATE_FILE) so `jarvis budget status` and the dashboard can show it from another process."""

    def __init__(self, home: Path | None, ai, *, health: Callable[[str, str, str], Any] | None = None) -> None:
        self.home, self.ai, self.health = home, ai, health
        self.step = "client"
        self.reason = ""
        self.changed = 0.0
        self.history: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.counters = {"delegations": 0, "completed": 0, "cancelled": 0, "cap_refused": 0, "timeouts": 0,
                         "errors": 0, "requests": 0, "fallbacks": 0, "plan_paused": 0}
        prev = self._read()
        if prev.get("step") and prev["step"] != "client":
            # a new Core process tries client delegation again (logged); the earlier failure stays in the history
            logs.info("delegation", "DELEG_LADDER_RESET", "a new Core start tries client delegation again",
                      previous=prev.get("step"), reason=prev.get("reason", ""))
        self.history = list(prev.get("history") or [])[-10:]
        self.save()

    @property
    def mode(self) -> str:
        return getattr(self.ai, "delegation_mode", "server")

    def session_step(self) -> str:
        """What the NEXT live conversation uses: server (feature off), client, or the ladder's fallback step."""
        if self.mode == "client" and self.step != "client":
            f = self._read()                   # `jarvis budget reset-ladder` from another process
            if f.get("step") == "client" and float(f.get("changed") or 0) > self.changed:
                self.reset(by=str(f.get("reset_by") or "file"))
        return "server" if self.mode != "client" else self.step

    def fail(self, why: str, detail: str = "") -> str:
        """A client-delegation PROTOCOL failure: step down the ladder (logged, shown, never silent)."""
        if self.step != "client":
            return self.step
        nxt = "server" if getattr(self.ai, "delegation_fallback", "local") == "server" else "text_only"
        self.step, self.reason, self.changed = nxt, f"{why}: {detail}"[:200], time.time()
        self.counters["fallbacks"] += 1
        limits.report("DELEGATION_FALLBACK", f"to {nxt}: {why}")
        self.history = (self.history + [{"ts": self.changed, "to": nxt, "reason": self.reason}])[-10:]
        logs.warn("delegation", "DELEG_FALLBACK", "client delegation failed; the next conversation uses the fallback",
                  to=nxt, why=why, detail=detail[:160])
        self._health("degraded", f"client delegation failed ({why}); next conversation: "
                     f"{'server delegation (counting cap)' if nxt == 'server' else 'text only, no lookups'}",
                     "restart JARVES to try client delegation again, or `jarvis budget reset-ladder`")
        self.save()
        return nxt

    def reset(self, by: str = "user") -> None:
        if self.step != "client":
            logs.info("delegation", "DELEG_LADDER_RESET", "client delegation re-enabled", by=by, previous=self.step)
        self.step, self.reason, self.changed = "client", "", time.time()
        self._health("ok", self.describe(), "")
        self.save()

    def describe(self) -> str:
        s = self.session_step()
        if s == "server":
            return ("server delegation: OpenAI starts the backend requests (counted, not strictly capped)"
                    + (f"; fallback after: {self.reason}" if self.mode == "client" else ""))
        if s == "text_only":
            return f"text only (no lookups) after a client-delegation failure: {self.reason}"
        return (f"client delegation: every request reserved before it is sent (strict cap "
                f"${self.ai.delegation_daily_usd:.2f}/day, ${self.ai.delegation_turn_usd:.2f}/question)")

    def _health(self, status: str, detail: str, action: str) -> None:
        if self.health is not None:
            try:
                self.health(status, detail, action)
            except Exception:
                pass

    def count(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def snapshot(self, budget: "Budget | None" = None) -> dict:
        out = {"mode": self.mode, "step": self.session_step(), "ladder": list(STEPS), "reason": self.reason,
               "changed": self.changed, "fallback": getattr(self.ai, "delegation_fallback", "local"),
               "describe": self.describe(), "history": self.history[-5:], "in_flight": list(self.jobs.values()),
               "counters": dict(self.counters),
               "caps": {"daily_usd": self.ai.delegation_daily_usd, "turn_usd": self.ai.delegation_turn_usd,
                        "turn_requests": self.ai.delegation_turn_requests,
                        "max_searches": self.ai.delegation_max_searches, "timeout_s": self.ai.delegation_timeout_s}}
        if budget is not None:
            out["budget"] = usd_status(budget)
        return out

    def _read(self) -> dict:
        if self.home is None:
            return {}
        try:
            return json.loads((self.home / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self) -> None:
        if self.home is None:
            return
        try:
            p = self.home / STATE_FILE
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({"step": self.step, "reason": self.reason, "changed": self.changed,
                                       "mode": self.mode, "history": self.history, "counters": self.counters,
                                       "in_flight": list(self.jobs.values()), "saved": time.time()},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass


def read_state(home: Path) -> dict:
    """The persisted DelegationState of the running (or last) Core, for CLI / dashboard in another process."""
    try:
        return json.loads((home / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def usd_status(budget: "Budget") -> dict:
    """Today's delegated API spend: used, held by live reservations (all processes), limit, remaining (USD)."""
    used, held, limit = budget.used(USD_KIND), budget.held(USD_KIND), budget.limit(USD_KIND)
    return {"used_usd": round(used / MICRO, 6), "held_usd": round(held / MICRO, 6), "limit_usd": round(limit / MICRO, 6),
            "remaining_usd": round(max(0, limit - used - held) / MICRO, 6),
            "requests_today": budget.used("ai_requests"), "requests_limit": budget.limit("ai_requests"),
            "reservations": budget.reservations()}


# ------------------------------------------------------------------------------------------------ one delegated turn
@dataclass
class Job:
    did: str
    turn: int
    lang: str
    history: list[dict]
    t0: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None
    reserved: int = 0               # micro-USD reserved by this turn so far (per-turn cap)
    requests: int = 0
    spent: int = 0                  # micro-USD settled
    stage: str = "starting"
    tool_running: int = 0
    tools_done: list[str] = field(default_factory=list)
    cancel_why: str = ""
    sent_any: bool = False
    own: bool = False               # started by the Core itself (CV-15 hand-over): appends carry delegation_id null
    convo_n: int = 0                # finished utterances when the job started (later questions are not in its input)
    pending: str = ""               # the words still being transcribed when it started (part of its input)
    unsaid: list[str] = field(default_factory=list)   # earlier results GPT-Live has not said yet (carried over)


class _Held:
    """The reservations of ONE backend request (ai_requests unit + micro-USD), settled exactly once."""

    def __init__(self, res: "list[Reservation]", est: int) -> None:
        self.res, self.est, self.sent, self.done = res, est, False, False

    def settle(self, usd_micros: int | None) -> int:
        """usd_micros None = outcome unknown (counted at the estimate). Unsent = released."""
        if self.done:
            return 0
        self.done = True
        if not self.sent:
            for r in self.res:
                r.release()
            return 0
        n = self.est if usd_micros is None else int(usd_micros)
        for r in self.res:
            if r.kind == USD_KIND:
                if n > r.n:
                    logs.warn("delegation", "DELEG_ESTIMATE_LOW", "a request cost more than its reserved estimate",
                              reserved_usd=r.n / MICRO, actual_usd=n / MICRO)
                r.commit(n)
            else:
                r.commit()
        return n


class ClientDelegation:
    """Runs GPT-Live's client delegations for one LiveVoice (attach()). Reused across sessions of that LiveVoice."""

    def __init__(self, ai, pricing, budget: "Budget | None", state: DelegationState, *, route: str = "api_key",
                 plan_brain=None, client=None, max_output_tokens: int = 800, reasoning_effort: str = "",
                 service_tier: str = "", model: str = "") -> None:
        self.ai, self.pricing, self.budget, self.state = ai, pricing, budget, state
        self.route = route                       # "api_key" | "plan"
        self.plan_brain, self._client = plan_brain, client
        self.reasoning_effort, self.service_tier = reasoning_effort, service_tier
        self.model = model or ai.text_model
        self.max_output_tokens = max_output_tokens
        self.costs = Costs(pricing, self.model, service_tier, max_output_tokens, ai.delegation_max_searches)
        self.live = None
        self.jobs: dict[str, Job] = {}
        self._eids = 0
        self._sent_notes: dict[str, tuple[str, str]] = {}   # event id -> (delegation id, content) for a resend
        self.latencies: list[float] = []                    # delegation.created -> result handed to GPT-Live (s)
        self._results: list[tuple[str, int, float]] = []    # (answer, said_all offset, monotonic) handed to GPT-Live

    # ---------------------------------------------------------------- wiring
    def attach(self, live) -> "ClientDelegation":
        self.live = live
        live.delegation = self
        return self

    def billed_route(self) -> str:
        return "chatgpt_plan" if self.route == "plan" else "api_key"

    def _api(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI()
        # no hidden SDK retries: each attempt would be a second, unreserved (double-billed) request
        try:
            return self._client.with_options(max_retries=0, timeout=self.ai.delegation_timeout_s + 5)
        except AttributeError:                    # test doubles
            return self._client

    # ---------------------------------------------------------------- events from LiveVoice
    async def on_created(self, did: str, turn: int, history: list[dict], lang: str, *, own: bool = False) -> None:
        """session.delegation.created (target client): newer turn supersedes older ones; start the job.
        own=True: the Core hands a turn GPT-Live acknowledged but never delegated (CV-15) to the backend itself."""
        self.state.count("delegations")
        for old in list(self.jobs.values()):
            if old.turn < turn and old.task is not None and not old.task.done():
                await self.cancel(old.did, "superseded", wait=False)   # never block the event loop of the session
        job = Job(did, turn, lang or "he", history, own=own, convo_n=len(getattr(self.live, "_convo", [])),
                  pending=(getattr(self.live, "_in_text", "") or "").strip(), unsaid=self._take_unsaid())
        self.jobs[did] = job
        self.state.jobs[did] = {"id": did[-8:], "turn": turn, "started": time.time(), "stage": "starting",
                                "reserved_usd": 0.0, "route": self.billed_route()}
        logs.info("delegation", "DELEG_START", "client delegation started", delegation=did, turn=turn,
                  route=self.billed_route(), history_items=len(history))
        job.task = self.live._spawn(self._run(job), f"delegation-{did[-6:]}")

    def on_user_text(self, text: str) -> None:
        """A finished user utterance: a bare "never mind" / "עזוב" while lookups run cancels them."""
        if not self.jobs or not is_cancel((text or "").strip()) or len((text or "").split()) > 4:
            return
        for did, job in list(self.jobs.items()):
            if job.task is not None and not job.task.done():
                self._user_cancel_t = time.monotonic()
                asyncio.get_running_loop().create_task(self.cancel(did, "user_cancel"))

    def may_hand_over(self, text: str) -> bool:
        """CV-15 hand-over in client mode: never for a bare cancel phrase, nor right after the user cancelled a lookup
        (live 20 Oct: the "רגע, אני בודק" before "עזוב" was judged undelegated and the cancelled lookup ran again)."""
        if is_cancel((text or "").strip()):
            return False
        return time.monotonic() - getattr(self, "_user_cancel_t", -1e9) > 10.0

    async def cancel(self, did: str, why: str, *, wait: bool = True) -> bool:
        """Stop a running delegated turn (the request in flight is cancelled; unsent work released; sent work counted
        at its reservation) and confirm it to GPT-Live silently."""
        job = self.jobs.get(did)
        if job is None or job.task is None or job.task.done():
            return False
        job.cancel_why = why
        job.task.cancel()
        if not wait:
            return True
        try:
            await asyncio.wait({job.task}, timeout=5)
        except Exception:
            pass
        return True

    async def on_rejected(self, eid: str, code: str, msg: str) -> None:
        """The server rejected one of our delegation appends: a PROTOCOL failure (unknown delegation id, schema).
        This conversation still gets the content (general commentary, delegation_id null); the ladder steps down."""
        did, content = self._sent_notes.pop(eid, ("", ""))
        self.state.fail("append_rejected", f"{code}: {msg}")
        if content and self.live is not None and self.live.running:
            try:
                await self.live.conn.session.commentary.append(content=content, delegation_id=None,
                                                               event_id=f"jarves_task_deleg_resend_{self._eids}")
                logs.info("delegation", "DELEG_RESENT", "result re-sent as general commentary", delegation=did)
            except Exception as e:
                logs.warn("delegation", "DELEG_RESEND_FAILED", "result could not be re-sent", error_type=type(e).__name__)

    async def close(self) -> None:
        """Session over: cancel every running delegated turn (each settles its reservations)."""
        for did in list(self.jobs):
            await self.cancel(did, "session_end")
        self.jobs.clear()
        self.state.jobs.clear()
        self.state.save()

    def stats(self) -> dict:
        lat = sorted(self.latencies)
        out = {"client_delegations": len(lat)}
        if lat:
            out["deleg_p50_s"] = round(lat[len(lat) // 2], 2)
            out["deleg_p95_s"] = round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2)
        return out

    # ---------------------------------------------------------------- the job
    async def _run(self, job: Job) -> None:
        lv = self.live
        self._busy(job, True)
        await lv._state("thinking")
        await lv._show("thinking", "חושב…")
        if getattr(lv, "progress_after_s", 0) > 0:
            lv._progress_for = job.did
            lv._spawn(lv._progress_later(job.did), "progress")
        answer, note, outcome = "", "", "completed"
        try:
            answer = await asyncio.wait_for(self._turn(job), self.ai.delegation_timeout_s)
            if not answer:
                outcome, note = "error", ERROR_NOTE.format(why="empty answer")
        except CapReached as e:
            outcome = "cap"
            self.state.count("cap_refused")
            logs.warn("delegation", "DELEG_CAP", "a delegated request was refused before sending (cap)",
                      delegation=job.did, scope=e.scope, detail=e.detail)
            if e.scope == "turn":
                if "requests per question" in e.detail:
                    limits.report("DELEGATION_TURN_REQUESTS", f"{job.requests} requests, cap {self.ai.delegation_turn_requests}",
                                  self.ai.delegation_turn_requests)
                else:
                    limits.report("DELEGATION_TURN_USD", f"{e.detail}", self.ai.delegation_turn_usd)
                partial = f"\nWhat was found so far: {job.tools_done[-1][:300]}" if job.tools_done else ""
                note = TURN_CAP_NOTE.format(partial=partial)
            else:
                note = CAP_NOTE.get(job.lang, CAP_NOTE["he"])
                self.state._health("degraded", f"delegation budget reached ({e.detail})",
                                   "raise ai.delegation_daily_usd or ai.daily_request_limit, or wait until tomorrow")
        except asyncio.TimeoutError:
            outcome, note = "timeout", TIMEOUT_NOTE
            self.state.count("timeouts")
            logs.warn("delegation", "DELEG_TIMEOUT", "a delegated turn hit its time limit and was cancelled",
                      delegation=job.did, timeout_s=self.ai.delegation_timeout_s, stage=job.stage)
            limits.report("DELEGATION_TIMEOUT", f"{self.ai.delegation_timeout_s:g} s", self.ai.delegation_timeout_s)
        except asyncio.CancelledError:
            outcome = "cancelled"
            self.state.count("cancelled")
            logs.info("delegation", "DELEG_CANCELLED", "a delegated turn was cancelled", delegation=job.did,
                      why=job.cancel_why or "session_end", requests=job.requests, spent_usd=job.spent / MICRO)
            self._finish(job, outcome)
            if job.cancel_why in ("user_cancel", "superseded") and lv.running:
                await self._append("thinking", job.did, CANCEL_NOTE if job.cancel_why == "user_cancel"
                                   else SUPERSEDED_NOTE)
            raise
        except Exception as e:
            outcome = "error"
            code = getattr(e, "code", "") or type(e).__name__
            if code == "CHATGPT_LIMIT":
                outcome, note = "plan_limit", PLAN_LIMIT_NOTE
                self.state.count("plan_paused")
                self.state._health("degraded", "ChatGPT plan limit reached: AI lookups paused (never moved to the "
                                   "API)", "see ChatGPT Settings > Usage; switch to the API key only if you choose to")
            else:
                note = ERROR_NOTE.format(why=str(code)[:60])
                self.state.count("errors")
            logs.warn("delegation", "DELEG_FAILED", "a delegated turn failed", delegation=job.did, err_code=str(code),
                      **logs.error_fields(e))
        self._finish(job, outcome)
        if not lv.running:
            return
        if answer:
            lat = time.monotonic() - job.t0
            self.latencies.append(lat)
            self.state.count("completed")
            if lv._superseded(job.did):
                await self._append("thinking", job.did, STALE_RESULT.format(answer=answer[:600]))
            else:
                later = self._asked_later(job)
                if later:                    # live 20 Oct: a second question asked while this lookup ran was dropped
                    await self._append("thinking", job.did, LATER_NOTE.format(text=" | ".join(later)[:400]))
                await self._append("commentary", job.did, answer)
                self._results = (self._results + [(answer, len(lv._said_all), time.monotonic())])[-4:]
                lv._answers += 1
                if answer.strip(" .!?,").lower() not in ("כן", "לא", "ok", "yes", "no"):
                    lv._spawn(lv._watch_delivery(answer, (len(lv._said_all), lv._answers, lv._user_turn_n)),
                              "delivery-watch")
            logs.info("delegation", "DELEG_DONE", "client delegation answered", delegation=job.did,
                      deleg_s=round(lat, 2), requests=job.requests, spent_usd=job.spent / MICRO,
                      route=self.billed_route())
        elif note:
            await self._append("commentary", job.did, note)

    def _take_unsaid(self) -> list[str]:
        """Results handed to GPT-Live in the last minute that it has not said (live 20 Oct, scenario 53: the user's
        next question arrived while the first result was waiting and GPT-Live dropped it). The next request carries
        them, so its answer can include them; each is carried over once."""
        from .live import answer_said
        lv, now, out = self.live, time.monotonic(), []
        for ans, start, t in self._results:
            if now - t < 60 and not answer_said(ans, getattr(lv, "_said_all", "")[start:]):
                out.append(ans)
        self._results = []
        return out

    def _asked_later(self, job: Job) -> list[str]:
        """User questions finished AFTER this job took its input and not covered by a newer delegation."""
        lv = self.live
        if any(j.turn > job.turn or (j.t0 > job.t0 and j is not job) for j in self.jobs.values()):
            return []                               # a newer delegation already handles them
        out = [h["text"] for h in getattr(lv, "_convo", [])[job.convo_n:] if h.get("role") == "user"
               and h.get("text") and h["text"] != job.pending and not job.pending.startswith(h["text"])]
        cur = (getattr(lv, "_in_text", "") or "").strip()
        if cur and cur != job.pending:
            out.append(cur)
        return [t for t in out if len(t.split()) >= 2]

    def _busy(self, job: Job, on: bool) -> None:
        lv = self.live
        running = [j for j in self.jobs.values() if j.task is not None and not j.task.done() and j is not job]
        if on:
            lv._backend_t0 = lv._busy_t0 = time.monotonic()
            lv._busy_delegation = job.did
        lv._backend_busy = on or bool(running)
        lv.note_activity()

    def _finish(self, job: Job, outcome: str) -> None:
        self.jobs.pop(job.did, None)
        self.state.jobs.pop(job.did, None)
        self.state.save()
        self._busy(job, False)
        lv = self.live
        if lv is not None and lv.running and not self.jobs:
            lv._spawn(lv._clear_activity_later(), "activity-clear")
        logs.debug("delegation", "DELEG_SETTLED", "delegated turn settled", delegation=job.did, outcome=outcome,
                   reserved_usd=job.reserved / MICRO, spent_usd=job.spent / MICRO, requests=job.requests)

    async def _append(self, kind: str, did: str, content: str) -> None:
        self._eids += 1
        eid = f"jarves_deleg_{kind[0]}_{self._eids}"
        content = content[:NOTE_MAX_CHARS]
        if kind == "commentary":
            self._sent_notes[eid] = (did, content)
        try:
            api = self.live.conn.session.commentary if kind == "commentary" else self.live.conn.session.thinking
            job = self.jobs.get(did)
            own = did.startswith("jarves_own") or (job is not None and job.own)
            await api.append(content=content, delegation_id=None if own else did, event_id=eid)
            self.live.note_activity()
        except Exception as e:
            logs.warn("delegation", "DELEG_APPEND_FAILED", "result not handed to GPT-Live", kind=kind,
                      error_type=type(e).__name__)

    # ---------------------------------------------------------------- reservations
    def _reserve(self, job: Job, est: int) -> _Held:
        """Before EVERY request: the per-turn caps, then the daily caps (atomic, cross-process). Never sends on a miss."""
        ai = self.ai
        if job.requests >= ai.delegation_turn_requests:
            raise CapReached("turn", f"{job.requests} requests per question")
        # the turn's SETTLED spend so far + this request's worst case (earlier requests are settled before the next)
        if job.spent + est > int(round(ai.delegation_turn_usd * MICRO)) and self.route != "plan":
            raise CapReached("turn", f"${(job.spent + est) / MICRO:.4f} > ${ai.delegation_turn_usd:.2f} per question")
        items: list[tuple[str, int]] = []
        if self.route != "plan":            # the plan route reserves its ai_requests unit inside ChatGPTBrain
            items = [("ai_requests", 1), (USD_KIND, est)]
        res = []
        if items and self.budget is not None:
            try:
                res = self.budget.reserve_many(items)
            except BudgetExceeded as e:
                raise CapReached("daily", f"{e.kind}: {e}") from e
        job.reserved += est if self.route != "plan" else 0
        job.requests += 1
        self.state.count("requests")
        info = self.state.jobs.get(job.did)
        if info is not None:
            info["reserved_usd"] = round(job.reserved / MICRO, 6)
            self.state.save()
        return _Held(res, est if self.route != "plan" else 0)

    # ---------------------------------------------------------------- the backend turn (tool loop)
    def _input(self, job: Job) -> list[dict]:
        items = [{"role": h["role"], "content": h["text"]} for h in job.history[-HISTORY_ITEMS:] if h.get("text")]
        while items and items[0]["role"] != "user":
            items.pop(0)
        note = DELEGATE_INPUT
        if job.unsaid:
            note += UNSAID_INPUT.format(results=" | ".join(job.unsaid)[:800])
        return items + [{"role": "developer" if self.route != "plan" else "user", "content": note}]

    async def _turn(self, job: Job) -> str:
        if self.route == "plan":
            return await self._plan_turn(job)
        return await self._api_turn(job)

    def _api_body(self, job: Job, inp: list) -> dict:
        from .live import live_tools
        tools: list[dict] = live_tools(self.live.registry)
        if self.live.web_search and self.ai.delegation_max_searches > 0:
            tools.append({"type": "web_search"})
        body: dict[str, Any] = {"model": self.model, "instructions": self.live.backend_instructions(), "input": inp,
                                "tools": tools, "tool_choice": "auto", "parallel_tool_calls": False,
                                "text": {"verbosity": "low"}, "store": False,
                                "max_tool_calls": max(1, self.ai.delegation_max_searches)}
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
            if self.reasoning_effort != "none":
                body["include"] = ["reasoning.encrypted_content"]
        if self.service_tier:
            body["service_tier"] = self.service_tier
        if self.max_output_tokens:
            body["max_output_tokens"] = max(16, self.max_output_tokens)
        return body

    async def _api_turn(self, job: Job) -> str:
        from ..brain import BackendTrace, speakable
        inp = self._input(job)
        while True:
            body = self._api_body(job, inp)
            held = self._reserve(job, self.costs.estimate(body))
            trace = BackendTrace("live", "LIVE_BACKEND", delegation=job.did)
            out_items: list[dict] = []
            final: dict = {}
            try:
                api = self._api()
                held.sent = True           # from here on the request may have reached OpenAI
                job.sent_any = True
                stream = await api.responses.create(stream=True, **body)
                async for ev in stream:
                    d = _dump(ev)
                    shown = trace.feed(d)
                    if shown is not None:
                        job.stage = trace.stage
                        await self.live._show(*shown)
                    t = d.get("type", "")
                    if t == "response.output_item.done" and isinstance(d.get("item"), dict):
                        out_items.append(d["item"])
                    elif t in ("response.completed", "response.incomplete", "response.failed"):
                        final = _dump(d.get("response"))
            except BaseException:
                trace.finish("cancelled" if not final else "failed")
                # sent, outcome unknown: counted conservatively - full input + output allowance, every search seen
                # plus one that may have started unseen (none once the answer text was being written)
                job.spent += held.settle(self._unknown_cost(trace, body, held))
                raise
            status = final.get("status") or ("completed" if final else "failed")
            searches = trace.search_calls or sum(1 for i in out_items if i.get("type") == "web_search_call")
            if self.ai.delegation_max_searches > 0 and searches >= self.ai.delegation_max_searches:
                limits.report("DELEGATION_SEARCHES", f"{searches} of {self.ai.delegation_max_searches} searches",
                              self.ai.delegation_max_searches)       # no silent limit: the answer may be partial
            u = trace.usage or {}
            job.spent += held.settle(self.costs.actual(u, searches, final.get("model", ""), final.get("service_tier", ""))
                                     if u else self._unknown_cost(trace, body, held))
            if status == "failed" or not final:
                err = _dump(final.get("error"))
                raise DelegationError(str(err.get("code") or "response_failed"))
            calls = [i for i in out_items if i.get("type") == "function_call"]
            if not calls:
                text = "".join(c.get("text", "") for i in out_items if i.get("type") == "message"
                               for c in (i.get("content") or []) if c.get("type") == "output_text").strip()
                return speakable(text or trace.text)
            inp = inp + [_clean_item(i) for i in out_items if i.get("type") in ("function_call", "reasoning")]
            for fc in calls:
                res = await self._tool(job, fc.get("name", ""), fc.get("arguments") or "{}")
                inp.append({"type": "function_call_output", "call_id": fc.get("call_id", ""), "output": res})

    def _unknown_cost(self, trace, body: dict, held: _Held) -> int:
        """A sent request without a usage report (cancel, drop, timeout, broken stream): its full input + output
        allowance, every web search seen plus one that may have started unseen (none once the answer was being
        written); never more than its reservation."""
        if trace.usage:
            return min(held.est, self.costs.actual(trace.usage, trace.search_calls))
        seen = max(trace.search_calls, len(trace.queries)) + (0 if trace._wrote else 1)
        return min(held.est, self.costs.estimate(body, searches=seen))

    async def _plan_turn(self, job: Job) -> str:
        from ..brain import BackendTrace, speakable
        from ..chatgpt_brain import plain_tool_name
        brain = self.plan_brain
        ok, code, action = brain.available()
        if not ok:
            if code == "CHATGPT_LIMIT":
                raise DelegationError("CHATGPT_LIMIT")
            if code == "AI_BUDGET":
                raise CapReached("daily", "ai_requests")
            raise DelegationError(code)
        model = await brain._pick_model()
        inp = self._input(job)
        loop = asyncio.get_running_loop()
        while True:
            body = {"model": model, "instructions": self.live.backend_instructions(), "input": inp,
                    "tools": brain._tools(), "store": False, "stream": True}
            self._reserve(job, 0)
            trace = BackendTrace("live", "PLAN", quiet=True)

            def on_event(ev, _t=trace):
                shown = _t.feed(ev)
                if shown is not None:
                    job.stage = _t.stage
                    loop.create_task(self.live._show(*shown))
            job.sent_any = True
            try:
                resp = await brain._request(body, on_event)    # reserved + counted inside (ai_requests)
            except BudgetExceeded as e:
                raise CapReached("daily", f"{e.kind}: {e}") from e
            outputs = [o for o in resp.get("output", []) if o.get("type") in ("message", "function_call")]
            calls = [o for o in outputs if o["type"] == "function_call"]
            if not calls:
                return speakable(brain._text(outputs))
            inp = inp + [brain._clean(o) for o in outputs]
            for fc in calls:
                res = await self._tool(job, plain_tool_name(fc), fc.get("arguments") or "{}")
                inp.append({"type": "function_call_output", "call_id": fc["call_id"], "output": res})

    async def _tool(self, job: Job, name: str, arguments: str) -> str:
        """One Skill call inside a delegated turn (same registry, approvals and screen activity as server mode).
        A side effect is never cut in half: a cancel during the tool waits for it, then stops the turn."""
        from ..skills import ROUTE
        from .live import VOICE_ROUTE
        lv = self.live
        ROUTE.set(VOICE_ROUTE)
        logs.info("live", "LIVE_TOOL_CALL", "backend called a tool", tool=name, delegation=job.did)
        job.stage = "tool"
        lv._tools_running += 1
        try:
            await lv._tool_activity(name, None)
            res = await asyncio.shield(lv.registry.call(name, arguments))
            if res.ok and getattr(lv.registry.get(name), "permission", "read") in ("action", "destructive"):
                lv._action_times.append(time.monotonic())
            await lv._tool_activity(name, res)
        finally:
            lv._tools_running = max(0, lv._tools_running - 1)
            lv.note_activity()
        out = res.to_model()
        job.tools_done.append(f"{name}: {out[:200]}")
        return out


class DelegationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _dump(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else {}


def _clean_item(item: dict) -> dict:
    """An output item echoed back as input of the next (stateless, store=false) request."""
    if item.get("type") == "function_call":
        return {k: item[k] for k in ("type", "call_id", "name", "arguments") if k in item}
    return {k: v for k, v in item.items() if k not in ("status",)}


# ------------------------------------------------------------------------------------------------ Core factory
def for_core(core, live_kwargs: dict) -> dict:
    """LiveVoice kwargs for the Core's current delegation step (app.py calls this; one place for the wiring).
    Returns {} for server delegation (the existing Responses path, counting cap)."""
    state: DelegationState | None = getattr(core, "delegation_state", None)
    if state is None:
        return {}
    step = state.session_step()
    if step == "server":
        return {}
    if step == "text_only":
        logs.info("delegation", "DELEG_TEXT_ONLY", "this conversation has no lookups (client delegation fallback)",
                  reason=state.reason)
        return {"delegation_mode": "text_only"}
    ai = core.cfg.ai
    route = "plan" if ai.connection in ("chatgpt", "combined") else "api_key"
    plan_brain = core.agent if route == "plan" else None
    engine = ClientDelegation(ai, core.cfg.pricing, core.budget, state, route=route, plan_brain=plan_brain,
                              client=getattr(core, "_delegation_client", None),      # tests: a local fake server
                              max_output_tokens=ai.voice_max_output_tokens, reasoning_effort=ai.voice_reasoning_effort,
                              service_tier=ai.voice_service_tier, model=ai.text_model)
    logs.info("delegation", "DELEG_MODE", "this conversation uses client delegation", route=engine.billed_route(),
              daily_usd=ai.delegation_daily_usd, turn_usd=ai.delegation_turn_usd)
    return {"delegation_mode": "client", "delegation": engine}
