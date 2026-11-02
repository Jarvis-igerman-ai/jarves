"""#379: send tools declare their real target (the contact check is not by name alone); the Gate never blocks the
event loop on the cross-process queue lock."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import time

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


@pytest.mark.parametrize("mod,name", [("whatsapp_bridge", "whatsapp_send"), ("instagram_channel", "instagram_send"),
                                      ("telegram_channel", "telegram_send")])
def test_real_send_tools_declare_a_target(mod, name):
    src = inspect.getsource(importlib.import_module("jarves." + mod))
    seg = src.split(f'Tool("{name}"', 1)[1].split("\n        ]", 1)[0]
    assert "target=" in seg


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
