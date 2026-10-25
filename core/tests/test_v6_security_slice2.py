"""Security sweep slice 2 (issue #324): while drive mode is on, the pre-drive briefing is refused, never queued."""
from __future__ import annotations

from pydantic import BaseModel

from jarves import drive_mode as dm
from jarves.skills import ROUTE, Tool, ToolResult


class NoA(BaseModel):
    pass


async def _run(_a):
    return ToolResult(True, "ran")


async def test_briefing_is_refused_not_queued_while_drive_mode_is_on(tmp_path):
    q = dm.DriveQueue(tmp_path / "q.json")
    active = {"on": True}
    gate = dm.Gate(active=lambda: active["on"], queue=q)
    tool = Tool("drive_briefing_get", "t", _run, NoA, permission="read", untrusted_output=True, origin="builtin", skill_id="drive_briefing")
    tok = ROUTE.set("voice")
    try:
        r = gate.check(tool, NoA(), origin="user", route="voice", tainted=False)
    finally:
        ROUTE.reset(tok)
    assert r is not None and not r.ok and r.code == "DRIVE_REFUSED"
    assert r.message and dm.say("DRIVE_BRIEFING_FIRST", "he") and "before drive mode" in r.message
    assert q.items() == []                                          # no junk "when you stop" item
    active["on"] = False
    assert gate.check(tool, NoA(), origin="user", route="voice", tainted=False) is None     # off: unchanged
    active["on"] = True                                             # the phone screen route is separate and still reads
    assert gate.check(tool, NoA(), origin="user", route=dm.PHONE_SCREEN, tainted=False) is None
