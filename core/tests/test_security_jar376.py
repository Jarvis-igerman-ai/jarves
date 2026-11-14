"""Regression tests for #1096 #1097 #1098 #1099 (JAR-376)."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from jarves.preferences import looks_sensitive
from jarves.projectmgr import gitops


def test_1096_github_workflows_are_protected():
    prot, _ = gitops.violations([".github/workflows/ci.yml", "src/a.py"], ["src/"])
    assert prot == [".github/workflows/ci.yml"]


@pytest.mark.parametrize("path", [".GitHub/workflows/x.yml", ".Claude/settings.json", ".GIT/config", "sub/OpenCode.JSON",
                                  ".githooks\\pre-commit", "./.mcp.json"])
def test_1097_protected_check_ignores_case_and_separators(path):
    prot, _ = gitops.violations([path], ["*"])
    assert prot == [path]


def test_1097_ordinary_paths_are_not_protected():
    assert gitops.violations(["src/github.py", "docs/claude.md"], ["src/", "docs/"]) == ([], [])


@pytest.mark.parametrize("text", ["my key is sk-proj-abcdEFGH1234ijklMNOP5678", "token ghp_aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5",
                                  "api key: AKIAIOSFODNN7EXAMPLE", "use bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc",
                                  "remember my api_key", "the secret is hunter2", "Xk9vQ2mP7rT4wZ8nB3cF6hJ1sL5dG0aY"])
def test_1099_key_and_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", ["I like jazz in the morning", "call me Igal", "prefer short answers",
                                  "wake me at seven on weekdays", "internationalization matters"])
def test_1099_ordinary_preferences_are_not_sensitive(text):
    assert not looks_sensitive(text)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses /proc and process groups")
def test_1098_recovery_kills_only_the_same_process(tmp_path):
    from jarves.projectmgr import engine
    from jarves.projectmgr.service import _kill_pid
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        tok = engine.proc_token(p.pid)
        assert tok
        _kill_pid(p.pid, "999999")                        # a recycled pid (different start) is left alone
        _kill_pid(p.pid, "")                              # no stored identity: never kill blind
        time.sleep(0.2)
        assert p.poll() is None
        _kill_pid(p.pid, tok)                             # the same process: stopped
        assert p.wait(5) is not None
    finally:
        if p.poll() is None:
            os.killpg(p.pid, 9)
            p.wait(5)
