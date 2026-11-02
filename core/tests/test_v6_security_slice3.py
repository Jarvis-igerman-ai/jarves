"""V6 security review follow-ups (issues #830, #831): send tools declare a real target so the drive-mode
known-contact check never matches by display name, and calendar reads honour the passenger setting. Offline."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from jarves import drive_mode as dm
from jarves.instagram_channel import InstagramSkill
from jarves.skills import ROUTE, SkillRegistry, Tool, ToolResult
from jarves.telegram_channel import TelegramSkill
from jarves.whatsapp import WhatsAppSkill

CONTACTS = [{"name": "Dana Levi", "phone": "054-123-4567"}]


def _tool(skill, name):
    return next(t for t in skill.tools() if t.name == name)


def test_instagram_and_telegram_send_declare_a_target_never_matching_a_contact(tmp_path):
    ig = InstagramSkill(tmp_path)
    tg = TelegramSkill(tmp_path)
    ig._drafts["d1"] = {"name": "Dana Levi", "sender": "972541234567"}
    tg._drafts["d1"] = {"name": "Dana Levi", "chat_id": 972541234567}

    class A(BaseModel):
        draft_id: str = "d1"
    g = dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"), contacts=lambda: list(CONTACTS))
    for t in (_tool(ig, "instagram_send"), _tool(tg, "telegram_send")):
        tgt = g.target(t, A())
        assert tgt == ""                                   # declared, but not an address
        assert not g.known(g.recipient(t, A()), tgt)       # a stranger named "Dana Levi" is NOT a known contact
        assert g.known("Dana Levi")                        # (the old name-only path would have said yes)


def test_whatsapp_draft_target_is_the_real_number_not_the_name(tmp_path):
    wa = WhatsAppSkill(tmp_path)
    t = _tool(wa, "whatsapp_draft")
    g = dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"), contacts=lambda: list(CONTACTS))
    a = t.args(name="Dana Levi", phone="0500000000", text="hi")
    assert g.target(t, a) and not g.known("Dana Levi", g.target(t, a))
    a2 = t.args(name="Dana Levi", phone="054-123-4567", text="hi")
    assert g.known("Dana Levi", g.target(t, a2))
    a3 = t.args(name="Nobody Atall", phone="", text="hi")
    assert g.target(t, a3) == "" and not g.known("Nobody Atall", g.target(t, a3))


class NoA(BaseModel):
    pass


def _cal_registry(tmp_path, passenger):
    async def run(_a):
        return ToolResult(True, "Meeting with the doctor")

    class S(__import__("jarves.skills", fromlist=["Skill"]).Skill):
        name = "calendar"

        def tools(self):
            return [Tool("calendar_next_event", "t", run, NoA, permission="read")]
    S.origin = "builtin"
    reg = SkillRegistry(allowed_actions=set())
    reg.register(S())
    reg.drive = dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"), passenger=lambda: passenger)
    return reg


@pytest.mark.parametrize("mode,deferred", [("ask", True), ("present", True), ("garbage", True), ("alone", False)])
async def test_calendar_read_in_drive_mode_follows_the_passenger_setting(tmp_path, mode, deferred):
    reg = _cal_registry(tmp_path, mode)
    tok = ROUTE.set("voice")
    try:
        res = await reg.call("calendar_next_event", {})
    finally:
        ROUTE.reset(tok)
    if deferred:
        assert res.code == "DRIVE_DEFERRED" and "Meeting" not in res.message
    else:
        assert res.ok


async def test_calendar_read_with_a_broken_passenger_setting_fails_closed(tmp_path):
    reg = _cal_registry(tmp_path, "alone")

    def boom():
        raise RuntimeError("db")
    reg.drive.passenger = boom
    tok = ROUTE.set("voice")
    try:
        res = await reg.call("calendar_next_event", {})
    finally:
        ROUTE.reset(tok)
    assert res.code == "DRIVE_DEFERRED"


def test_attach_reads_the_saved_passenger_setting(tmp_path):
    from jarves.userdata import open_db

    class F:
        def active(self, _):
            return True
    reg = SkillRegistry(allowed_actions=set())
    g = dm.attach(reg, F(), tmp_path)
    assert g._passenger_mode() == "ask"
    open_db(tmp_path).set_setting("drive_briefing.passenger", "alone")
    assert g._passenger_mode() == "alone"
