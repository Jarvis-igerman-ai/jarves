"""Regression tests for #1096 #1097 #1098 #1099 (JAR-376)."""
from __future__ import annotations

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


@pytest.mark.parametrize("text", ["secret-ish title", "plan a secret party", "I like jazz in the morning", "call me Igal", "prefer short answers",
                                  "wake me at seven on weekdays", "internationalization matters"])
def test_1099_ordinary_preferences_are_not_sensitive(text):
    assert not looks_sensitive(text)


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
            p.kill()
            p.wait(5)


def test_206_f13_privacy_keeps_no_reply_words_at_rest(tmp_path, monkeypatch):
    import sqlite3
    from jarves import logs
    from jarves.continuity import Continuity
    monkeypatch.setattr(logs, "privacy", lambda: True)
    c = Continuity(tmp_path)
    c.store("phone1", "c1", "chat", 200, {"ok": True, "reply": "your bank PIN reminder"})
    rows = [r for f in tmp_path.rglob("*") if f.is_file() and f.suffix in (".db", ".sqlite", ".sqlite3")
            for r in sqlite3.connect(f).execute("SELECT body FROM ledger").fetchall()]
    assert rows and all("bank" not in r[0] for r in rows) and '"ok": true' in rows[0][0]
    monkeypatch.setattr(logs, "privacy", lambda: False)
    c.store("phone1", "c2", "chat", 200, {"ok": True, "reply": "jazz at seven"})
    assert c.seen("phone1", "c2")["reply"] == "jazz at seven"                      # privacy off: kept for a resend


def test_206_h3_h4_guides_state_the_exposure():
    from pathlib import Path
    import jarves
    data = Path(jarves.__file__).parent / "guides" / "data"
    assert "status permission sees this strip" in (data / "mobile_access.toml").read_text(encoding="utf-8")
    assert "not in a sandbox" in (data / "project_manager.toml").read_text(encoding="utf-8")
