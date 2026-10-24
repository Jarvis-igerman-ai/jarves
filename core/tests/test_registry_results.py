"""Every completed tool call records its outcome, including policy refusals (no real services)."""
import asyncio

import pytest
from pydantic import BaseModel

from jarves import logs
from jarves.skills import Skill, SkillRegistry, Tool, ToolResult


class Args(BaseModel):
    value: int


@pytest.mark.parametrize("case,code,runs", [
    ("unknown", "TOOL_UNKNOWN", 0),
    ("disabled", "SKILL_DISABLED", 0),
    ("off", "FEATURE_OFF", 0),
    ("bad_json", "TOOL_BAD_ARGS", 0),
    ("bad_args", "TOOL_BAD_ARGS", 0),
    ("forbidden", "TOOL_FORBIDDEN", 0),
    ("untrusted", "TOOL_UNTRUSTED_TURN", 0),
    ("precheck", "NOT_READY", 0),
    ("declined", "TOOL_DECLINED", 0),
    ("success", "ok", 1),
    ("failure", "FAILED", 1),
    ("crash", "TOOL_CRASH", 1),
    ("timeout", "TOOL_TIMEOUT", 1),
    ("replay", "ok", 1),
])
async def test_each_completed_call_logs_one_content_free_result(monkeypatch, case, code, runs):
    calls, records = [], []

    async def handle(args):
        calls.append(args.value)
        if case == "crash":
            raise RuntimeError("private handler detail")
        if case == "timeout":
            await asyncio.sleep(1)
        return ToolResult(case != "failure", "private response", {"private": "data"},
                          code="FAILED" if case == "failure" else "ok")

    tool = Tool("test_result", "Test a local result", handle, args=Args,
                permission="action", timeout_s=0.01 if case == "timeout" else 10.0)

    class TestSkill(Skill):
        name = "test_results"

        def tools(self):
            return [tool]

    reg = SkillRegistry(allowed_actions={tool.name})
    reg.register(TestSkill())
    name, args = tool.name, {"value": 3}
    if case == "unknown":
        name = "missing_result"
    elif case == "disabled":
        reg.disabled.add("test_results")
    elif case == "off":
        reg.gate = lambda skill, tool: "disabled"
    elif case == "bad_json":
        args = "{private invalid JSON"
    elif case == "bad_args":
        args = {"value": "private invalid value"}
    elif case == "forbidden":
        reg.allowed_actions.clear()
    elif case == "untrusted":
        reg._untrusted_at = reg.clock()
    elif case == "precheck":
        tool.precheck = lambda args: ToolResult(False, "private reason", code="NOT_READY")
    elif case in ("declined", "replay"):
        tool.confirm = "private approval details"

        async def confirm(prompt):
            return case == "replay"

        reg.confirm = confirm

    def capture(component, event, message, **fields):
        if event == "TOOL_RESULT":
            records.append((component, message, fields))

    monkeypatch.setattr(logs, "info", capture)
    monkeypatch.setattr(logs, "warn", capture)
    results = [await reg.call(name, args)]
    if case == "replay":
        results.append(await reg.call(name, args))
        assert results[-1].data["already_done"]
    assert len(calls) == runs
    assert len(records) == len(results)
    for result, (component, message, fields) in zip(results, records):
        assert result.code == code
        assert component == "skills" and message == "tool finished"
        assert fields == {"tool": name, "ok": result.ok, "result_code": code,
                          "origin": "model", "ms": fields["ms"]}
        assert isinstance(fields["ms"], int) and fields["ms"] >= 0
