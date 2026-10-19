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
Untrusted content (WP-G): after a tool with `untrusted_output` (mail, calendar text written by others) returned in
the current user turn, the registry refuses model-origin action/destructive tools (TOOL_UNTRUSTED_TURN) until the
user speaks again (`new_turn`), so text inside a mail can never trigger a side effect by itself.
Action/destructive handlers run shielded from cancellation of the calling turn (the timeout still applies), so a
pre-empted turn or a shutdown never aborts a half-done side effect; read tools stay normally cancellable.
"""
from __future__ import annotations

import asyncio
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


async def _deny(_: str) -> bool:
    return False


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
        self._inflight: set[asyncio.Future] = set()
        self._untrusted_at: float | None = None   # monotonic time untrusted text entered this turn
        self.clock = time.monotonic

    def new_turn(self) -> None:
        """The user said something new: side effects are allowed again. A taint younger than UNTRUSTED_GRACE_S is
        kept: a voice transcript can complete just after the tool of that same turn returned."""
        if self._untrusted_at is not None and self.clock() - self._untrusted_at >= UNTRUSTED_GRACE_S:
            self._untrusted_at = None

    def register(self, skill: Skill) -> None:
        if skill.name in self.skills:
            raise ValueError(f"skill {skill.name!r} already registered")
        tools = skill.tools()
        for t in tools:
            if t.name in self._tools:
                raise ValueError(f"tool {t.name!r} already registered by {self._tools[t.name][0].name}")
        self.skills[skill.name] = skill
        for t in tools:
            self._tools[t.name] = (skill, t)
        logs.info("skills", "SKILL_REGISTERED", f"skill {skill.name} registered", tools=[t.name for t in tools])

    def _gated(self, skill: Skill, tool: Tool) -> str | None:
        if self.gate is None:
            return None
        try:
            return self.gate(skill.name, tool.name)
        except Exception:          # a broken switch check must not hide every tool or crash a turn
            logs.error("skills", "FEATURE_GATE_FAILED", f"feature check failed for {tool.name}", exc=True)
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
            logs.warn("skills", "TOOL_FORBIDDEN", f"{tool.name} is not authorized by policy; denied",
                      tool=tool.name, permission=str(perm), origin=str(origin))
            return ToolResult(False, f"{tool.name} is not allowed", code="TOOL_FORBIDDEN")
        if perm != "read" and origin == "model" and self._untrusted_at is not None:
            logs.warn("skills", "TOOL_UNTRUSTED_TURN", f"{tool.name} refused: mail/calendar text was read this turn",
                      tool=tool.name)
            return ToolResult(False, "I just read mail or calendar text written by others, so I take no action in "
                              "this turn. Ask the user to say the request again.", code="TOOL_UNTRUSTED_TURN")
        prompt = tool.confirm
        if perm == "destructive":
            count, impact_prompt = (await tool.impact(args)) if tool.impact else (None, "")
            if origin == "model" or count is None or count > 1:
                prompt = prompt or impact_prompt or f"{tool.description} - approve?"
        if prompt and not await self.confirm(prompt):
            logs.info("skills", "TOOL_DECLINED", f"{tool.name} not confirmed by user", tool=tool.name, origin=origin)
            return ToolResult(False, "cancelled: the user did not confirm", code="TOOL_DECLINED")
        return None

    async def _run(self, tool: Tool, args: BaseModel) -> ToolResult:
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
            logs.info("skills", "FEATURE_OFF", f"{name} refused: {off}", tool=name)
            return ToolResult(False, f"not available: {off}", code="FEATURE_OFF")
        try:
            if isinstance(raw_args, str):
                raw_args = json.loads(raw_args or "{}")
            args = tool.args.model_validate(raw_args or {})
        except ValidationError as e:        # never log the input values (may be private)
            bad = [".".join(map(str, err.get("loc", ()))) for err in e.errors(include_input=False)]
            logs.warn("skills", "TOOL_BAD_ARGS", f"invalid arguments for {name}", bad_fields=bad)
            return ToolResult(False, f"invalid arguments for {name}", code="TOOL_BAD_ARGS")
        except ValueError:
            logs.warn("skills", "TOOL_BAD_ARGS", f"invalid arguments for {name}")
            return ToolResult(False, f"invalid arguments for {name}", code="TOOL_BAD_ARGS")
        refused = await self._authorize(tool, args, origin)
        if refused is not None:
            return refused
        try:
            res = await self._run(tool, args)
        except TimeoutError:
            res = ToolResult(False, f"{name} timed out", code="TOOL_TIMEOUT")
        except Exception as e:  # a broken Skill must never take down the Core
            logs.error("skills", "TOOL_CRASH", f"{name} raised", exc=True)
            res = ToolResult(False, f"{name} failed: {type(e).__name__}", code="TOOL_CRASH")
        if tool.untrusted_output and res.ok:
            self._untrusted_at = self.clock()
        log = logs.info if res.ok else logs.warn
        log("skills", "TOOL_RESULT", f"{name} -> {res.code}", tool=name, ok=res.ok, origin=origin)
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
            return FunctionTool(name=t.name, description=t.description, params_json_schema=t.args.model_json_schema(),
                                on_invoke_tool=invoke, strict_json_schema=False)
        return [make(t) for t in self.tools()]

    def realtime_tools(self) -> list:
        return self.agent_tools()   # RealtimeAgent accepts the same FunctionTool objects
