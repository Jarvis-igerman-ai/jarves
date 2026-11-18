"""Regression tests for #1096 #1097 #1098 #1099 (JAR-376)."""
from __future__ import annotations

import gc
import subprocess
import sys

import pytest

from jarves.preferences import looks_sensitive
from jarves.projectmgr import gitops


def test_1096_github_workflows_are_protected():
    prot, _ = gitops.violations([".github/workflows/ci.yml", "src/a.py"], ["src/"])
    assert prot == [".github/workflows/ci.yml"]


@pytest.mark.parametrize("path", [".GitHub/workflows/x.yml", ".Claude/settings.json", ".GIT/config", "sub/OpenCode.JSON",
                                  ".githooks\\pre-commit", "./.mcp.json", "./.github/workflows/x.yml",
                                  "src/../.github/workflows/x.yml", "../.github/workflows/x.yml", "..", "/etc/passwd",
                                  "C:/x.py", "src/../../x.py"])
def test_1097_protected_check_ignores_case_and_separators(path):
    prot, _ = gitops.violations([path], ["*"])
    assert prot == [path]


def test_1097_ordinary_paths_are_not_protected():
    assert gitops.violations(["src/github.py", "docs/claude.md"], ["src/", "docs/"]) == ([], [])


@pytest.mark.parametrize("text", ["my key is sk-proj-abcdEFGH1234ijklMNOP5678", "token ghp_aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5",
                                  "api key: AKIAIOSFODNN7EXAMPLE", "use bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc",
                                  "remember my api_key", "the secret is hunter2", "מפתח ה-API שלי",
                                  "מפתח הגישה הוא abc", "המפתח: hunter2", "Xk9vQ2mP7rT4wZ8nB3cF6hJ1sL5dG0aY"])
def test_1099_key_and_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", ["secret-ish title", "plan a secret party", "I like jazz in the morning", "call me Igal", "prefer short answers",
                                  "wake me at seven on weekdays", "internationalization matters",
                                  "אני מפתח תוכנה", "תזכיר לי איפה מפתחות הבית", "I like board games with tokens",
                                  "my uncle was a pallbearer",
                                  "access keyboard shortcuts", "a private keyboard"])
def test_1099_ordinary_preferences_are_not_sensitive(text):
    assert not looks_sensitive(text)


def test_1098_recovery_kills_only_the_same_process():
    from jarves.projectmgr import engine
    from jarves.projectmgr.service import _kill_pid
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        tok = engine.proc_token(p.pid)
        assert tok
        assert _kill_pid(p.pid, "999999") == "reused"     # a recycled pid (different start) is left alone
        assert _kill_pid(p.pid, "") == "unknown"          # no stored identity: never kill blind
        assert p.poll() is None
        assert _kill_pid(p.pid, tok) == "killed"          # the same process: stopped
        assert p.wait(5) is not None
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(5)


def test_206_f13_privacy_keeps_no_reply_words_at_rest(tmp_path, monkeypatch):
    import sqlite3
    from jarves import logs
    from jarves.continuity import RESEND_UNAVAILABLE, Continuity
    monkeypatch.setattr(logs, "privacy", lambda: True)
    c = Continuity(tmp_path)
    c.store("phone1", "c1", "chat", 200, {"ok": True, "reply": "your bank PIN reminder"})
    c.execute("phone1", "c3", "goal", lambda: (200, {"ok": True, "text": "goal kept"}))   # not chat: unchanged
    rows = [r for f in tmp_path.rglob("*") if f.is_file() and f.suffix in (".db", ".sqlite", ".sqlite3")
            for r in sqlite3.connect(f).execute("SELECT body FROM ledger").fetchall()]
    assert rows and all("bank" not in r[0] for r in rows)
    assert c.seen("phone1", "c1") == {"ok": True, "reply": RESEND_UNAVAILABLE, "code": "RESEND_UNAVAILABLE"}
    assert c.seen("phone1", "c3")["text"] == "goal kept"
    monkeypatch.setattr(logs, "privacy", lambda: False)
    c.store("phone1", "c2", "chat", 200, {"ok": True, "reply": "jazz at seven"})
    assert c.seen("phone1", "c2")["reply"] == "jazz at seven"                      # privacy off: kept for a resend


def test_206_f13_privacy_resend_is_served_and_explicit(tmp_path, monkeypatch):
    """The phone's resend path (MobileAccess.replay): the in-memory copy serves the words; after a PC restart the
    ledger answers with an explicit RESEND_UNAVAILABLE reply, never an empty ok."""
    from jarves import logs
    from jarves.continuity import RESEND_UNAVAILABLE
    from jarves.mobile import MobileAccess
    monkeypatch.setattr(logs, "privacy", lambda: True)
    a = MobileAccess(tmp_path)
    a.remember("dev-a", "msg-id-0001", {"reply": "lights on", "ok": True})
    assert a.replay("dev-a", "msg-id-0001") == {"reply": "lights on", "ok": True}
    again = MobileAccess(tmp_path).replay("dev-a", "msg-id-0001")                 # restarted: memory gone
    assert again["reply"] == RESEND_UNAVAILABLE and again["code"] == "RESEND_UNAVAILABLE"


@pytest.fixture
def pm(tmp_path):
    from jarves.config import Config
    from jarves.projectmgr.service import Manager
    repo = tmp_path / "repo"
    repo.mkdir()
    for a in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=repo, check=True, capture_output=True)
    m = Manager(tmp_path / "home", Config(), ask=lambda *a, **k: True)
    assert m.call("open", path=str(repo), name="demo", goal="g")["ok"]
    yield m
    m.stop()


def _child():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)


def _running_task(m, pid, token):
    """What `on_start` stores for a started agent: the pid on the row and its identity in the job (via _set_job)."""
    tid = m.db.add_task(1, "T", "s", "a", ["a"], [], "running")
    m.db.update_task(tid, pid=pid)
    if token is not None:
        m._set_job(m.db.task(tid), pid_token=token)
    return tid


def _reap(p):
    if p.poll() is None:
        p.kill()
    p.wait(5)


def test_1098_recover_kills_the_recorded_child_and_spares_a_mismatch(pm):
    from jarves.projectmgr import engine
    same, other = _child(), _child()
    try:
        t1 = _running_task(pm, same.pid, engine.proc_token(same.pid))
        t2 = _running_task(pm, other.pid, "999999")                                # pid reused by another program
        pm._recover()
        assert same.wait(5) is not None and other.poll() is None
        assert pm.db.task(t1)["status"] in ("todo", "ready") and pm.db.task(t2)["status"] in ("todo", "ready")
    finally:
        _reap(same), _reap(other)


def test_1098_legacy_row_without_identity_is_blocked_not_resumed(pm):
    p = _child()
    try:
        tid = _running_task(pm, p.pid, None)                                       # started before this change
        pm._recover()
        t = pm.db.task(tid)
        assert p.poll() is None and t["status"] == "blocked" and "could not be identified" in t["reason"]
    finally:
        _reap(p)


def test_1098_stop_without_identity_does_not_claim_stopped(pm):
    from jarves.projectmgr import engine
    lost, known = _child(), _child()
    try:
        lost_t = _running_task(pm, lost.pid, "")                                   # proc_token could not read it
        _running_task(pm, known.pid, engine.proc_token(known.pid))
        r = pm.call("stop_agent", project="demo")
        assert r["ok"] is False and r["code"] == "NOT_STOPPED" and len(r["stopped"]) == 1
        assert "not stopped" in r["text"].lower() and str(lost.pid) in r["text"]
        assert known.wait(5) is not None and lost.poll() is None
        assert "stop_unverified" in [e["kind"] for e in pm.db.events(lost_t)]
    finally:
        _reap(lost), _reap(known)


def test_206_h3_h4_guides_state_the_exposure():
    from pathlib import Path
    import jarves
    data = Path(jarves.__file__).parent / "guides" / "data"
    assert "status permission sees this strip" in (data / "mobile_access.toml").read_text(encoding="utf-8")
    assert "not in a sandbox" in (data / "project_manager.toml").read_text(encoding="utf-8")


def test_206_h2_phone_edit_reports_the_stored_state():
    """mobile_prefs edit: the state comes from the store after the edit (not assumed), and a row that vanished or a
    malformed store answer is a 404, not an AttributeError/500."""
    from types import SimpleNamespace
    from jarves import mobile_prefs

    class P:
        def __init__(self, after):
            self.after, self.calls = after, 0

        def get(self, pid):
            self.calls += 1
            return {"id": pid, "state": "confirmed"} if self.calls == 1 else self.after

        def edit(self, pid, text, *, by, repropose):
            assert repropose is True
            return None

    for after, want in (({"id": 1, "state": "proposed"}, (200, "proposed")), (None, (404, None)), ("x", (404, None))):
        mob = SimpleNamespace(bridge=None, _prefs=P(after))
        st, body = mobile_prefs.action(mob, {"op": "edit", "pref": 1, "text": "short answers"}, "dev")
        assert (st, body.get("state")) == want


def test_1098_windows_taskkill_failure_is_not_stopped(pm, monkeypatch):
    """taskkill exits non-zero (access denied) while the same process still runs: Stop must not claim it stopped."""
    from jarves.projectmgr import engine, service
    calls = []
    monkeypatch.setattr(service, "_WINDOWS", True)
    monkeypatch.setattr(engine, "proc_token", lambda pid: "T1")
    monkeypatch.setattr(service, "_run", lambda argv, **k: calls.append(argv) or
                        subprocess.CompletedProcess(argv, 1, "", "Access is denied."))
    tid = _running_task(pm, 4242, "T1")
    r = pm.call("stop_agent", project="demo")
    assert calls and calls[0][0] == "taskkill"
    assert r["ok"] is False and r["code"] == "NOT_STOPPED" and r["stopped"] == []
    assert "stop_unverified" in [e["kind"] for e in pm.db.events(tid)]
    seq = iter(["T1", ""])
    monkeypatch.setattr(engine, "proc_token", lambda pid: next(seq))              # taskkill failed, but it is gone now
    assert service._kill_pid(4242, "T1") == "gone"


def test_1098_unreadable_identity_is_unknown_not_gone(monkeypatch):
    from jarves.projectmgr import engine, service
    monkeypatch.setattr(engine, "proc_token", lambda pid: engine.UNREADABLE)
    assert service._kill_pid(4242, "T1") == "unknown"
    assert service._kill_pid(4242, engine.UNREADABLE) == "unknown"


def test_1098_proc_token_of_a_missing_pid_is_gone():
    from jarves.projectmgr import engine
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(10)
    pid = p.pid
    del p                                                                         # Windows: release the process handle,
    gc.collect()                                                                  # else the exited process object lives on
    assert engine.proc_token(pid) == ""                                         # reaped: gone, not UNREADABLE


def test_1098_proc_token_reaped_during_read_is_gone(monkeypatch):
    """Linux: /proc/<pid>/stat can raise ESRCH (ProcessLookupError) if the process is reaped mid-read: gone, not UNREADABLE."""
    from pathlib import Path
    from jarves.projectmgr import engine
    if engine.os.name == "nt":
        pytest.skip("the /proc path is not used on Windows")
    real = Path.read_text
    monkeypatch.setattr(engine.Path, "exists", lambda self: True)
    monkeypatch.setattr(engine.Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(ProcessLookupError())
                        if self.name == "stat" and "self" not in self.parts else real(self, *a, **k))
    assert engine.proc_token(4242) == ""


def test_1098_proc_token_bad_ctypes_argument_is_unreadable(monkeypatch):
    """A ctypes.ArgumentError while reading identity must not escape into the stage start: it is UNREADABLE."""
    import ctypes
    from jarves.projectmgr import engine
    def boom(self):
        raise ctypes.ArgumentError("bad handle")
    monkeypatch.setattr(engine.os, "name", "posix")
    monkeypatch.setattr(engine.Path, "exists", boom)
    assert engine.proc_token(4242) == engine.UNREADABLE
