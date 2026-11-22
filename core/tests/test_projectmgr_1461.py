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


def _wrap_git(name, after):
    """Run `after(wt)` right after gitops.<name> returns (review R2/R3 race windows)."""
    orig = getattr(gitops, name)

    def run(wt, *a, **k):
        r = orig(wt, *a, **k)
        after(Path(wt))
        return r
    return orig, run


def test_branch_moved_inside_the_check_merge_window_is_not_recorded(env, monkeypatch):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    done = []

    def once(wt):
        if not done:
            done.append(1)
            _sibling_commit(wt, "moved before merge_in\n")
    orig = gitops.merge_in
    monkeypatch.setattr(gitops, "merge_in", lambda wt, *a, **k: (once(Path(wt)), orig(wt, *a, **k))[1])
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "not checked" in t["reason"], t["reason"]
    assert _integration_a(home) == ""


def _write(wt: Path, rel: str, body: str = "never scope-checked\n") -> None:
    """What a sibling can do without committing: leave a file in the shared worktree (#1461 review R1)."""
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text(body)


def _integration_has(home: Path, rel: str) -> bool:
    return (gitops.project_root(home, 1) / "integration" / rel).exists()


def test_uncommitted_file_before_the_merge_is_not_merged(env):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    _wrap(m, "_st_merge", lambda t: _write(Path(t["worktree"]), ".github/x.yml"))
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "not committed" in t["reason"] and "not merged" in t["reason"], t["reason"]
    assert not _integration_has(home, ".github/x.yml") and _integration_a(home) == ""


def test_uncommitted_file_before_the_check_is_not_checked(env):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    _wrap(m, "_st_check", lambda t: _write(Path(t["worktree"]), "extra.txt"))
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "extra.txt" in t["reason"] and "not checked" in t["reason"], t["reason"]
    assert not _integration_has(home, "extra.txt")


def _owner_merge_task(m, repo):
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    m.call("add_task", project="demo", title="A", spec="FAKE write a.txt=1", acceptance="x", scope=["a.txt"],
           flow="work test merge:owner")
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "waiting" and "approve merging" in t["waiting"]
    return Path(t["worktree"])


def test_protected_file_folded_at_owner_approval_is_not_merged(env):
    m, repo, home = env
    _write(_owner_merge_task(m, repo), ".github/x.yml")
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "moved after JARVES checked it" in t["reason"], t["reason"]
    assert not _integration_has(home, ".github/x.yml") and _integration_a(home) == ""


def test_owner_edit_at_approval_still_merges(env):
    m, repo, home = env
    _write(_owner_merge_task(m, repo), "a.txt", "owner\n")
    assert m.call("answer", project="demo", task=1, answer="approve")["ok"]
    finish(m)
    assert m.db.by_seq(1, 1)["status"] == "done"
    assert _integration_a(home) == "owner\n"


def test_move_during_scope_check_clears_an_old_checked_commit(env, monkeypatch):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    _wrap(m, "_after_code", lambda t: m._set_job(t, tested_head="stale"))
    orig, run = _wrap_git("link_files", lambda wt: _sibling_commit(wt, "during scope check\n"))
    monkeypatch.setattr(gitops, "link_files", run)
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert t["status"] == "blocked" and "during the scope check" in t["reason"], t["reason"]
    assert m._job(t).get("tested_head") is None


def test_missing_worktree_fails_closed(env, tmp_path):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    t = m.db.by_seq(1, 1)
    m._set_job(t, tested_head="abc")
    why = m._moved(t, tmp_path / "gone", "not merged")
    assert "worktree" in why and "not merged" in why, why


def test_sibling_merge_that_makes_merge_in_a_noop_is_not_checked(env, monkeypatch):
    """Review 2 R1: a merge whose first parent is the vetted commit but whose second parent is not the base."""
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    done = []

    def sibling_merge(wt: Path, base: str) -> None:
        if done:
            return
        done.append(1)
        br = sh(wt, "git", "rev-parse", "--abbrev-ref", "HEAD").strip()
        sh(wt, "git", "checkout", "-q", "-b", "side", base)
        (wt / "extra.txt").write_text("never scope-checked\n")
        sh(wt, *GIT, "add", "-A")
        sh(wt, *GIT, "commit", "-q", "-m", "side")
        sh(wt, "git", "checkout", "-q", br)
        sh(wt, *GIT, "merge", "-q", "--no-ff", "--no-edit", "side")
    orig = gitops.merge_in
    monkeypatch.setattr(gitops, "merge_in",
                        lambda wt, base, *a, **k: (sibling_merge(Path(wt), base), orig(wt, base, *a, **k))[1])
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert done and t["status"] == "blocked" and "not checked" in t["reason"], t["reason"]
    assert not (gitops.project_root(home, 1) / "integration" / "extra.txt").exists()


def _repo_ab(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo with commit a and its child b checked out (a merge_in that fast-forwarded the task branch to b)."""
    tmp_path.mkdir()
    sh(tmp_path, "git", "init", "-q")
    shas = []
    for n in ("a", "b"):
        (tmp_path / "f").write_text(n)
        sh(tmp_path, *GIT, "add", "-A")
        sh(tmp_path, *GIT, "commit", "-q", "-m", n)
        shas.append(sh(tmp_path, "git", "rev-parse", "HEAD").strip())
    return tmp_path, shas[0], shas[1]


def test_bind_accepts_a_fast_forward_to_the_base(env, tmp_path):
    m, repo, home = env
    wt, a, b = _repo_ab(tmp_path / "r")
    assert m._bind(wt, a, "x", merged=True, base_sha=b) == (b, "")
    assert m._bind(wt, a, "x", merged=True, base_sha=a)[0] == ""          # b is not the base: blocked
    sh(wt, "git", "checkout", "-q", a)                                     # a sibling reset the branch to the base
    assert m._bind(wt, b, "x", merged=True, base_sha=a)[0] == ""          # review R2: vetted work dropped: blocked


def test_bind_on_a_missing_worktree_fails_closed(env, tmp_path):
    m, repo, home = env
    sha, why = m._bind(tmp_path / "gone", "abc", "not merged")
    assert sha == "" and "worktree is missing" in why and "not merged" in why, why


def _evil_merge(wt: Path, base_sha: str) -> None:
    """Review 3 R1: a merge with the exact parents [vetted, base_sha] amended with content nobody checked."""
    sh(wt, *GIT, "merge", "-q", "--no-ff", "--no-edit", base_sha)
    (wt / "extra.txt").write_text("never scope-checked\n")
    sh(wt, *GIT, "add", "-A")
    sh(wt, *GIT, "commit", "-q", "--amend", "--no-edit")


def _evil_before_merge_in(monkeypatch, done: list) -> None:
    orig = gitops.merge_in

    def run(wt, ref, *a, **k):
        if not done:
            done.append(1)
            _evil_merge(Path(wt), sh(Path(wt), "git", "rev-parse", ref).strip())
        return orig(wt, ref, *a, **k)
    monkeypatch.setattr(gitops, "merge_in", run)


def _base_commit(home: Path) -> None:
    """Newer work lands on the integration branch, so a later merge_in has something to merge."""
    integ = gitops.project_root(home, 1) / "integration"
    (integ / "b.txt").write_text("newer\n")
    sh(integ, *GIT, "add", "-A")
    sh(integ, *GIT, "commit", "-q", "-m", "newer")


def test_amended_merge_with_the_base_parents_is_not_checked(env, monkeypatch):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    done = []
    _wrap(m, "_st_check", lambda t: _base_commit(home))
    _evil_before_merge_in(monkeypatch, done)
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert done and t["status"] == "blocked" and "not checked" in t["reason"], t["reason"]
    assert not (gitops.project_root(home, 1) / "integration" / "extra.txt").exists()


def test_amended_merge_with_the_base_parents_is_not_merged(env, monkeypatch):
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    done = []
    _wrap(m, "_st_merge", lambda t: (_base_commit(home), _evil_before_merge_in(monkeypatch, done)))
    m.call("resume", project="demo")
    finish(m)
    t = m.db.by_seq(1, 1)
    assert done and t["status"] == "blocked" and "not merged" in t["reason"], t["reason"]
    assert not (gitops.project_root(home, 1) / "integration" / "extra.txt").exists()


def test_bind_accepts_a_clean_merge_and_refuses_an_amended_one(env, tmp_path):
    m, repo, home = env
    wt, a, b = _repo_ab(tmp_path / "r")
    sh(wt, "git", "checkout", "-q", "-b", "task", a)
    (wt / "g").write_text("task")
    sh(wt, *GIT, "add", "-A")
    sh(wt, *GIT, "commit", "-q", "-m", "task")
    vetted = sh(wt, "git", "rev-parse", "HEAD").strip()
    sh(wt, *GIT, "merge", "-q", "--no-ff", "--no-edit", b)
    clean_sha = sh(wt, "git", "rev-parse", "HEAD").strip()
    assert m._bind(wt, vetted, "x", merged=True, base_sha=b) == (clean_sha, "")
    sh(wt, "git", "reset", "-q", "--hard", vetted)
    _evil_merge(wt, b)
    sha, why = m._bind(wt, vetted, "x", merged=True, base_sha=b)
    assert sha == "" and "moved after JARVES checked it" in why, why


def test_scope_check_reads_the_fixed_commit_not_the_moving_head(env, monkeypatch):
    """Review 3 R3: a sibling moves the branch away and back (A->B->A) while the scope check reads it."""
    m, repo, home = env
    open_project(m, repo, check=[sys.executable, "-c", "pass"])
    task(m, "A", "FAKE write a.txt=1", scope=["a.txt"])
    seen = []
    orig = gitops.changed_files

    def run(wt, base, *a, **k):
        seen.append(a[0] if a else k.get("head", "HEAD"))
        return orig(wt, base, *a, **k)
    monkeypatch.setattr(gitops, "changed_files", run)
    m.call("resume", project="demo")
    finish(m)
    assert seen and len(seen[0]) == 40 and seen[0] != "HEAD", seen
