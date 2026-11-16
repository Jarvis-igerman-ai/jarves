"""JAR-258 / #831: with drive mode on, a calendar read (full event titles) is spoken only when the owner's saved
drive_briefing.passenger setting is "alone". ask, present, an unknown value or a broken setting defer it (fail closed).
The phone's read-only screen is a display, not speech, so it is not affected."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from jarves import drive_mode as dm
from jarves.skills import ROUTE, Skill, SkillRegistry, Tool, ToolResult

CAL_TOOLS = ("calendar_list_events", "calendar_next_event", "calendar_find_events")


class NoArgs(BaseModel):
    pass


def make(tmp_path, passenger, *, active=True):
    ran = []

    class Cal(Skill):
        name = "cal"

        def tools(self):
            def tool(name):
                async def run(_a):
                    ran.append(name)
                    return ToolResult(True, "Dentist with Dana, 10:00")
                return Tool(name, "cal", run, NoArgs, permission="read", untrusted_output=True)
            return [tool(n) for n in CAL_TOOLS]
    reg = SkillRegistry()
    reg.register(Cal())
    reg.drive = dm.Gate(active=lambda: active, queue=dm.DriveQueue(tmp_path / "q.json"), passenger=passenger)
    return reg, ran


async def call(reg, name, route="voice"):
    tok = ROUTE.set(route)
    try:
        return await reg.call(name, {})
    finally:
        ROUTE.reset(tok)


def _boom():
    raise RuntimeError("settings db locked")


@pytest.mark.parametrize("name", CAL_TOOLS)
@pytest.mark.parametrize("passenger", [lambda: "ask", lambda: "present", lambda: None, lambda: "ALONE",
                                       lambda: "maybe", _boom])
async def test_calendar_read_deferred_unless_alone(tmp_path, name, passenger):
    reg, ran = make(tmp_path, passenger)
    res = await call(reg, name)
    assert not res.ok and res.code == "DRIVE_DEFERRED"
    assert "Dentist" not in res.message and dm.say("DRIVE_PASSENGER") in res.message
    assert ran == []
    assert [i.action for i in reg.drive.queue.items()] == ["calendar_read"]


@pytest.mark.parametrize("name", CAL_TOOLS)
async def test_calendar_read_spoken_when_alone(tmp_path, name):
    reg, ran = make(tmp_path, lambda: "alone")
    res = await call(reg, name)
    assert res.ok and "Dentist" in res.message and ran == [name]
    assert reg.drive.queue.items() == []


async def test_drive_off_is_unchanged(tmp_path):
    reg, ran = make(tmp_path, lambda: "present", active=False)
    assert (await call(reg, "calendar_next_event")).ok and ran == ["calendar_next_event"]


async def test_phone_screen_display_not_affected(tmp_path):
    reg, ran = make(tmp_path, lambda: "present")
    res = await call(reg, "calendar_next_event", route=dm.PHONE_SCREEN)
    assert res.ok and ran == ["calendar_next_event"] and reg.drive.queue.items() == []


async def test_attach_reads_the_saved_setting(tmp_path):
    from jarves.userdata import open_db
    reg, ran = make(tmp_path, lambda: "ask")
    gate = dm.attach(reg, SimpleNamespace(active=lambda f: True), tmp_path)
    assert (await call(reg, "calendar_next_event")).code == "DRIVE_DEFERRED"     # default ask
    open_db(tmp_path).set_setting("drive_briefing.passenger", "alone")
    assert gate.passenger() == "alone"
    assert (await call(reg, "calendar_next_event")).ok and ran == ["calendar_next_event"]
    open_db(tmp_path).set_setting("drive_briefing.passenger", "present")
    assert (await call(reg, "calendar_next_event")).code == "DRIVE_DEFERRED"
