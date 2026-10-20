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


def test_b03_memory_delete_needs_a_number_and_does_not_traceback(tmp_path, capsys):
    a = argparse.Namespace(action="delete", id=None, yes=False)
    assert cmd_memory(a, Config(), tmp_path) == 2
    assert "jarvis memory list" in capsys.readouterr().err
    a = argparse.Namespace(action="delete", id="abc", yes=False)
    assert cmd_memory(a, Config(), tmp_path) == 2
    a = argparse.Namespace(action="delete", id="99999", yes=False)
    assert cmd_memory(a, Config(), tmp_path) == 0
    assert "not found" in capsys.readouterr().out
