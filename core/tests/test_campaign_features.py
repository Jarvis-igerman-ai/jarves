"""Regression tests for bugs found by the SW-V3 feature-by-feature campaign (PASS B, 20 Oct 2026)."""
from __future__ import annotations

import argparse

from jarves import features as feat
from jarves.cli import cmd_memory
from jarves.config import Config
from jarves.setup_wizard import ADDON_MODULES, load_addons


def test_b01_every_core_feature_is_registered_by_the_cli_addon_loader():
    """`jarvis features` loads ADDON_MODULES; a module that registers a switch but is missing there (context_trace)
    made the switch invisible and un-switchable from the CLI while the dashboard showed it."""
    assert "jarves.context_trace" in ADDON_MODULES
    assert load_addons() == []
    assert "context_trace" in feat.CATALOG


async def test_b04_refused_helper_command_returns_the_refusal_not_a_crash(tmp_path):
    """logs.info(..., code=...) collided with logs.info's own `code` parameter: every REFUSED command (sudo, runas,
    multi-line, JARVES data folder...) crashed the tool with a TypeError (TOOL_CRASH) instead of its refusal text."""
    from jarves.skills import SkillRegistry
    from jarves.skills import helper as hp
    f = feat.attach(feat.Features(Config(), tmp_path))
    try:
        f.set("helper", True)
        reg = SkillRegistry()
        reg.gate = f.gate
        hp.register_helper(reg, tmp_path)
        for cmd in ("sudo ls", "runas /user:Administrator cmd", "echo a\necho b", ""):
            r = await reg.call("shell_propose", {"command": cmd, "purpose": "campaign"})
            assert not r.ok and r.code.startswith("HELPER_"), (cmd, r.code, r.message)
    finally:
        feat.detach()


async def test_b05_cli_answer_file_approves_a_request_asked_from_inside_a_tool(tmp_path):
    """The answers poller inherited the asking tool's IN_TOOL marker and rejected every file answer
    (`jarvis approvals approve`, a dashboard in another process) as APPROVAL_FROM_TOOL: helper commands asked from
    shell_propose could only be approved in-process."""
    import asyncio

    from jarves import approvals as appr
    from jarves.skills import IN_TOOL

    class Ch:
        name = "face"

        def available(self, a):
            return True

        def open(self, a):
            return None

        def close(self, a):
            return None

    svc = appr.ApprovalService(tmp_path)
    svc.channels.append(Ch())
    mark = IN_TOOL.set("shell_propose")
    try:
        task = asyncio.create_task(svc.ask("run it?", kind="shell", risk="medium"))   # copies the context: "in a tool"
    finally:
        IN_TOOL.reset(mark)
    for _ in range(50):
        if svc.has_pending():
            break
        await asyncio.sleep(0.05)
    aid = svc.pending()[0]["id"]
    ok, why = appr.submit_answer(tmp_path, aid, True, channel="cli")      # not the in-process service: via the file
    if svc.home.resolve() == tmp_path.resolve():
        pass
    assert ok, why
    assert await asyncio.wait_for(task, 5) is True


def test_b06_face_recognition_can_become_active_when_the_camera_skill_is_there(tmp_path):
    """The probe asked for a Skill named 'faces' that nothing registers (the camera Skill owns enroll / who), so in the
    running Core the switch was permanently 'not installed yet (no connector in this version)'."""
    f = feat.Features(Config(), tmp_path, components=lambda: {"camera": {"status": "ok"}}, skills=lambda: {"camera"})
    f.set("camera", True)
    f.set("face_recognition", True)
    ok, why = f.available("face_recognition")
    assert ok, why
    f2 = feat.Features(Config(), tmp_path, components=lambda: {"camera": {"status": "ok"}}, skills=lambda: {"clock"})
    assert not f2.available("face_recognition")[0]          # without the camera Skill it is honestly unavailable


def test_b03_memory_delete_needs_a_number_and_does_not_traceback(tmp_path, capsys):
    a = argparse.Namespace(action="delete", id=None, yes=False)
    assert cmd_memory(a, Config(), tmp_path) == 2
    assert "jarvis memory list" in capsys.readouterr().err
    a = argparse.Namespace(action="delete", id="abc", yes=False)
    assert cmd_memory(a, Config(), tmp_path) == 2
    a = argparse.Namespace(action="delete", id="99999", yes=False)
    assert cmd_memory(a, Config(), tmp_path) == 0
    assert "not found" in capsys.readouterr().out
