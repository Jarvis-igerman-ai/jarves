"""Launcher flags must reach the root CLI without starting a Core, voice or setup wizard.
Windows runs the real jarvis.cmd; Linux has no .cmd launcher, so its variant drives the same root CLI entry point
(`jarves`, what the launcher forwards to). Each OS runs its own test; none is skipped."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMMANDS = ("dashboard", "google", "features", "setup", "update", "doctor")


def _check(result):
    assert result.returncode == 0, result.stderr
    assert "usage: jarves run" not in result.stdout
    assert all(name in result.stdout for name in COMMANDS)
    assert "already running" not in result.stdout and "How should JARVES connect?" not in result.stdout


@pytest.mark.windows_only
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_launcher_root_help_lists_commands(flag):
    _check(subprocess.run(["cmd.exe", "/d", "/c", str(REPO / "jarvis.cmd"), flag],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30))


@pytest.mark.linux_only
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_cli_root_help_lists_commands_linux(flag):
    env = {**os.environ, "PYTHONPATH": str(REPO / "core" / "src")}
    _check(subprocess.run([sys.executable, "-c", "import sys; from jarves.cli import main; sys.exit(main())", flag],
                          cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                          env=env))
