"""Windows launcher flags must reach the root CLI without starting a Core, voice or setup wizard."""
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_launcher_root_help_lists_commands(flag):
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(["cmd.exe", "/d", "/c", str(repo / "jarvis.cmd"), flag],
                            cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode == 0, result.stderr
    assert "usage: jarves run" not in result.stdout
    assert all(name in result.stdout for name in ("dashboard", "google", "features", "setup", "update", "doctor"))
    assert "already running" not in result.stdout and "How should JARVES connect?" not in result.stdout
