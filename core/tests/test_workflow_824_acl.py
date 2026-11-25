"""#824: access.token / sessions.json get an owner-only ACL on Windows (icacls mocked; no real ACL changes)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import types
from pathlib import Path

from jarves.workflow import server


def _fake_os(monkeypatch, name: str) -> None:
    """Pretend another OS inside server only; patching os.name itself would leak to pathlib and the process
    (review #1761 @6027ee70 R4)."""
    monkeypatch.setattr(server, "os", types.SimpleNamespace(name=name, environ=os.environ))


def _reset(monkeypatch) -> None:
    monkeypatch.setattr(server, "_TIGHTENED", set())
    monkeypatch.setattr(server, "_TIGHTEN_FAILS", {})
    monkeypatch.setattr(server, "_TIGHTEN_INFLIGHT", {})


def _run_recorder(calls, rc=0):
    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, rc, b"", b"")
    return fake


def test_icacls_argv_owner_only(monkeypatch, tmp_path):
    calls = []
    _fake_os(monkeypatch, "nt")
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
    _fake_os(monkeypatch, "nt")
    monkeypatch.setenv("USERNAME", "alice")

    def boom(*a, **k):
        raise FileNotFoundError("icacls")
    monkeypatch.setattr(server.subprocess, "run", boom)
    assert server.owner_only_file(tmp_path / "x") is False
    monkeypatch.delenv("USERNAME")
    assert server.owner_only_file(tmp_path / "x") is False


def test_noop_off_windows(monkeypatch, tmp_path):
    calls = []
    _fake_os(monkeypatch, "posix")
    monkeypatch.setattr(server.subprocess, "run", _run_recorder(calls))
    assert server.owner_only_file(tmp_path / "x") is True
    assert calls == []


def test_token_and_sessions_call_acl(monkeypatch, tmp_path):
    seen: list[Path] = []
    monkeypatch.setattr(server, "owner_only_file", lambda p: seen.append(Path(p)) or True)
    _reset(monkeypatch)
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
    _reset(monkeypatch)
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
    _reset(monkeypatch)
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
    _reset(monkeypatch)
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
    _fake_os(monkeypatch, "posix")
    monkeypatch.setattr(server.subprocess, "run", _run_recorder(calls))
    _reset(monkeypatch)
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
    _reset(monkeypatch)
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
    _fake_os(monkeypatch, "nt")  # Windows only inside server; pathlib keeps this host's os.name
    monkeypatch.setenv("USERNAME", "alice")

    def boom(*a, **k):
        raise FileNotFoundError("icacls")

    monkeypatch.setattr(server.subprocess, "run", boom)
    _reset(monkeypatch)
    digest = "a" * 64
    (tmp_path / "sessions.json").write_text(json.dumps({"version": 1, "sessions": {digest: 1060.0}}),
                                            encoding="utf-8")
    assert server.load_sessions(tmp_path, now=1000.0) == {digest: 1060.0}


def test_no_gave_up_error_while_an_earlier_attempt_is_still_running(monkeypatch, tmp_path, caplog):
    """Review #1761 @6027ee70 R2: attempt 3 fails while attempt 1 is still in icacls and then succeeds.
    The file ends owner-only, so no "gave up" error may be logged."""
    _reset(monkeypatch)
    path = tmp_path / "sessions.json"
    path.write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    first_in, release_first = threading.Event(), threading.Event()
    calls: list[int] = []

    def fake(p) -> bool:
        calls.append(1)
        if len(calls) == 1:  # attempt 1: held inside icacls, succeeds last
            first_in.set()
            assert release_first.wait(5)
            return True
        return False  # attempts 2 and 3 fail at once

    monkeypatch.setattr(server, "owner_only_file", fake)
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        t = threading.Thread(target=server._tighten_once, args=(path,))
        t.start()
        assert first_in.wait(5)
        server._tighten_once(path)
        server._tighten_once(path)  # attempt 3 (the cap) fails while attempt 1 is outstanding
        release_first.set()
        t.join(5)
    assert len(calls) == server._TIGHTEN_TRIES
    assert str(path) in server._TIGHTENED
    assert [r for r in caplog.records if "gave up" in r.getMessage()] == []


def test_all_concurrent_attempts_failing_log_one_error(monkeypatch, tmp_path, caplog):
    """Review #1761 @6027ee70 R2: with every reserved attempt failing, only the last to return logs, once."""
    _reset(monkeypatch)
    path = tmp_path / "sessions.json"
    path.write_text('{"version": 1, "sessions": {}}', encoding="utf-8")
    inside = threading.Barrier(server._TIGHTEN_TRIES, timeout=5)

    def fail_together(p) -> bool:
        inside.wait()
        return False

    monkeypatch.setattr(server, "owner_only_file", fail_together)
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        threads = [threading.Thread(target=server._tighten_once, args=(path,)) for _ in range(server._TIGHTEN_TRIES)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
    assert len([r for r in caplog.records if "gave up" in r.getMessage()]) == 1


def test_load_sessions_survives_is_file_permission_error(monkeypatch, tmp_path):
    """Review #1761 @6027ee70 R3: on Windows Path.is_file re-raises PermissionError from stat; a file whose
    ACL denies stat must not break Server.__init__ (load_sessions returns no stored sessions)."""
    _reset(monkeypatch)
    (tmp_path / "sessions.json").write_text('{"version": 1, "sessions": {}}', encoding="utf-8")

    def denied(self) -> bool:
        raise PermissionError(13, "Access is denied", str(self))

    monkeypatch.setattr(server.Path, "is_file", denied)
    assert server.load_sessions(tmp_path) == {}
