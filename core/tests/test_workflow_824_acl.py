"""#824: access.token / sessions.json get an owner-only ACL on Windows (icacls mocked; no real ACL changes)."""
from __future__ import annotations

import os
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
    """JAR-260 reopened: an upgraded install's sessions.json was only read, so it kept the folder ACL."""
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or True)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    assert server.load_sessions(tmp_path) == {}
    server.load_sessions(tmp_path)
    assert seen == [tmp_path / "sessions.json"]  # once per process, the real file (not a temp copy)


def test_missing_sessions_file_is_not_tightened(monkeypatch, tmp_path):
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or True)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    assert server.load_sessions(tmp_path) == {}
    assert seen == []


def test_failed_tighten_is_not_retried_until_next_start(monkeypatch, tmp_path):
    """Review R2 (JAR-835): once per process per path; a failure waits for the next process start."""
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or False)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    server.load_sessions(tmp_path)
    server.load_sessions(tmp_path)
    assert seen == [tmp_path / "sessions.json"]
    monkeypatch.setattr(server, "_TIGHTENED", set())  # a new process
    server.load_sessions(tmp_path)
    assert seen == [tmp_path / "sessions.json"] * 2


def test_unreadable_sessions_file_is_still_tightened(monkeypatch, tmp_path):
    """Review R2 (JAR-835): a file that exists but cannot be read is tightened before the read fails."""
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or True)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    (tmp_path / "sessions.json").write_text("{}", encoding="utf-8")

    def locked(self, *a, **k):
        raise PermissionError(13, "locked", str(self))
    monkeypatch.setattr(Path, "read_text", locked)
    assert server.load_sessions(tmp_path) == {}
    assert seen == [tmp_path / "sessions.json"]


def test_relative_and_absolute_path_share_one_key(monkeypatch, tmp_path):
    """Review R1 (JAR-835): the same file through two spellings is tightened once per process."""
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or True)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    (tmp_path / "sessions.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    server.load_sessions(Path("."))
    # The absolute spelling comes from the OS (not tmp_path), so a short-name or symlinked temp folder
    # cannot give a different string for the same folder (#1778 review R2).
    server.load_sessions(Path(os.getcwd()))
    assert len(seen) == 1
