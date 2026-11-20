"""JAR-258 / issue #831: in drive mode, with a passenger present, no calendar read may return full event titles.
Covers every calendar read tool name the drive-mode gate knows. Offline, fake calendar.
Titles are spoken only when the saved drive_briefing.passenger is exactly "alone"; ask, unknown, wrong case or a
broken setting defer (fail closed). Drive mode off and the phone's read-only screen are not affected."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from jarves import drive_mode as dm
from jarves.skills import ROUTE, Skill, SkillRegistry, Tool, ToolResult

TITLE = "Oncology appointment with Dr. Cohen"
CAL_TOOLS = sorted(n for n, k in dm.TOOL_KINDS.items() if k == "calendar_read")


class NoA(BaseModel):
    pass


def _registry(tmp_path, passenger, *, active=True, ran=None) -> SkillRegistry:
    def tool(name):
        async def run(_a):
            if ran is not None:
                ran.append(name)
            return ToolResult(True, TITLE, {"events": [{"title": TITLE}]})
        return Tool(name, "t", run, NoA, permission="read")

    class Cal(Skill):
        name = "calendar"

        def tools(self):
            return [tool(n) for n in CAL_TOOLS]
    Cal.origin = "builtin"
    reg = SkillRegistry(allowed_actions=set())
    reg.register(Cal())
    reg.drive = dm.Gate(active=lambda: active, queue=dm.DriveQueue(tmp_path / "q.json"), passenger=passenger if callable(passenger) else (lambda: passenger))
    return reg


async def _call(reg, name, route="voice"):
    tok = ROUTE.set(route)
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


def _broken_setting():
    raise RuntimeError("settings db locked")


@pytest.mark.parametrize("name", CAL_TOOLS)
@pytest.mark.parametrize("passenger", ["ask", None, "ALONE", "maybe", _broken_setting])
async def test_any_setting_but_alone_defers_without_running_the_tool(tmp_path, name, passenger):
    ran = []
    reg = _registry(tmp_path, passenger, ran=ran)
    res = await _call(reg, name)
    assert not res.ok and res.code == "DRIVE_DEFERRED"
    assert TITLE not in res.message and dm.say("DRIVE_PASSENGER") in res.message
    assert ran == [] and [i.action for i in reg.drive.queue.items()] == ["calendar_read"]


async def test_drive_off_is_unchanged(tmp_path):
    ran = []
    res = await _call(_registry(tmp_path, "present", active=False, ran=ran), "calendar_next_event")
    assert res.ok and ran == ["calendar_next_event"]


async def test_phone_screen_display_not_affected(tmp_path):
    ran = []
    reg = _registry(tmp_path, "present", ran=ran)
    res = await _call(reg, "calendar_next_event", route=dm.PHONE_SCREEN)
    assert res.ok and ran == ["calendar_next_event"] and reg.drive.queue.items() == []


async def test_attach_follows_the_saved_setting(tmp_path):
    from jarves.userdata import open_db
    reg = _registry(tmp_path, "ask")
    gate = dm.attach(reg, SimpleNamespace(active=lambda f: True), tmp_path)
    assert (await _call(reg, "calendar_next_event")).code == "DRIVE_DEFERRED"     # default ask
    open_db(tmp_path).set_setting("drive_briefing.passenger", "alone")
    assert gate.passenger() == "alone"
    assert (await _call(reg, "calendar_next_event")).ok
    open_db(tmp_path).set_setting("drive_briefing.passenger", "present")
    assert (await _call(reg, "calendar_next_event")).code == "DRIVE_DEFERRED"
