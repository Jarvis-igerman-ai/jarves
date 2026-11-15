"""JAR-401 regressions for the residual gaps of #824 #855 #858 #879 #903 (temp homes only, no real ACLs)."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from jarves.workflow import server, unlock
from jarves.workflow.control import exclusive
from jarves.workflow.store import Store


def test_824_failed_icacls_is_logged_not_silent(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(server, "_ACL_WARNED", set())  # first failure in this process warns
    monkeypatch.setattr(server.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 5, b"", b"denied"))
    with caplog.at_level(logging.WARNING, logger=server.__name__):
        assert server.owner_only_file(tmp_path / "access.token") is False
    assert "owner-only ACL" in caplog.text


def test_824_secrets_are_locked_down_before_they_are_written(monkeypatch, tmp_path):
    sizes: dict[str, int] = {}
    monkeypatch.setattr(server, "_TIGHTENED", set())
    monkeypatch.setattr(server, "owner_only_file",
                        lambda p: sizes.setdefault(Path(p).name.split(".")[0], Path(p).stat().st_size) is not None)
    server.access_token(tmp_path)
    server.save_sessions(tmp_path, {})
    assert sizes == {"access": 0, "sessions": 0}  # no window where the secret exists with the inherited ACL


def test_855_owner_closed_task_is_not_ready_or_dispatched(tmp_path):
    store = Store(tmp_path / "workflow.db")
    try:
        store.create_task({"id": "closed", "title": "Issue closed by owner"})
        store.update_metadata("closed", {"source_closed": True})
        store.register_agent({"id": "w"})
        assert [t["ready"] for t in store.snapshot()["tasks"]] == [False]
        assert store.claim("w") is None
    finally:
        store.close()


def test_858_monitor_notes_do_not_spend_the_ready_work_budget(tmp_path):
    store = Store(tmp_path / "workflow.db")
    try:
        for i in range(2):  # at the worker limit the strict 2/hour cap applies
            store.post_manager_note("watch", f"note {i}")
            assert store.take_manager_notes_capped("m", worker_limit=0)
        store.create_task({"id": "ok", "title": "Runnable"})
        assert store.manager_continue("m", worker_limit=0) == ["ok"]
    finally:
        store.close()


def test_879_failed_unlock_does_not_escape_the_lock(monkeypatch, tmp_path):
    # The platform's own lock module: msvcrt on Windows, fcntl elsewhere (no skips on either).
    if os.name == "nt":
        import msvcrt as mod
        name, unlock_op = "locking", mod.LK_UNLCK   # locking(fd, mode, nbytes)
    else:
        import fcntl as mod
        name, unlock_op = "flock", mod.LOCK_UN      # flock(fd, op)
    real = getattr(mod, name)

    def fake(*args):
        if args[1] == unlock_op:
            raise PermissionError(13, "Permission denied")
        return real(*args)
    monkeypatch.setattr(mod, name, fake)
    with exclusive(tmp_path / "supervisor.lock"):
        pass  # closing the handle releases the kernel lock anyway; the supervisor must still stop cleanly
    monkeypatch.setattr(mod, name, real)
    with exclusive(tmp_path / "supervisor.lock"):
        pass


@pytest.fixture
def web(tmp_path):
    srv = server.Server(tmp_path, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    thread.join(5)
    srv.server_close()


def test_903_trace_detail_opt_in_needs_the_unlock(web):
    def post(path, data, cookie=""):
        req = urllib.request.Request(web.origin + path, data=json.dumps(data).encode(), headers={
            "Cookie": cookie, "Origin": web.origin, "Content-Type": "application/json", "X-Workflow-Request": "1"})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, response.headers
    _, headers = post("/api/session", {"token": web.token})
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    assert post("/api/trace/settings", {"mode": "details"}, cookie)[0] == 403
    assert post("/api/unlock", {"code": unlock.issue(web.home)}, cookie)[0] == 200
    assert post("/api/trace/settings", {"mode": "details"}, cookie)[0] == 200
