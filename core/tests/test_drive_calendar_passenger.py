"""JAR-258 / issue #831: in drive mode, with a passenger present, no calendar read may return full event titles.
Covers every calendar read tool name the drive-mode gate knows. Offline, fake calendar."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from jarves import drive_mode as dm
from jarves.skills import ROUTE, Skill, SkillRegistry, Tool, ToolResult

TITLE = "Oncology appointment with Dr. Cohen"
CAL_TOOLS = sorted(n for n, k in dm.TOOL_KINDS.items() if k == "calendar_read")


class NoA(BaseModel):
    pass


def _registry(tmp_path, passenger: str) -> SkillRegistry:
    async def run(_a):
        return ToolResult(True, TITLE, {"events": [{"title": TITLE}]})

    class Cal(Skill):
        name = "calendar"

        def tools(self):
            return [Tool(n, "t", run, NoA, permission="read") for n in CAL_TOOLS]
    Cal.origin = "builtin"
    reg = SkillRegistry(allowed_actions=set())
    reg.register(Cal())
    reg.drive = dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"), passenger=lambda: passenger)
    return reg


async def _call(reg, name):
    tok = ROUTE.set("voice")
    try:
        return await reg.call(name, {})
    finally:
        ROUTE.reset(tok)


def test_all_calendar_read_tools_are_covered():
    assert set(CAL_TOOLS) >= {"calendar_list_events", "calendar_next_event", "calendar_find_events"}


@pytest.mark.parametrize("name", CAL_TOOLS)
async def test_passenger_present_drive_calendar_read_never_returns_full_titles(tmp_path, name):
    res = await _call(_registry(tmp_path, "present"), name)
    assert not res.ok and res.code == "DRIVE_DEFERRED"
    assert TITLE not in res.message and TITLE not in str(res.data)


@pytest.mark.parametrize("name", CAL_TOOLS)
async def test_owner_alone_drive_calendar_read_returns_the_event(tmp_path, name):
    res = await _call(_registry(tmp_path, "alone"), name)
    assert res.ok and TITLE in res.message
