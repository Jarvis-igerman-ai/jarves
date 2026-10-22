"""Minimal Skills interface: a Skill is a named group of tools with validated arguments, an enforced permission
policy and optional user confirmation. The same tools are exposed to the OpenAI agent, the realtime voice agent
and the offline intent router, so adding a Skill never requires Core changes.

Permission policy (Issue #9 CR-03), enforced centrally in `SkillRegistry.call`, never by prompt text:

- ``read``        - no side effects; always allowed.
- ``action``      - changes something outside the conversation (player, a new memory). Allowed only when the tool
                    is pre-authorized by the registry policy (`allowed_actions`); otherwise denied (TOOL_FORBIDDEN).
                    A future connector (e-mail, calendar...) is therefore denied until it is explicitly allowed.
- ``destructive`` - deletes/irreversibly changes user data. From the model it ALWAYS needs `registry.confirm`
                    approval. From the user (origin="user": the deterministic command parser matched the user's own
                    words) it needs approval when it affects more than one record or everything.
- missing/unknown permission - denied by default.

A tool's `confirm` prompt, when set, is always asked (any origin).
Generated Skills (skills/generated.py, owner-approved SKILL.md + optional sandboxed Python) register here like any
other Skill; `disabled` holds the ones the owner has not switched on, so no route can run them.
Untrusted content (WP-G): after a tool with `untrusted_output` (mail, calendar text written by others) returned in
the current user turn, the registry refuses model-origin action/destructive tools (TOOL_UNTRUSTED_TURN) until the
user speaks again (`new_turn`), so text inside a mail can never trigger a side effect by itself.
Action/destructive handlers run shielded from cancellation of the calling turn (the timeout still applies), so a
pre-empted turn or a shutdown never aborts a half-done side effect; read tools stay normally cancellable.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ValidationError

from .. import logs

Permission = Literal["read", "action", "destructive"]
PERMISSIONS = ("read", "action", "destructive")
Origin = Literal["model", "user"]

# Low-risk actions pre-authorized by policy (reversible, user-visible, no data loss).
DEFAULT_ALLOWED_ACTIONS = frozenset({"remember", "music_play", "music_pause", "music_resume", "music_next",
                                     "music_stop", "music_volume", "music_mute", "music_unmute",
                                     # settings Skill: one appended line (backed up, undoable) / allowlisted keys
                                     "workspace_note", "set_voice", "set_setting"})
UNTRUSTED_GRACE_S = 3.0    # see SkillRegistry.new_turn
REPEAT_REPLAY_S = 30.0     # V4-VC-3: an identical successful action repeated in a tainted turn gets its result back
# Which conversation a tool call belongs to ("voice", "text", default "default": scheduler / other callers). The
# untrusted-content taint is kept per route. A contextvar: tasks spawned by a route inherit it.
ROUTE: contextvars.ContextVar[str] = contextvars.ContextVar("jarves_skill_route", default="default")
# The tool whose handler is running ("" = none). approvals.answer refuses inside a tool: no Skill (or text a tool
# returned) can ever approve a request; only the owner's own channels can (approvals.py).
IN_TOOL: contextvars.ContextVar[str] = contextvars.ContextVar("jarves_in_tool", default="")
# Terminal text chat (chat_service.py): a callable (name, ok, code) told about every tool call of THIS turn, so the
# terminal shows one short line per call. It never receives arguments or results (nothing to redact or leak).
TOOL_SINK: contextvars.ContextVar = contextvars.ContextVar("jarves_tool_sink", default=None)


def _tool_seen(name: str, ok: bool, code: str, message: str = "") -> None:
    sink = TOOL_SINK.get()
    if sink is not None:
        try:
            if getattr(sink, "wants_detail", False):   # the web chat shows an expandable (redacted) result line
                sink(name, ok, code, message)
            else:
                sink(name, ok, code)
        except Exception:
            pass                                       # a display problem never changes a tool result


async def bounded_ai(aw, timeout_s: float, registry: "SkillRegistry | None"):
    """asyncio.wait_for for an AI turn whose deadline excludes the time a human spent on registry.confirm
    (real typed session 19 Oct: an approval answered after minutes became "the AI did not answer in time")."""
    task = asyncio.ensure_future(aw)
    loop = asyncio.get_running_loop()
    base = registry.confirm_elapsed() if registry is not None else 0.0
    deadline = loop.time() + timeout_s
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=max(0.0, deadline - loop.time()))
            if task in done:
                return task.result()
            human = registry.confirm_elapsed() - base if registry is not None else 0.0
            if registry is not None and (human > 0 or registry._confirming_since is not None):
                deadline += human
                base += human
                if deadline - loop.time() <= 0:     # still waiting on the person: keep waiting
                    deadline = loop.time() + 0.5
                continue
            raise asyncio.TimeoutError
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@dataclass
class ToolResult:
    ok: bool
    message: str                       # short, user-presentable (may be spoken)
    data: dict = field(default_factory=dict)
    code: str = "ok"                   # stable error code for logs/diagnostics

    def to_model(self) -> str:
        return json.dumps({"ok": self.ok, "message": self.message, "code": self.code, **self.data}, ensure_ascii=False)


class NoArgs(BaseModel):
    pass


# (validated args) -> (records the call would affect, or None for "everything/unknown"; confirmation prompt)
ImpactFn = Callable[[Any], Awaitable[tuple[int | None, str]]]


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[[Any], Awaitable[ToolResult]]
    args: type[BaseModel] = NoArgs
    permission: str | None = None      # "read" | "action" | "destructive"; missing/anything else is denied
    confirm: str | None = None         # if set, the user must approve this prompt first (any origin)
    timeout_s: float = 10.0
    impact: ImpactFn | None = None     # destructive tools: how many records this call would affect
    untrusted_output: bool = False     # returns third-party text (mail, calendar): see SkillRegistry.new_turn
    recipient: Callable[[Any], str | None] | None = None   # send tools: who it goes to (first name is spoken aloud)
    # extra approval metadata (SW-V4 F): (validated args) -> {"details": {...}, "typed": bool, "voice": bool} merged
    # into the approvals request (a WhatsApp send shows name + number + full text; typed at night)
    approval_meta: Callable[[Any], dict] | None = None
    # (validated args) -> a refusal, or None: runs BEFORE the owner is asked, so an impossible call is never put to
    # the owner as a question (V4-WA-2: unknown/used WhatsApp draft id)
    precheck: Callable[[Any], "ToolResult | None"] | None = None

    @property
    def side_effect(self) -> bool:     # read-only view of the old flag
        return self.permission in ("action", "destructive")


class Skill:
    name: str = "skill"
    description: str = ""

    def tools(self) -> list[Tool]:
        raise NotImplementedError

    async def health(self) -> tuple[str, str]:
        """(status, detail) for diagnostics."""
        return "ok", ""


ConfirmFn = Callable[[str], Awaitable[bool]]


_UNTRUSTED_KEYS = ("output_tail",)


def _carries_untrusted(data: dict | None) -> bool:
    """Does a result carry text written by someone else (untrusted_* keys, a program's output)?"""
    return any(str(k).startswith("untrusted") or k in _UNTRUSTED_KEYS for k, v in (data or {}).items() if v)


async def _deny(_: str) -> bool:
    return False



_DROP_KEYS = ("title", "minLength", "maxLength", "default")


def slim_schema(node: Any, _in_props: bool = False) -> Any:
    """SW-V4 P: a tool's JSON schema as the model needs it. Pydantic adds a `title` to every property and the model
    class (about 20% of the schema tokens), length bounds and defaults the Core validates/fills itself on every call
    anyway, and `anyOf [X, null]` for an optional field (the model just leaves it out); none of it helps the model
    choose or fill a tool. Property NAMES (even one called "title" or "default") are kept."""
    if isinstance(node, dict):
        if not _in_props and isinstance(node.get("anyOf"), list):
            rest = [v for v in node["anyOf"] if v != {"type": "null"}]
            if len(rest) == 1 and len(rest) < len(node["anyOf"]) and isinstance(rest[0], dict):
                node = {**{k: v for k, v in node.items() if k != "anyOf"}, **rest[0]}
        return {k: slim_schema(v, k == "properties") for k, v in node.items()
                if _in_props or k not in _DROP_KEYS}
    if isinstance(node, list):
        return [slim_schema(v) for v in node]
    return node


def tool_schema(tool: "Tool") -> dict:
    return slim_schema(tool.args.model_json_schema())


class SkillRegistry:
    def __init__(self, disabled: set[str] | None = None,
                 allowed_actions: set[str] | frozenset[str] | None = None) -> None:
        self.skills: dict[str, Skill] = {}
        self._tools: dict[str, tuple[Skill, Tool]] = {}
        self.disabled = disabled or set()
        self.allowed_actions = set(DEFAULT_ALLOWED_ACTIONS if allowed_actions is None else allowed_actions)
        self.confirm: ConfirmFn = _deny       # replaced by the Core (asks the user via Godot/voice)
        # feature switches (features.py): (skill, tool) -> None to offer/run it, or why the feature is not active
        self.gate: Callable[[str, str], str | None] | None = None
        # WP-L guides: (skill, tool, failed result) -> machine-readable setup hint added as data["setup_hint"]
        self.setup_hint: Callable[[str, str, "ToolResult"], dict | None] | None = None
        self._inflight: set[asyncio.Future] = set()
        # monotonic time untrusted text entered the current turn, PER ROUTE (SW-V3 P0.1: one shared taint let a
        # mail read in the voice conversation refuse a scheduled job's action, and vice versa)
        self._untrusted: dict[str, float] = {}
        self.clock = time.monotonic
        # human approval time (registry.confirm) is not AI time: Brain timeouts add it back (bounded_ai)
        self.confirm_wait_s = 0.0
        self._confirming_since: float | None = None
        self.asks = 0                              # approvals asked through this registry (V4-VC-3)
        self._recent_ok: dict[tuple, tuple] = {}   # V4-VC-3: (route, tool, args) -> (t, result, owner approved it)

    @property
    def _untrusted_at(self) -> float | None:
        return self._untrusted.get(ROUTE.get())

    @_untrusted_at.setter
    def _untrusted_at(self, value: float | None) -> None:
        if value is None:
            self._untrusted.pop(ROUTE.get(), None)
        else:
            self._untrusted[ROUTE.get()] = value

    def new_turn(self, route: str | None = None) -> None:
        """The user said something new on `route` (default: the caller's ROUTE): side effects are allowed again
        there. A taint younger than UNTRUSTED_GRACE_S is kept: a voice transcript can complete just after the tool
        of that same turn returned."""
        key = route or ROUTE.get()
        at = self._untrusted.get(key)
        if at is not None and self.clock() - at >= UNTRUSTED_GRACE_S:
            self._untrusted.pop(key, None)
        # V4-VC-3: only a repeat inside the turn of the approval answer is replayed; a later turn runs it anew
        now = self.clock()
        self._recent_ok = {k: v for k, v in self._recent_ok.items() if k[0] != key or now - v[0] < UNTRUSTED_GRACE_S}

    def clear_taint(self, route: str) -> None:
        """The owner explicitly resumed work on `route` (a goal plan): the untrusted-content block is lifted there."""
        self._untrusted.pop(route, None)

    async def _ask(self, prompt: str, **meta) -> bool:
        """registry.confirm, with the human's thinking time recorded (it must never count as an AI timeout).
        `meta` (title, why, risk, kind, details, typed, timeout) reaches a confirm that declares `accepts_meta`
        (the Core's approvals service); a plain confirm(prompt) gets the prompt only."""
        t0 = time.monotonic()
        self._confirming_since = t0
        self.asks += 1
        try:
            if meta and getattr(self.confirm, "accepts_meta", False):
                return bool(await self.confirm(prompt, **meta))
            return bool(await self.confirm(prompt))
        finally:
            self._confirming_since = None
            self.confirm_wait_s += time.monotonic() - t0

    async def ask(self, prompt: str, **meta) -> bool:
        """For Skills that need an approval inside their handler (helper, feature_set): same accounting."""
        return await self._ask(prompt, **meta)

    def confirm_elapsed(self) -> float:
        """Total human approval time so far, including an approval still open."""
        open_s = time.monotonic() - self._confirming_since if self._confirming_since is not None else 0.0
        return self.confirm_wait_s + open_s

    def register(self, skill: Skill) -> None:
        if skill.name in self.skills:
            raise ValueError(f"skill {skill.name!r} already registered")
        tools = skill.tools()
        from ..guardrails import check_tool          # hard bans (no calendar delete), for every Skill incl. generated
        for t in tools:
            why = check_tool(skill.name, t.name, t.description)
            if why:
                logs.error("skills", "TOOL_GUARDRAIL", "tool rejected by a guardrail", why=why, skill=skill.name, tool=t.name)
                raise ValueError(why)
            if t.name in self._tools:
                raise ValueError(f"tool {t.name!r} already registered by {self._tools[t.name][0].name}")
        self.skills[skill.name] = skill
        for t in tools:
            self._tools[t.name] = (skill, t)
        logs.info("skills", "SKILL_REGISTERED", "skill registered", skill=skill.name, tools=[t.name for t in tools])

    def unregister(self, name: str) -> bool:
        """Remove a Skill and its tools (generated Skills: uninstall / rollback / new version). True if it existed."""
        skill = self.skills.pop(name, None)
        if skill is None:
            return False
        for tname in [t for t, (s, _) in self._tools.items() if s is skill]:
            del self._tools[tname]
        logs.info("skills", "SKILL_UNREGISTERED", "skill unregistered", skill=name)
        return True

    def _gated(self, skill: Skill, tool: Tool) -> str | None:
        if self.gate is None:
            return None
        try:
            return self.gate(skill.name, tool.name)
        except Exception:          # a broken switch check must not hide every tool or crash a turn
            logs.error("skills", "FEATURE_GATE_FAILED", "feature check failed", tool=tool.name, exc=True)
            return None

    def tools(self) -> list[Tool]:
        """Tools offered to the model: disabled Skills and inactive features are left out (read at each call, so a
        switch flipped mid-conversation shows on the next tool list)."""
        return [t for s, t in self._tools.values() if s.name not in self.disabled and self._gated(s, t) is None]

    def get(self, name: str) -> Tool | None:
        st = self._tools.get(name)
        return st[1] if st and st[0].name not in self.disabled and self._gated(*st) is None else None

    async def _authorize(self, tool: Tool, args: BaseModel, origin: str) -> ToolResult | None:
        """None = allowed to run; otherwise the refusal to return."""
        perm = tool.permission
        if origin not in ("model", "user") or perm not in PERMISSIONS or (
                perm == "action" and tool.name not in self.allowed_actions):
            logs.warn("skills", "TOOL_FORBIDDEN", "tool not authorized by policy; denied",
                      tool=tool.name, permission=str(perm), origin=str(origin))
            return ToolResult(False, f"{tool.name} is not allowed", code="TOOL_FORBIDDEN")
        if perm != "read" and origin == "model" and self._untrusted_at is not None:
            logs.warn("skills", "TOOL_UNTRUSTED_TURN", "tool refused: untrusted text was read this turn",
                      tool=tool.name)
            return ToolResult(False, "I just read text written by others (mail, calendar, a message or an agent's output), so I take no "
                              "action in this turn. Ask the user to say the request again.", code="TOOL_UNTRUSTED_TURN")
        if tool.precheck is not None:
            try:
                refusal = tool.precheck(args)
            except Exception:
                refusal = None
            if refusal is not None:
                return refusal
        prompt = tool.confirm
        if perm == "destructive":
            count, impact_prompt = (await tool.impact(args)) if tool.impact else (None, "")
            if origin == "model" or count is None or count > 1:
                prompt = prompt or impact_prompt or f"{tool.description} - approve?"
        meta = {"title": tool.name, "risk": "high" if perm == "destructive" else "medium", "source": origin,
                "kind": "message" if "send" in tool.name or "reply" in tool.name else "tool"}
        if tool.recipient is not None:
            try:
                who = tool.recipient(args)
            except Exception:
                who = None
            if who:
                meta["details"] = {"recipient": str(who)}       # spoken_kind() speaks its first name only
        if tool.approval_meta is not None and prompt:
            try:
                extra = tool.approval_meta(args) or {}
            except Exception:
                extra = {}
            meta["details"] = {**meta.get("details", {}), **(extra.get("details") or {})}
            for k in ("typed", "voice", "why"):
                if k in extra:
                    meta[k] = extra[k]
        if prompt and not await self._ask(prompt, **meta):
            logs.info("skills", "TOOL_DECLINED", "tool not confirmed by the user", tool=tool.name, origin=origin)
            return ToolResult(False, "cancelled: the user did not confirm", code="TOOL_DECLINED")
        return None

    async def _run(self, tool: Tool, args: BaseModel) -> ToolResult:
        mark = IN_TOOL.set(tool.name)          # tasks created below copy the context: the handler runs "in a tool"
        try:
            return await self._run_marked(tool, args)
        finally:
            IN_TOOL.reset(mark)

    async def _run_marked(self, tool: Tool, args: BaseModel) -> ToolResult:
        if tool.permission == "read":
            return await asyncio.wait_for(tool.handler(args), tool.timeout_s)
        # side effect: survives cancellation of the calling turn, but still honours the tool timeout
        task = asyncio.ensure_future(tool.handler(args))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)
        try:
            return await asyncio.wait_for(asyncio.shield(task), tool.timeout_s)
        except TimeoutError:
            task.cancel()
            raise

    async def call(self, name: str, raw_args: dict | str | None = None, *, origin: Origin = "model") -> ToolResult:
        res = await self._call(name, raw_args, origin=origin)
        _tool_seen(name, res.ok, res.code, res.message)   # terminal + web chat: one line per call (TOOL_SINK)
        return res

    async def _call(self, name: str, raw_args: dict | str | None = None, *, origin: Origin = "model") -> ToolResult:
        """Run a tool under the permission policy. origin="user" only when the deterministic command parser matched
        the user's own words; everything from a model (text agent, voice agent, ChatGPT plan) is "model"."""
        st = self._tools.get(name)
        if st is None:
            return ToolResult(False, f"unknown tool {name}", code="TOOL_UNKNOWN")
        skill, tool = st
        if skill.name in self.disabled:
            return ToolResult(False, f"skill {skill.name} is disabled", code="SKILL_DISABLED")
        off = self._gated(skill, tool)
        if off is not None:        # the model may still hold an older tool list: refuse truthfully
            logs.info("skills", "FEATURE_OFF", "tool refused: feature off", tool=name, reason=off)
            return self._hinted(skill, tool, ToolResult(False, f"not available: {off}", code="FEATURE_OFF"))
        try:
            if isinstance(raw_args, str):
                raw_args = json.loads(raw_args or "{}")
            args = tool.args.model_validate(raw_args or {})
        except ValidationError as e:        # never log the input values (may be private)
            bad = [".".join(map(str, err.get("loc", ()))) for err in e.errors(include_input=False)]
            logs.warn("skills", "TOOL_BAD_ARGS", "invalid tool arguments", tool=name, bad_fields=bad)
            return ToolResult(False, f"invalid arguments for {name}", code="TOOL_BAD_ARGS")
        except ValueError:
            logs.warn("skills", "TOOL_BAD_ARGS", "invalid tool arguments", tool=name)
            return ToolResult(False, f"invalid arguments for {name}", code="TOOL_BAD_ARGS")
        key = (ROUTE.get(), name, json.dumps(args.model_dump(mode="json"), sort_keys=True, ensure_ascii=False,
                                            default=str))
        last = self._recent_ok.get(key)
        if (last is not None and tool.permission != "read" and origin == "model"
                and (self._untrusted_at is not None or last[2]) and self.clock() - last[0] < REPEAT_REPLAY_S):
            # V4-VC-3 (campaign C): GPT-Live re-delegates the spoken approval "כן"; that second backend run repeats
            # the SAME action it just ran (approved): the untrusted-turn guard refused it (helper: the backend then
            # said "it did not work" instead of the real result) or it asked the owner a second time (list_clear).
            # The identical approved action is never run or asked again: its result is handed back as done.
            logs.info("skills", "TOOL_REPLAYED", "identical action already done this turn; result returned again",
                      tool=name)
            prev = last[1]
            return ToolResult(prev.ok, prev.message, {**prev.data, "already_done": "this exact action already ran a "
                              "moment ago (approved); do NOT run it again: report this result"}, code=prev.code)
        asked0 = self.asks
        refused = await self._authorize(tool, args, origin)
        if refused is not None:
            return refused
        t_run = time.monotonic()
        try:
            res = await self._run(tool, args)
        except TimeoutError:
            res = ToolResult(False, f"{name} timed out", code="TOOL_TIMEOUT")
        except Exception as e:  # a broken Skill must never take down the Core
            logs.error("skills", "TOOL_CRASH", "tool raised an exception", tool=name, exc=True)
            res = ToolResult(False, f"{name} failed: {type(e).__name__}", code="TOOL_CRASH")
        if tool.untrusted_output and (res.ok or _carries_untrusted(res.data)):
            # review C (SWV4): a FAILED run still returns third-party text (a helper command exiting 1 prints
            # whatever it likes into output_tail): that taints the turn exactly like a successful one
            self._untrusted_at = self.clock()
        if res.ok and tool.permission != "read":
            now = self.clock()
            self._recent_ok = {k: v for k, v in self._recent_ok.items() if now - v[0] < REPEAT_REPLAY_S}
            self._recent_ok[key] = (now, res, self.asks > asked0)    # approved by the owner this call
        log = logs.info if res.ok else logs.warn
        log("skills", "TOOL_RESULT", "tool finished", tool=name, ok=res.ok, result_code=res.code, origin=origin,
            ms=round((time.monotonic() - t_run) * 1000))      # SW-V4 O: tool p50/p95 in the health monitor
        return self._hinted(skill, tool, res) if not res.ok else res

    def _hinted(self, skill: Skill, tool: Tool, res: ToolResult) -> ToolResult:
        if self.setup_hint is None or "setup_hint" in res.data:
            return res
        try:
            hint = self.setup_hint(skill.name, tool.name, res)
        except Exception:          # a broken hint must never change the result
            logs.error("skills", "SETUP_HINT_FAILED", "setup hint failed", tool=tool.name, exc=True)
            return res
        if hint:
            res.data = {**res.data, "setup_hint": hint}
        return res

    async def drain(self, timeout_s: float = 5.0) -> None:
        """Wait for shielded side effects still running (for an orderly Core shutdown)."""
        pending = [t for t in self._inflight if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout_s)

    # --- adapters
    def agent_tools(self) -> list:
        """FunctionTools for the OpenAI Agents SDK (text agent). Every call from here is origin="model"."""
        from agents import FunctionTool

        def make(t: Tool):
            async def invoke(_ctx, args_json: str) -> str:
                return (await self.call(t.name, args_json, origin="model")).to_model()
            return FunctionTool(name=t.name, description=t.description, params_json_schema=tool_schema(t),
                                on_invoke_tool=invoke, strict_json_schema=False)
        return [make(t) for t in self.tools()]

    def realtime_tools(self) -> list:
        return self.agent_tools()   # RealtimeAgent accepts the same FunctionTool objects
