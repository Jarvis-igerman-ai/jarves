"""#824: access.token / sessions.json get an owner-only ACL on Windows (icacls mocked; no real ACL changes)."""
from __future__ import annotations

import json
import logging
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
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
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
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
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
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    for _ in range(3):
        server.load_sessions(tmp_path)
    assert seen == ["sessions.json", "sessions.json"]  # failed, retried and succeeded, then done


def test_permanent_tighten_failure_is_bounded(monkeypatch, tmp_path, caplog):
    """JAR-824 review R1: a permanently failing icacls runs (and warns) at most _TIGHTEN_TRIES times per file."""
    seen: list[str] = []

    def failing(p) -> bool:
        seen.append(Path(p).name)
        return False

    monkeypatch.setattr(server, "owner_only_file", failing)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        for _ in range(10):
            server.load_sessions(tmp_path)
    assert seen == ["sessions.json"] * server._TIGHTEN_TRIES
    # JAR-824 review R2: the exhausted state is logged exactly once, not on every later load
    gave_up = [r for r in caplog.records if "gave up" in r.getMessage()]
    assert len(gave_up) == 1 and gave_up[0].levelno == logging.ERROR


def test_non_windows_load_marks_done_without_error(monkeypatch, tmp_path, caplog):
    """JAR-824 review R1 (#1761): off Windows the ACL step is a no-op that counts as done, so repeated loads
    never run icacls, never consume retries and never log a false "gave up" error."""
    calls = []
    monkeypatch.setattr(server.os, "name", "posix")
    monkeypatch.setattr(server.subprocess, "run", _run_recorder(calls))
    monkeypatch.setattr(server, "_TIGHTENED", set())
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=server.__name__):
        for _ in range(5):
            server.load_sessions(tmp_path)
    assert calls == []
    assert str(tmp_path / "sessions.json") in server._TIGHTENED
    assert caplog.records == []


def test_concurrent_loads_never_exceed_the_cap(monkeypatch, tmp_path):
    """JAR-824 review R2 (#1761): an attempt is reserved before icacls runs, so threads that pass the check
    together cannot run icacls more than _TIGHTEN_TRIES times."""
    import threading
    gate = threading.Barrier(6, timeout=5)
    inside = threading.Barrier(server._TIGHTEN_TRIES, timeout=5)  # hold the first attempts in icacls together
    seen: list[str] = []

    def slow_fail(p) -> bool:
        seen.append(Path(p).name)
        try:
            inside.wait()
        except threading.BrokenBarrierError:
            pass
        return False

    monkeypatch.setattr(server, "owner_only_file", slow_fail)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
    path = tmp_path / "sessions.json"
    path.write_text('{"version": 1, "sessions": {}}', encoding="utf-8")

    def worker() -> None:
        gate.wait()
        server._tighten_once(path)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(seen) == server._TIGHTEN_TRIES


def test_no_gave_up_error_when_a_concurrent_attempt_succeeded(monkeypatch, tmp_path, caplog):
    """JAR-824 review #1761 @54ef83f R1: the last attempt fails while an earlier reserved attempt succeeded;
    the file is owner-only, so no "gave up" error is logged."""
    path = tmp_path / "sessions.json"
    path.write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    key = str(path)
    tightened: set[str] = set()

    def other_thread_wins_then_fail(p) -> bool:
        tightened.add(key)  # the concurrent attempt finished first and marked the file done
        return False

    monkeypatch.setattr(server, "owner_only_file", other_thread_wins_then_fail)
    monkeypatch.setattr(server, "_TIGHTENED", tightened)
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {key: server._TIGHTEN_TRIES - 1})
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        server._tighten_once(path)
    assert [r for r in caplog.records if "gave up" in r.getMessage()] == []


def test_load_sessions_survives_icacls_raising(monkeypatch, tmp_path):
    """JAR-824 review #1761 @54ef83f R2: the tighten runs before the read's try; an icacls OSError must not
    break startup, and the stored sessions are still read."""
    import os
    import types
    # Windows only inside server (pathlib on this host keeps its own os.name, so tmp_path stays usable)
    monkeypatch.setattr(server, "os", types.SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setenv("USERNAME", "alice")

    def boom(*a, **k):
        raise FileNotFoundError("icacls")

    monkeypatch.setattr(server.subprocess, "run", boom)
    monkeypatch.setattr(server, "_TIGHTENED", set())
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
    digest = "a" * 64
    (tmp_path / "sessions.json").write_text(json.dumps({"version": 1, "sessions": {digest: 1060.0}}),
                                            encoding="utf-8")
    assert server.load_sessions(tmp_path, now=1000.0) == {digest: 1060.0}
