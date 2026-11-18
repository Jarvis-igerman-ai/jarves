"""Regression tests for #1096 #1097 #1098 #1099 #206 (JAR-376). Hermetic: no real process is started or killed."""
from __future__ import annotations

import pytest

from jarves.preferences import looks_sensitive
from jarves.projectmgr import gitops, service
from test_projectmgr import env, open_project  # noqa: F401 - the shared project-manager fixture


# ---------------------------------------------------------------- #1096 / #1097: protected paths after normalisation
@pytest.mark.parametrize("path", ["./.github/workflows/ci.yml", "src/../.GitHub/workflows/x.yml", r".\.Claude\settings.json",
                                  "a/./../.git/config", "../outside.py", "..", "/etc/passwd", "C:/x.py", "src/../../x.py"])
def test_1096_1097_dotted_or_escaping_paths_are_protected(path):
    assert gitops.is_protected(path)


@pytest.mark.parametrize("path", ["src/a.py", "./src/a.py", "docs/../src/b.py", "src/..x/y.py"])
def test_1096_1097_ordinary_paths_stay_allowed(path):
    assert not gitops.is_protected(path)


# ---------------------------------------------------------------- #1098: a stored pid is killed only if it is still ours
@pytest.fixture
def procs(monkeypatch):
    live, killed = {}, []
    monkeypatch.setattr(service, "_started_at", lambda pid: live.get(pid))
    monkeypatch.setattr(service, "_kill_tree", killed.append)
    return live, killed


@pytest.mark.parametrize("now,stored,want", [(None, 5.0, "gone"), (6.0, 5.0, "gone"), (5.0, None, "unknown"),
                                             (5.0, "x", "unknown"), (5.0, 5.0, "killed")])
def test_1098_kill_pid_checks_identity(procs, now, stored, want):
    live, killed = procs
    if now is not None:
        live[42] = now
    assert service._kill_pid(42, stored) == want
    assert killed == ([42] if want == "killed" else [])


def _running(m, pid, job):
    tid = m.db.add_task(1, "Zombie", "FAKE write z=1", "z", ["z"], [], "running")
    m.db.update_task(tid, pid=pid, job=job)
    return tid


def test_1098_startup_recovery_never_kills_a_reused_pid(env, procs):  # noqa: F811
    m, repo, _ = env
    open_project(m, repo)
    live, killed = procs
    live[4242] = 999.0                                        # the pid now belongs to another program
    tid = _running(m, 4242, {"pid_started": 111.0})
    m._recover()
    assert killed == [] and m.db.task(tid)["status"] in ("todo", "ready")


def test_1098_startup_recovery_kills_its_own_child(env, procs):  # noqa: F811
    m, repo, _ = env
    open_project(m, repo)
    live, killed = procs
    live[4242] = 111.0
    tid = _running(m, 4242, {"pid_started": 111.0})
    m._recover()
    assert killed == [4242] and "pid_started" not in m.db.task(tid)["job"]


def test_1098_unidentified_live_pid_blocks_instead_of_killing(env, procs):  # noqa: F811
    m, repo, _ = env
    open_project(m, repo)
    live, killed = procs
    live[4242] = 111.0
    tid = _running(m, 4242, {})                               # a row from before identities were recorded
    m._recover()
    t = m.db.task(tid)
    assert killed == [] and t["status"] == "blocked" and "4242" in t["reason"] and t["pid"] == 4242


# ---------------------------------------------------------------- #1099: key/token-shaped text is never learned
@pytest.mark.parametrize("text", ["my key is sk-proj-abcdEFGH1234ijklMNOP5678", "ghp_aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW3xY5",
                                  "AKIAIOSFODNN7EXAMPLE1", "remember my api key", "the api_token is in the drawer",
                                  "use secret key from the box", "access-token please", "מפתח API שלי", "מפתחות API",
                                  "הטוקן שלי"])
def test_1099_key_and_token_shaped_text_is_sensitive(text):
    assert looks_sensitive(text)


@pytest.mark.parametrize("text", ["I like jazz in the morning", "prefer short answers", "access keyboard shortcuts",
                                  "a private keyboard", "plan a secret party", "internationalization matters",
                                  "Python3.12-compatible-code", "wake me at seven on weekdays"])
def test_1099_ordinary_preferences_are_not_sensitive(text):
    assert not looks_sensitive(text)


# ---------------------------------------------------------------- #206 F13: privacy keeps no reply words at rest
def test_206_f13_privacy_keeps_no_reply_words_at_rest(tmp_path, monkeypatch):
    from jarves import continuity, logs
    c = continuity.Continuity(tmp_path)
    monkeypatch.setattr(logs, "privacy", lambda: True)
    c.store("phone1", "c1", "chat", 200, {"ok": True, "reply": "your bank PIN reminder", "n": 2})
    assert c.seen("phone1", "c1") == {"ok": True, "reply": continuity.RESEND_UNAVAILABLE, "code": "RESEND_UNAVAILABLE",
                                      "n": 2}
    monkeypatch.setattr(logs, "privacy", lambda: False)
    c.store("phone1", "c2", "chat", 200, {"ok": True, "reply": "jazz at seven"})
    assert c.seen("phone1", "c2")["reply"] == "jazz at seven"                      # privacy off: kept for a resend


# ---------------------------------------------------------------- #206 H3 / H4: the guides state the exposure
def test_206_h3_h4_guides_state_the_exposure():
    from pathlib import Path
    import jarves
    data = Path(jarves.__file__).parent / "guides" / "data"
    assert "status permission sees this strip" in (data / "mobile_access.toml").read_text(encoding="utf-8")
    assert "not in a sandbox" in (data / "project_manager.toml").read_text(encoding="utf-8")
