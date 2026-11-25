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
    assert cmd == ["icacls", str(f), "/inheritance:r", "/grant:r", "PC\\alice:(F)",
                   "/remove:g", "*S-1-3-4", "*S-1-5-18", "*S-1-5-32-544"]
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
    assert any(n.startswith("sessions.") for n in names)  # the temp file, locked before it is written


def test_existing_sessions_file_is_tightened_at_startup(monkeypatch, tmp_path):
    """JAR-824 (JAR-260 reopened): a sessions.json written before the fix kept the inherited folder ACL at
    startup; only a later login rewrote it. Loading it now applies the owner-only ACL once."""
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or True)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    assert server.load_sessions(tmp_path) == {}
    assert seen == []  # no file: nothing to tighten, no icacls warning
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    server.load_sessions(tmp_path)
    server.load_sessions(tmp_path)
    assert [p.name for p in seen] == ["sessions.json"]


def test_failed_tighten_is_retried_next_time(monkeypatch, tmp_path):
    """JAR-824 review R2: a failed tighten must not mark the file done, or it is never retried in this process."""
    results = [False, True]
    seen: list[str] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p).name) or results.pop(0))
    monkeypatch.setattr(server, "_TIGHTENED", set())
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    for _ in range(3):
        server.load_sessions(tmp_path)
    assert seen == ["sessions.json", "sessions.json"]  # failed, retried and succeeded, then done


def test_permanent_tighten_failure_is_bounded(monkeypatch, tmp_path):
    """JAR-824 review R1: a permanently failing icacls runs (and warns) at most _TIGHTEN_TRIES times per file."""
    seen: list[str] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p).name) and False)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    for _ in range(10):
        server.load_sessions(tmp_path)
    assert seen == ["sessions.json"] * server._TIGHTEN_TRIES
