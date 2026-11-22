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
    assert not r["ok"] and r["code"] == "BLOCKED", r                   # not the re-ask code CHANGED (round 4 R4)
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


def test_files_written_after_the_scope_check_are_never_checked_or_merged(env, monkeypatch):
    """Round 4 R1: the check stage must not commit (and so bind) files no scope check saw, e.g. a protected path."""
    m, repo, home = env
    real = m._st_check

    def write_first(p, t, *a, **k):
        (Path(t["worktree"]) / ".github").mkdir(exist_ok=True)
        (Path(t["worktree"]) / ".github" / "x.yml").write_text("on: push\n")
        return real(p, t, *a, **k)
    monkeypatch.setattr(m, "_st_check", write_first)
    t = _run(m, repo, "work test merge")
    assert t["status"] == "blocked" and "never scope-checked" in t["reason"] and ".github/x.yml" in t["reason"], t
    assert not (_integration(home) / ".github" / "x.yml").exists() and not (_integration(home) / "a.txt").exists()


def test_files_the_check_leaves_are_named_at_the_merge(env):
    """Round 4 R2: check output that is not gitignored blocks the merge by name, with the remedy."""
    m, repo, home = env
    t = _run(m, repo, "work test merge", check=[sys.executable, "-c", "open('coverage.out', 'w').write('x')"])
    assert t["status"] == "blocked" and "coverage.out" in t["reason"] and ".gitignore" in t["reason"], t
    assert not (_integration(home) / "a.txt").exists()


def test_a_merge_question_on_unchecked_content_says_so(env):
    """Round 4 R3: your merge yes on other content than the checked commit is your call: the question names it."""
    m, repo, home = env
    t = _start(m, repo, "work test merge:owner")
    checked = m.db.by_seq(1, 1)["job"]["tested_head"]
    assert "NOT the checked commit" not in t["waiting"]
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    (Path(t["worktree"]) / "b.txt").write_text("out of scope\n")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "waiting" and f"NOT the checked commit {checked[:12]}" in t["waiting"], t
    assert "outside the task scope: b.txt" in t["waiting"]


def test_a_check_without_a_recorded_checked_commit_fails_closed(env):
    """Doctor R1: with no checked commit recorded (a legacy task) the check never binds unscoped content."""
    m, repo, home = env
    t = _start(m, repo, "work gate test merge")
    _sibling_moves(home, repo, t, "unscoped\n")
    m._set_job(t, tested_head=None)
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "no checked commit" in t["reason"] and "not checked" in t["reason"], t
    assert m.db.by_seq(1, 1)["job"].get("tested_head") is None and not (_integration(home) / "a.txt").exists()


def test_after_a_back_rule_the_check_binds_only_what_the_rerun_work_stage_saw(env, monkeypatch):
    """Doctor R1: a review sends the task back:work; a move after that work stage is not checked or merged."""
    m, repo, home = env
    _start(m, repo, "work review:owner test merge")
    assert m.call("answer", project="demo", task=1, answer="reject", note="again")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "waiting" and t["rounds"] == 1 and t["job"].get("tested_head") == _rev(Path(t["worktree"]))
    _move_before(m, monkeypatch, home, repo, "_st_check")
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "changed after the check" in t["reason"] and "not checked" in t["reason"], t
    assert not (_integration(home) / "a.txt").exists()


def test_a_merge_commit_swapped_in_right_after_merge_in_is_not_merged(env, monkeypatch):
    """Doctor R2: no check command; a commit with the re-merge's parents but other contents replaces it at once."""
    m, repo, home = env
    _start(m, repo, "work gate merge", check=None)
    integ = _integration(home)
    (integ / "c.txt").write_text("newer work\n")                   # another task merged meanwhile: merge re-merges
    sh(integ, "git", "add", "c.txt")
    sh(integ, "git", "-c", "user.name=s", "-c", "user.email=s@s", "commit", "-qm", "newer")
    before = _rev(integ)
    real = gitops.merge_in

    def swap(wt, *a, **k):
        out = real(wt, *a, **k)
        cur = _rev(wt)
        (Path(wt) / "a.txt").write_text("forged\n")
        sh(wt, "git", "add", "a.txt")
        c = sh(wt, "git", "-c", "user.name=x", "-c", "user.email=x@x", "commit-tree", sh(wt, "git", "write-tree").strip(),
               "-p", _rev(wt, cur + "^1"), "-p", _rev(wt, cur + "^2"), "-m", "forged").strip()
        sh(wt, "git", "reset", "-q", "--hard", c)
        return out
    monkeypatch.setattr(gitops, "merge_in", swap)
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "moved during the merge" in t["reason"], t
    assert _rev(integ) == before


def test_worktree_tree_matches_what_commit_all_commits(env):
    """Doctor R4: the temporary index starts from the real one (staged, changed, deleted and new files)."""
    m, repo, home = env
    t = _start(m, repo, "work merge:owner", check=None)
    wt = Path(t["worktree"])
    (wt / "a.txt").write_text("changed\n")
    (wt / "n.txt").write_text("new\n")
    sh(wt, "git", "add", "n.txt")
    (wt / "u.txt").write_text("untracked\n")
    tree = gitops.worktree_tree(wt)
    assert gitops.commit_all(wt, "x") and tree == _rev(wt, "HEAD^{tree}")
