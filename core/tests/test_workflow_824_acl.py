"""#824: access.token / sessions.json get an owner-only ACL on Windows (icacls mocked; no real ACL changes)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from jarves.workflow import server


def _run_recorder(calls, rc=0):
    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, rc, b"", b"")
    return fake


def test_icacls_argv_owner_only(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setenv("USERNAME", "alice")
    monkeypatch.setenv("USERDOMAIN", "PC")
    monkeypatch.setattr(server.subprocess, "run", _run_recorder(calls))
    f = tmp_path / "access.token"
    assert server.owner_only_file(f) is True
    cmd, kw = calls[0]
    assert cmd == ["icacls", str(f), "/inheritance:r", "/grant:r", "PC\\alice:(F)"]
    assert not kw.get("shell")


def test_fail_safe_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setenv("USERNAME", "alice")

    def boom(*a, **k):
        raise FileNotFoundError("icacls")
    monkeypatch.setattr(server.subprocess, "run", boom)
    assert server.owner_only_file(tmp_path / "x") is False
    monkeypatch.delenv("USERNAME")
    assert server.owner_only_file(tmp_path / "x") is False


def test_noop_off_windows(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(server.os, "name", "posix")
    monkeypatch.setattr(server.subprocess, "run", _run_recorder(calls))
    assert server.owner_only_file(tmp_path / "x") is True
    assert calls == []


def test_token_and_sessions_call_acl(monkeypatch, tmp_path):
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or True)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    tok = server.access_token(tmp_path)
    assert server.access_token(tmp_path) == tok
    server.save_sessions(tmp_path, {})
    names = [p.name for p in seen]
    assert names.count("access.token") == 2  # created, then existing file tightened once
    assert "sessions.json" in names
