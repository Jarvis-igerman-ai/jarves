"""Minimal Skills interface: a Skill is a named group of tools with validated arguments, explicit side-effect
flags and optional user confirmation. The same tools are exposed to the OpenAI agent, the realtime voice agent
and the offline intent router, so adding a Skill never requires Core changes.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from .. import logs


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


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[[Any], Awaitable[ToolResult]]
    args: type[BaseModel] = NoArgs
    side_effect: bool = False          # changes something outside the conversation (player, data)
    confirm: str | None = None         # if set, the user must approve this prompt first
    timeout_s: float = 10.0


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
    def __init__(self, disabled: set[str] | None = None) -> None:
        self.skills: dict[str, Skill] = {}
        self._tools: dict[str, tuple[Skill, Tool]] = {}
        self.disabled = disabled or set()
        self.confirm: ConfirmFn = _deny       # replaced by the Core (asks the user via Godot/voice)

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

    def tools(self) -> list[Tool]:
        return [t for s, t in self._tools.values() if s.name not in self.disabled]

    def get(self, name: str) -> Tool | None:
        st = self._tools.get(name)
        return st[1] if st and st[0].name not in self.disabled else None

    async def call(self, name: str, raw_args: dict | str | None = None) -> ToolResult:
        st = self._tools.get(name)
        if st is None:
            return ToolResult(False, f"unknown tool {name}", code="TOOL_UNKNOWN")
        skill, tool = st
        if skill.name in self.disabled:
            return ToolResult(False, f"skill {skill.name} is disabled", code="SKILL_DISABLED")
        try:
            if isinstance(raw_args, str):
                raw_args = json.loads(raw_args or "{}")
            args = tool.args.model_validate(raw_args or {})
        except (ValidationError, ValueError) as e:
            logs.warn("skills", "TOOL_BAD_ARGS", f"invalid arguments for {name}", error=str(e)[:200])
            return ToolResult(False, f"invalid arguments for {name}", code="TOOL_BAD_ARGS")
        if tool.confirm:
            if not await self.confirm(tool.confirm):
                logs.info("skills", "TOOL_DECLINED", f"{name} not confirmed by user")
                return ToolResult(False, "cancelled: the user did not confirm", code="TOOL_DECLINED")
        try:
            res = await asyncio.wait_for(tool.handler(args), tool.timeout_s)
        except asyncio.TimeoutError:
            res = ToolResult(False, f"{name} timed out", code="TOOL_TIMEOUT")
        except Exception as e:  # a broken Skill must never take down the Core
            logs.error("skills", "TOOL_CRASH", f"{name} raised", exc=True)
            res = ToolResult(False, f"{name} failed: {type(e).__name__}", code="TOOL_CRASH")
        log = logs.info if res.ok else logs.warn
        log("skills", "TOOL_RESULT", f"{name} -> {res.code}", tool=name, ok=res.ok)
        return res

    # --- adapters
    def agent_tools(self) -> list:
        """FunctionTools for the OpenAI Agents SDK (text agent)."""
        from agents import FunctionTool

        def make(t: Tool):
            async def invoke(_ctx, args_json: str) -> str:
                return (await self.call(t.name, args_json)).to_model()
            return FunctionTool(name=t.name, description=t.description, params_json_schema=t.args.model_json_schema(),
                                on_invoke_tool=invoke, strict_json_schema=False)
        return [make(t) for t in self.tools()]

    def realtime_tools(self) -> list:
        return self.agent_tools()   # RealtimeAgent accepts the same FunctionTool objects
