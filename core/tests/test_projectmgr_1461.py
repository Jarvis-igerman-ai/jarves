"""#1461: the merge stage takes only the task commit that its last vetting step saw (scope check, check, agent or owner
review/test, owner merge approval); #1469: owner text names the commit, not only the movable integration branch name.
Real git worktrees, the fake agent of test_projectmgr."""
from __future__ import annotations

import sys
from pathlib import Path

from jarves.projectmgr import gitops
from test_projectmgr import env, finish, open_project, sh  # noqa: F401  (env is a fixture)

PASS = [sys.executable, "-c", "pass"]


def _rev(cwd, ref="HEAD") -> str:
    return sh(cwd, "git", "rev-parse", ref).strip()


def _start(m, repo, flow, check=PASS):
    open_project(m, repo, check=check)
    m.call("add_task", project="demo", title="A", spec="FAKE write a.txt=1", scope=["a.txt"], flow=flow)
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "waiting", t
    return t


def _sibling_moves(home, repo, t, text="never checked\n"):
    """Another agent commits from its own worktree and points the shared task branch at it (worktrees share refs)."""
    other = gitops.project_root(home, 1) / "other"
    sh(repo, "git", "worktree", "add", "-q", "--detach", str(other), t["branch"])
    (other / "a.txt").write_text(text)
    sh(other, "git", "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-qam", "sneak")
    sh(repo, "git", "update-ref", f"refs/heads/{t['branch']}", _rev(other))
    sh(Path(t["worktree"]), "git", "reset", "-q", "--hard")     # the folder follows the branch: nothing uncommitted


def _integration(home) -> Path:
    return gitops.project_root(home, 1) / "integration"


def test_a_task_branch_moved_after_the_owner_review_is_not_merged(env):
    m, repo, home = env
    t = _start(m, repo, "work test review:owner merge")
    before = _rev(_integration(home))
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    _sibling_moves(home, repo, t)
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "changed after the check" in t["reason"], t
    assert _rev(_integration(home)) == before


def test_a_review_answer_does_not_cover_a_commit_made_after_the_question(env):
    """The owner reviewed commit X; a sibling moved the branch to Y before the answer: re-asked, naming Y."""
    m, repo, home = env
    t = _start(m, repo, "work test review:owner merge")
    _sibling_moves(home, repo, t)
    r = m.call("answer", project="demo", task=1, answer="approve")
    assert not r["ok"] and r["code"] == "CHANGED", r
    t = m.db.by_seq(1, 1)
    new = _rev(Path(t["worktree"]))[:12]
    assert t["status"] == "waiting" and f"commit {new}" in t["waiting"], t
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]          # you looked again: now it holds
    finish(m)
    assert m.db.by_seq(1, 1)["status"] == "done"
    assert (_integration(home) / "a.txt").read_text() == "never checked\n"


def test_an_owner_review_binds_without_a_check(env):
    """R1 of the first round: `work review:owner merge` and no check command."""
    m, repo, home = env
    t = _start(m, repo, "work review:owner merge", check=None)
    before = _rev(_integration(home))
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    _sibling_moves(home, repo, t)
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "changed after the check" in t["reason"], t
    assert _rev(_integration(home)) == before


def test_a_flow_without_check_or_review_binds_the_scope_checked_commit(env):
    """`work gate merge`: the scope check of the work stage is the only vetting step; a later move is not merged."""
    m, repo, home = env
    t = _start(m, repo, "work gate merge", check=None)
    before = _rev(_integration(home))
    _sibling_moves(home, repo, t)
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "changed after the check" in t["reason"], t
    assert _rev(_integration(home)) == before


def test_uncommitted_files_the_merge_question_did_not_name_are_re_asked(env):
    """R2 of the first round: a file that appears after the merge question is named before your yes covers it."""
    m, repo, home = env
    t = _start(m, repo, "work test merge:owner")
    wt = Path(t["worktree"])
    assert "uncommitted" not in t["waiting"]
    (wt / "a.txt").write_text("appeared later\n")
    r = m.call("answer", project="demo", task=1, answer="approve")
    assert not r["ok"] and r["code"] == "CHANGED", r
    t = m.db.by_seq(1, 1)
    assert t["status"] == "waiting" and f"commit {_rev(wt)[:12]}" in t["waiting"], t
    assert "changed since the question: a.txt" in t["waiting"]
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    assert m.db.by_seq(1, 1)["status"] == "done"
    assert (_integration(home) / "a.txt").read_text() == "appeared later\n"


def test_an_unmoved_task_merges_and_owner_text_names_the_commit(env):
    """#1469 item 1: the review command, the merge approval and the "merged into" event name commits."""
    m, repo, home = env
    t = _start(m, repo, "work test review:owner merge")
    base_sha = _rev(_integration(home))[:12]
    head_sha = _rev(Path(t["worktree"]))[:12]
    assert f"diff {base_sha}...{head_sha}" in t["waiting"] and f"at commit {base_sha}" in t["waiting"]
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "done", t
    merged = [e["text"] for e in m.db.events(t["id"], 40) if e["kind"] == "merged"]
    assert merged and f"at commit {_rev(_integration(home))[:12]}" in merged[-1]
    assert (_integration(home) / "a.txt").read_text() == "1\n"


def test_owner_merge_question_names_the_commits(env):
    m, repo, home = env
    t = _start(m, repo, "work merge:owner", check=None)
    assert f"approve merging task #1 (commit {_rev(Path(t['worktree']))[:12]})" in t["waiting"], t
    assert f"at commit {_rev(_integration(home))[:12]}" in t["waiting"]
