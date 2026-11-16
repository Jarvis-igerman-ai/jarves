"""Hermetic tests for jarves.workflow_cli (argument forwarding only; the workflow service is stubbed)."""
from __future__ import annotations

import argparse
from pathlib import Path

from jarves import workflow_cli


def _args(*rest):
    return argparse.Namespace(workflow_args=list(rest))


def test_add_cli_registers_workflow_command_with_remainder():
    ap = argparse.ArgumentParser()
    workflow_cli.add_cli(ap.add_subparsers(dest="cmd"))
    ns = ap.parse_args(["workflow", "status", "--x", "1"])
    assert ns.cmd == "workflow"
    assert ns.workflow_args == ["status", "--x", "1"]


def test_commands_maps_workflow_to_handler():
    assert workflow_cli.COMMANDS["workflow"] is workflow_cli.cmd_workflow


def test_cmd_workflow_defaults_to_status_and_adds_profile_home(monkeypatch):
    import jarves.workflow.__main__ as wm
    seen = []
    monkeypatch.setattr(wm, "main", lambda argv: seen.append(argv) or 7)
    assert workflow_cli.cmd_workflow(_args(), None, Path("h")) == 7
    assert seen == [["status", "--profile-home", "h"]]


def test_cmd_workflow_keeps_explicit_profile_home(monkeypatch):
    import jarves.workflow.__main__ as wm
    seen = []
    monkeypatch.setattr(wm, "main", lambda argv: seen.append(argv) or 0)
    workflow_cli.cmd_workflow(_args("today", "--profile-home", "z"), None, Path("h"))
    assert seen == [["today", "--profile-home", "z"]]
