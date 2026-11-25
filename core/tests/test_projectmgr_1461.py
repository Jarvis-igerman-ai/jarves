"""#1461: all task worktrees share refs, so a sibling can move a task branch after JARVES checked it. The merge stage
must merge only the task commit the check saw, never whatever the branch points at later."""
from __future__ import annotations

import sys
from pathlib import Path

from test_projectmgr import env, finish, open_project, sh, task  # noqa: F401  (fixture + helpers)

from jarves.projectmgr import engine, gitops

GIT = ("git", "-c", "user.name=s", "-c", "user.email=s@s")
# #1599: the folder's own `.git` is a private copy, so the move goes through the owner repository's entry (a process
# that knows it), read from JARVES's pointer next to the folder
COMMIT_EVIL = ("import os, subprocess as s; d = os.getcwd(); "
               "g = open(os.path.join(os.path.dirname(d), '.' + os.path.basename(d) + '.gitlink')).read().strip(); "
               "open('a.txt','w').write('evil'); "
               f"s.run({list(GIT)!r} + ['commit', '-qam', 'evil', '--no-verify'], check=True, "
               "env=dict(os.environ, GIT_DIR=g, GIT_WORK_TREE=d))")


def _sibling_commit(wt) -> None:
    """A writer of the shared refs moves the task branch (#1599: the agent's own git in its folder no longer can)."""
    (Path(wt) / "a.txt").write_text("evil")
    gitops.git(wt, "commit", "-qam", "sibling", "--no-verify")


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


def _owner_task(m, repo, check):
    open_project(m, repo, check=check)
    m.call("add_task", project="demo", title="Mine", spec="I do it", scope=["mine.txt"], flow="work:owner test merge:owner")
    m.call("resume", project="demo")
    finish(m)
    wt = Path(m.db.by_seq(1, 1)["worktree"])
    (wt / "mine.txt").write_text("good\n")
    assert m.call("answer", project="demo", task=1, answer="done")["ok"]
    finish(m)
    assert "approve merging" in m.db.by_seq(1, 1)["waiting"]
    return wt


def test_owner_edit_folded_at_the_merge_approval_is_checked_again(env):
    m, repo, home = env
    wt = _owner_task(m, repo, [sys.executable, "-c", "import os,sys; sys.exit(os.path.exists('mine.txt') and "
                                                     "'bad' in open('mine.txt').read())"])
    (wt / "mine.txt").write_text("bad\n")                             # the owner's own edit, folded at the approval
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    assert not (_integ(home) / "mine.txt").exists()                   # the re-check failed: nothing merged
    assert any(e["kind"] == "check_failed" for e in m.db.events(m.db.by_seq(1, 1)["id"], 40))


def test_sibling_commit_before_the_owner_approves_is_not_an_owner_edit(env):
    """Review R1: one commit on the checked commit that answer() did not make is not the owner's own edit."""
    m, repo, home = env
    wt = _owner_task(m, repo, [sys.executable, "-c", "pass"])
    (wt / "mine.txt").write_text("evil\n")
    gitops.git(wt, "add", "-A")
    gitops.git(wt, "commit", "-qm", "sibling", "--no-verify")
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "moved after JARVES checked it" in t["reason"], (t["status"], t["reason"])
    assert not (_integ(home) / "mine.txt").exists()


def test_task_commit_already_in_the_integration_branch_still_merges(env):
    """Review R2: a merge-in that fast-forwards the task branch to the integration head is up to date, not a move."""
    m, repo, home = env

    def lands_with_more_on_top(t):
        sh(_integ(home), "git", "merge", "-q", "--ff-only", t["branch"])
        for n in ("o1", "o2"):                                         # two commits: merged^1 is not the task sha
            (_integ(home) / f"{n}.txt").write_text(n)
            sh(_integ(home), *GIT, "add", "-A")
            sh(_integ(home), *GIT, "commit", "-qm", n, "--no-verify")
    _after_check(m, lands_with_more_on_top)
    _start(m, repo, [sys.executable, "-c", "pass"])
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "done", (t["status"], t["reason"])
    assert (_integ(home) / "a.txt").read_text() == "1\n"


def test_file_left_after_the_check_is_checked_before_the_merge(env):
    """Review R3: what commit_all folds in after the check (here a file the check wrote) is checked again."""
    m, repo, home = env
    check = [sys.executable, "-c", "import os,sys; e=os.path.exists('left.txt'); open('left.txt','w').write('x'); "
                                   "sys.exit(1 if e else 0)"]
    _start(m, repo, check)
    finish(m)
    t = m.db.by_seq(1, 1)
    assert any(e["kind"] == "check_failed" for e in m.db.events(t["id"], 60))
    assert not (_integ(home) / "left.txt").exists() and t["status"] != "done", (t["status"], t["reason"])


def test_task_checked_before_this_fix_is_checked_again_at_the_merge(env):
    """Review R4: a task whose job has no tested_head (checked by an older JARVES) gets the merge-stage re-check."""
    m, repo, home = env
    moves = []

    def legacy_then_moved(t):
        if not moves:
            m._set_job(t, tested_head=None)
            moves.append(_sibling_commit(t["worktree"]))
    _after_check(m, legacy_then_moved)
    _start(m, repo, [sys.executable, "-c", "import sys; sys.exit('evil' in open('a.txt').read())"])
    finish(m)
    assert any(e["kind"] == "check_failed" for e in m.db.events(m.db.by_seq(1, 1)["id"], 60))
    assert (_integ(home) / "a.txt").read_text() == "1\n"              # the retry's checked content, never "evil"


def test_code_stage_after_the_check_is_checked_again_and_merges(env):
    """Review R1 (round 3): a code stage between the check and the merge is new code, not a move."""
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    m.call("add_task", project="demo", title="Mine", spec="I do it", scope=["mine.txt"],
           flow="work:owner test work:owner merge:owner")
    m.call("resume", project="demo")
    for text in ("one\n", "two\n"):
        finish(m)
        wt = Path(m.db.by_seq(1, 1)["worktree"])
        (wt / "mine.txt").write_text(text)
        assert m.call("answer", project="demo", task=1, answer="done")["ok"]
    finish(m)
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "done", (t["status"], t["reason"])
    assert (_integ(home) / "mine.txt").read_text() == "two\n"


def test_integration_moving_during_the_check_is_merged_in_and_checked(env):
    """Review R2 (round 3): tested_base is the integration commit from before the check, not one it never saw."""
    m, repo, home = env
    integ, flag = _integ(home), home.parent / "landed"
    land = (f"import os, subprocess as s\nif not os.path.exists(r'{flag}'):\n    open(r'{flag}', 'w').close()\n"
            f"    open(r'{integ / 'other.txt'}', 'w').write('o')\n"
            f"    s.run({list(GIT)!r} + ['-C', r'{integ}', 'add', '-A'], check=True)\n"
            f"    s.run({list(GIT)!r} + ['-C', r'{integ}', 'commit', '-qm', 'other', '--no-verify'], check=True)\n")
    _start(m, repo, [sys.executable, "-c", land])
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "done", (t["status"], t["reason"])
    assert (integ / "a.txt").read_text() == "1\n" and (integ / "other.txt").exists()
