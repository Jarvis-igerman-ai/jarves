"""#824: secret-file ACL command contract (real ACL coverage in JAR-406 regression)."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from jarves.workflow import server


def _run_recorder(calls, rc=0):
    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, rc, b"", b"")
    return fake


def test_acl_argv_owner_only(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setenv("PSModulePath", r"C:\pwsh7\Modules")
    monkeypatch.setattr(server.subprocess, "run", _run_recorder(calls))
    f = tmp_path / "access.token"
    assert server.owner_only_file(f) is True
    cmd, kw = calls[0]
    assert cmd[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert "FileSecurity]::new()" in cmd[4]
    assert "SetAccessRuleProtection($true,$false)" in cmd[4]
    assert "WindowsIdentity]::GetCurrent().User" in cmd[4]
    assert kw["env"]["JARVES_SECRET_ACL_PATH"] == str(f)
    assert str(f) not in cmd[4]
    assert "PSModulePath" not in kw["env"]
    assert not kw.get("shell")


def test_fail_safe_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(server.os, "name", "nt")

    def boom(*a, **k):
        raise FileNotFoundError("powershell.exe")
    monkeypatch.setattr(server.subprocess, "run", boom)
    assert server.owner_only_file(tmp_path / "x") is False


def test_nonzero_exit_fails_and_warns_once(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(server, "_ACL_WARNED", set())
    monkeypatch.setattr(server.subprocess, "run", _run_recorder([], rc=1))
    with caplog.at_level(logging.DEBUG, logger=server.__name__):
        assert server.owner_only_file(tmp_path / "sessions.a") is False
        assert server.owner_only_file(tmp_path / "sessions.b") is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ["could not set an owner-only ACL on sessions.a"]
    assert any(r.levelno == logging.DEBUG for r in caplog.records)  # later failures still recorded


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
    assert any(n.startswith("sessions.") for n in names)  # the temp file, locked before it is written
