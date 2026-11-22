"""#1461: the merge stage takes only the task commit the work-stage scope check or the last check ran on, or the exact
contents of your merge approval; reviews and owner test/verify answers never rebind it; #1469: owner text names the commit, not only the movable integration branch name.
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
    """The check ran on X; a sibling moved the branch to Y before your review yes: the yes never rebinds to Y (round 3
    R1), the task blocks and Y is not merged."""
    m, repo, home = env
    t = _start(m, repo, "work test review:owner merge")
    before = _rev(_integration(home))
    _sibling_moves(home, repo, t)
    r = m.call("answer", project="demo", task=1, answer="approve")
    assert not r["ok"] and r["code"] == "CHANGED", r
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "changed after the check" in t["reason"], t
    finish(m)
    assert m.db.by_seq(1, 1)["status"] == "blocked" and _rev(_integration(home)) == before


def _run(m, repo, flow, check=PASS):
    open_project(m, repo, check=check)
    m.call("add_task", project="demo", title="A", spec="FAKE write a.txt=1", scope=["a.txt"], flow=flow)
    m.call("resume", project="demo")
    finish(m)
    return m.db.by_seq(1, 1)


def _move_before(m, monkeypatch, home, repo, stage):
    real = getattr(m, stage)

    def wrapped(p, t, *a, **k):                         # a sibling moves the branch right before this stage runs
        if not getattr(wrapped, "done", False):
            wrapped.done = True
            _sibling_moves(home, repo, t)
        return real(p, t, *a, **k)
    monkeypatch.setattr(m, stage, wrapped)


def test_an_agent_review_after_a_move_since_the_check_does_not_rebind(env, monkeypatch):
    m, repo, home = env
    _move_before(m, monkeypatch, home, repo, "_st_review")
    t = _run(m, repo, "work test review merge")
    assert t["status"] == "blocked" and "changed after the check" in t["reason"] and "not reviewed" in t["reason"], t
    assert not (_integration(home) / "a.txt").exists()


def test_a_check_after_a_review_does_not_rebind_a_moved_branch(env, monkeypatch):
    m, repo, home = env
    _move_before(m, monkeypatch, home, repo, "_st_check")
    t = _run(m, repo, "work review test merge")
    assert t["status"] == "blocked" and "changed after the check" in t["reason"] and "not checked" in t["reason"], t
    assert not (_integration(home) / "a.txt").exists()


def test_a_merge_without_a_recorded_checked_commit_fails_closed(env):
    """Round 3 R3: e.g. a task already past its check when this version was installed."""
    m, repo, home = env
    t = _start(m, repo, "work test gate merge")
    m._set_job(t, tested_head=None)
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "no checked commit" in t["reason"], t
    assert not (_integration(home) / "a.txt").exists()


def test_a_merge_yes_binds_file_contents_not_only_names(env):
    """Round 3 R2: a file the question listed as uncommitted is rewritten before your yes: re-asked, not merged."""
    m, repo, home = env
    t = _start(m, repo, "work test merge:owner")
    wt = Path(t["worktree"])
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    (wt / "a.txt").write_text("shown\n")
    finish(m)                                           # the code changed after the yes: asked again, listing a.txt
    t = m.db.by_seq(1, 1)
    assert t["status"] == "waiting" and "uncommitted: a.txt" in t["waiting"], t
    (wt / "a.txt").write_text("rewritten after the question\n")
    r = m.call("answer", project="demo", task=1, answer="approve")
    assert not r["ok"] and r["code"] == "CHANGED", r
    finish(m)
    assert m.db.by_seq(1, 1)["status"] == "waiting" and not (_integration(home) / "a.txt").exists()


def test_a_merge_commit_replaced_during_the_merge_check_is_not_merged(env, monkeypatch):
    """Round 3 R4: same parents as the re-merge, other contents."""
    m, repo, home = env
    t = _start(m, repo, "work test gate merge")
    integ = _integration(home)
    (integ / "c.txt").write_text("newer work\n")                   # another task merged meanwhile: merge re-merges
    sh(integ, "git", "add", "c.txt")
    sh(integ, "git", "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-qm", "newer")
    before = _rev(integ)

    def forge(wt, *a, **k):
        cur = _rev(wt)
        (Path(wt) / "a.txt").write_text("forged\n")
        sh(wt, "git", "add", "a.txt")
        tree = sh(wt, "git", "write-tree").strip()
        c = sh(wt, "git", "-c", "user.name=x", "-c", "user.email=x@x", "commit-tree", tree, "-p",
               _rev(wt, cur + "^1"), "-p", _rev(wt, cur + "^2"), "-m", "forged").strip()
        sh(wt, "git", "reset", "-q", "--hard", c)
        return True, ""
    monkeypatch.setattr(gitops, "run_check", forge)
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "changed during the check" in t["reason"], t
    assert _rev(integ) == before


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
