"""#379: send tools declare their real target (the contact check is not by name alone); the Gate never blocks the
event loop on the cross-process queue lock."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from jarves import drive_mode as dm
from jarves.skills import ROUTE, Skill, SkillRegistry, Tool, ToolResult


class A(BaseModel):
    draft_id: str = "d1"


def make(tmp_path, target):
    class S(Skill):
        name = "s"

        def tools(self):
            async def run(_a):
                return ToolResult(True, "sent")
            return [Tool("whatsapp_send", "send", run, A, permission="action", confirm="ok?",
                         recipient=lambda a: "Dana", target=lambda a: target)]
    reg = SkillRegistry(allowed_actions={"whatsapp_send"})
    reg.register(S())

    async def confirm(_p):
        return True
    reg.confirm = confirm
    reg.drive = dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"),
                        contacts=lambda: [{"name": "Dana", "phone": "0541234567"}])
    return reg


@pytest.mark.parametrize("target,code", [("0541234567", None), ("0509999999", "DRIVE_DEFERRED"), ("", "DRIVE_DEFERRED")])
async def test_name_alone_is_not_enough_real_target_decides(tmp_path, target, code):
    reg = make(tmp_path, target)
    tok = ROUTE.set("voice")
    try:
        res = await reg.call("whatsapp_send", {})
    finally:
        ROUTE.reset(tok)
    assert (res.code if not res.ok else None) == code


def _real_target(skill, tool_name, draft):
    skill._drafts["d1"] = draft
    tool = next(t for t in skill.tools() if t.name == tool_name)
    assert tool.target is not None, f"{tool_name} must declare a target"
    return tool.target(SimpleNamespace(draft_id="d1"))


def _gate(tmp_path, phone):
    return dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"),
                   contacts=lambda: [{"name": "Dana", "phone": phone}])


def test_whatsapp_bridge_target_is_the_draft_number(tmp_path):
    from jarves import whatsapp_bridge as wb
    skill = wb.WhatsAppBridgeSkill(tmp_path)
    assert _real_target(skill, "whatsapp_send", {"name": "Dana", "number": "0541234567"}) == "0541234567"
    assert _real_target(skill, "whatsapp_send", {"name": "Dana"}) == ""     # LID contact: never known


def test_telegram_and_instagram_numeric_ids_are_never_a_saved_phone(tmp_path):
    """A Telegram chat id / Instagram sender id like 972541234567 must NOT count as Dana's saved number: the real
    tools declare no usable target, so the Gate defers the send while driving."""
    from jarves import instagram_channel as ig
    from jarves import telegram_channel as tg
    gate = _gate(tmp_path, "0541234567")
    assert gate.known("Dana", "972541234567")                               # control: the same digits WOULD match
    tgt = _real_target(tg.TelegramSkill(tmp_path), "telegram_send", {"name": "Dana", "chat_id": 972541234567})
    igt = _real_target(ig.InstagramSkill(tmp_path), "instagram_send", {"name": "Dana", "sender": "972541234567"})
    assert not gate.known("Dana", tgt)
    assert not gate.known("Dana", igt)


async def test_gate_lock_wait_does_not_block_the_event_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "LOCK_WAIT_S", 0.6)
    reg = make(tmp_path, "0509999999")             # unknown target -> queue.add -> takes the file lock
    ticks = []

    async def ticker():
        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.02)
    t = asyncio.create_task(ticker())
    with dm._file_lock(reg.drive.queue.path):      # another "process" holds the lock
        tok = ROUTE.set("voice")
        try:
            res = await reg.call("whatsapp_send", {})
        finally:
            ROUTE.reset(tok)
    t.cancel()
    assert not res.ok                              # failed closed (lock timeout)
    assert len(ticks) > 10                         # the loop kept running during the 0.6 s wait


def test_whatsapp_digits_only_number_gets_plus_so_non_israeli_contact_is_known(tmp_path):
    """#833: the bridge number is digits-only; a saved +1 contact must match (fake data, in-car check UNVERIFIED)."""
    from jarves import whatsapp_bridge as wb
    skill = wb.WhatsAppBridgeSkill(tmp_path)
    tgt = _real_target(skill, "whatsapp_send", {"name": "Sam", "number": "12025550123"})
    assert tgt == "+12025550123"
    gate = dm.Gate(active=lambda: True, queue=dm.DriveQueue(tmp_path / "q.json"),
                   contacts=lambda: [{"name": "Sam", "phone": "+12025550123"}])
    assert gate.known("Sam", tgt)
    assert not gate.known("Sam", "12025550123")                            # control: without "+" it deferred


def test_passenger_opens_one_db_connection(tmp_path, monkeypatch):
    from jarves import userdata
    calls = []
    real = userdata.open_db
    monkeypatch.setattr(userdata, "open_db", lambda h: calls.append(1) or real(h))
    reg = SimpleNamespace()
    feats = SimpleNamespace(active=lambda f: True)
    gate = dm.attach(reg, feats, tmp_path)
    assert gate.passenger() == "ask" and gate.passenger() == "ask"
    assert len(calls) == 1
