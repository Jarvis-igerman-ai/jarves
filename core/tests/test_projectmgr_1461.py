"""#1461: all task worktrees share refs, so a sibling can move a task branch after JARVES checked it. The merge stage
must merge only the task commit the check saw, never whatever the branch points at later."""
from __future__ import annotations

import sys
from pathlib import Path

from test_projectmgr import env, finish, open_project, sh, task  # noqa: F401  (fixture + helpers)

from jarves.projectmgr import engine, gitops

GIT = ("git", "-c", "user.name=s", "-c", "user.email=s@s")
COMMIT_EVIL = ("import subprocess as s; open('a.txt','w').write('evil'); "
               f"s.run({list(GIT)!r} + ['commit', '-qam', 'evil', '--no-verify'], check=True)")


def _sibling_commit(wt) -> None:
    (Path(wt) / "a.txt").write_text("evil")
    sh(wt, *GIT, "commit", "-qam", "sibling", "--no-verify")


def _after_check(m, then) -> None:
    orig = m._st_check

    def wrapped(p, t, st, cancel):
        out = orig(p, t, st, cancel)
        then(m.db.task(t["id"]))
        return out
    m._st_check = wrapped


def _start(m, repo, check):
    open_project(m, repo, check=check)
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    m.call("resume", project="demo")


def _integ(home) -> Path:
    return gitops.project_root(home, 1) / "integration"


def _blocked(m, home, words):
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and words in t["reason"], (t["status"], t["reason"])
    assert not (_integ(home) / "a.txt").exists()                       # nothing reached the integration branch
    return t


def test_branch_moved_between_check_and_merge_is_not_merged(env):
    m, repo, home = env
    _after_check(m, lambda t: _sibling_commit(t["worktree"]))
    _start(m, repo, [sys.executable, "-c", "pass"])
    finish(m)
    _blocked(m, home, "moved after JARVES checked it")


def test_retry_after_a_moved_branch_runs_the_work_and_check_again(env):
    m, repo, home = env
    moves = []
    _after_check(m, lambda t: moves or moves.append(_sibling_commit(t["worktree"])))     # only the first time
    _start(m, repo, [sys.executable, "-c", "pass"])
    finish(m)
    t = _blocked(m, home, "moved after JARVES checked it")
    assert t["stage"] == 0                                            # back to the work stage, not stuck at merge
    assert m.call("retry_task", project="demo", task=1)["ok"]
    finish(m)
    assert m.db.by_seq(1, 1)["status"] == "done"
    assert (_integ(home) / "a.txt").read_text() == "1\n"              # the content the second check saw


def test_branch_moved_while_the_check_runs_is_not_merged(env):
    m, repo, home = env
    _start(m, repo, [sys.executable, "-c", COMMIT_EVIL])               # the check's cwd is the task folder
    finish(m)
    _blocked(m, home, "moved while JARVES checked it")


def test_branch_moved_during_the_merge_stage_recheck_is_not_merged(env):
    m, repo, home = env
    flag = home.parent / "recheck"
    check = [sys.executable, "-c", f"import os\nif os.path.exists(r'{flag}'):\n    " + COMMIT_EVIL.replace("; ", "\n    ")]

    def newer_work_lands(t):                                           # the integration branch moves after the check
        flag.write_text("x")
        (_integ(home) / "other.txt").write_text("o")
        sh(_integ(home), *GIT, "add", "-A")
        sh(_integ(home), *GIT, "commit", "-qm", "other", "--no-verify")
    _after_check(m, newer_work_lands)
    _start(m, repo, check)
    finish(m)
    _blocked(m, home, "moved while JARVES checked it again")


def test_branch_moved_while_the_merge_commits_is_not_merged(env, monkeypatch):
    m, repo, home = env
    armed, real = [], gitops.commit_all

    def commit_all(wt, msg):
        made = real(wt, msg)
        if armed and armed.pop():
            _sibling_commit(wt)
        return made
    monkeypatch.setattr(engine.gitops, "commit_all", commit_all)
    _after_check(m, lambda t: armed.append(True))
    _start(m, repo, [sys.executable, "-c", "pass"])
    finish(m)
    _blocked(m, home, "moved while JARVES merged it")


def test_owner_edit_folded_at_the_merge_approval_is_checked_again(env):
    m, repo, home = env
    check = [sys.executable, "-c", "import os,sys; sys.exit(os.path.exists('mine.txt') and "
                                   "'bad' in open('mine.txt').read())"]
    open_project(m, repo, check=check)
    m.call("add_task", project="demo", title="Mine", spec="I do it", scope=["mine.txt"], flow="work:owner test merge:owner")
    m.call("resume", project="demo")
    finish(m)
    wt = Path(m.db.by_seq(1, 1)["worktree"])
    (wt / "mine.txt").write_text("good\n")
    assert m.call("answer", project="demo", task=1, answer="done")["ok"]
    finish(m)
    assert "approve merging" in m.db.by_seq(1, 1)["waiting"]
    (wt / "mine.txt").write_text("bad\n")                             # the owner's own edit, folded at the approval
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    assert not (_integ(home) / "mine.txt").exists()                   # the re-check failed: nothing merged
    assert any(e["kind"] == "check_failed" for e in m.db.events(m.db.by_seq(1, 1)["id"], 40))
