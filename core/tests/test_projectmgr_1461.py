"""#1461: a task branch moved by a sibling after the scope check or the check never merges unchecked content."""
from __future__ import annotations

import sys
from pathlib import Path

from jarves.projectmgr import gitops

from test_projectmgr import env, finish, open_project, sh, task  # noqa: F401  (env is a fixture)

GIT = ["git", "-c", "user.name=s", "-c", "user.email=s@s"]


def _sibling_commit(wt: Path, body: str) -> None:
    """What a sibling agent can do from any worktree: point the task branch at a commit nobody checked."""
    (wt / "a.txt").write_text(body)
    sh(wt, *GIT, "add", "-A")
    sh(wt, *GIT, "commit", "-q", "-m", "sibling")


def _integration_a(home: Path) -> str:
    f = gitops.project_root(home, 1) / "integration" / "a.txt"
    return f.read_text() if f.exists() else ""


def _wrap(m, name, before):
    orig = getattr(m, name)

    def run(p, t, *a, **k):
        before(t)
        return orig(p, t, *a, **k)
    setattr(m, name, run)


def test_branch_moved_between_check_and_merge_is_not_merged(env):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    _wrap(m, "_st_merge", lambda t: _sibling_commit(Path(t["worktree"]), "never checked\n"))
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "moved after JARVES checked it" in t["reason"], t["reason"]
    assert _integration_a(home) == ""


def test_branch_moved_before_the_check_is_not_checked(env):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    _wrap(m, "_st_check", lambda t: _sibling_commit(Path(t["worktree"]), "never scope-checked\n"))
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "not checked" in t["reason"], t["reason"]
    assert _integration_a(home) == ""


def test_branch_moved_during_the_check_is_not_merged(env):
    m, repo, home = env
    move = ("import subprocess as s; open('a.txt','w').write('during\\n'); "
            "g=['git','-c','user.name=s','-c','user.email=s@s']; s.run(g+['add','-A'],check=True); "
            "s.run(g+['commit','-q','-m','x'],check=True)")
    open_project(m, repo, check=[sys.executable, "-c", move])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "not merged" in t["reason"], t["reason"]
    assert _integration_a(home) == ""


def test_unmoved_branch_still_merges(env):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    m.call("resume", project="demo")
    finish(m)
    assert m.db.by_seq(1, 1)["status"] == "done"
    assert _integration_a(home) == "1\n"


def test_owner_merge_approval_does_not_cover_a_sibling_move(env):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    m.call("add_task", project="demo", title="A", spec="FAKE write a.txt=1", acceptance="x", scope=["a.txt"],
           flow="work test merge:owner")
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "waiting" and "approve merging" in t["waiting"]
    _sibling_commit(Path(t["worktree"]), "slipped in\n")
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "moved after JARVES checked it" in t["reason"], t["reason"]
    assert _integration_a(home) == ""
