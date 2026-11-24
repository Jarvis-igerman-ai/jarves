"""#1533: an agent (or a check running its code) that writes the repository's shared git files must never make JARVES
git run a command. Real git, a throw-away local repository; the planted filter writes a marker file if it ever runs."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jarves.projectmgr import gitops, runners


@pytest.fixture(autouse=True)
def _user_home(tmp_path, monkeypatch):
    """The user-level git files are guarded too (review R1): each test gets its own, never the real ones."""
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user" / "xdg"))
    (tmp_path / "user" / "xdg" / "git").mkdir(parents=True)


def _setup(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    home = tmp_path / "home"
    gitops.ensure_integration(home, str(repo), 1)
    wt, br = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    return repo, home, wt, br


def _plant_code(cfg: Path, wt: Path, marker: Path) -> str:
    """Python source that adds a clean filter to the config file `cfg` and selects it for every file in `wt`."""
    evil = marker.with_suffix(".py")
    evil.write_text(f"import sys\nopen({str(marker)!r}, 'w').close()\nsys.stdout.write(sys.stdin.read())\n")
    entry = f'[filter "evil"]\n\tclean = "{Path(sys.executable).as_posix()}" "{evil.as_posix()}"\n'
    return (f"open({str(cfg)!r}, 'a').write({entry!r});"
            f"open({str(wt / '.gitattributes')!r}, 'w').write('* filter=evil\\n');"
            f"open({str(wt / 'new.txt')!r}, 'w').write('y')")


def _point(dotgit: Path, gd: Path) -> None:
    """Rewrite a worktree's `.git` pointer (unlink first: on Windows git marks it hidden, which `open('w')` refuses)."""
    dotgit.unlink()
    dotgit.write_text(f"gitdir: {gd.as_posix()}\n", encoding="utf-8")


class _Planter(runners.Runner):
    name = "planter"

    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code

    def build(self, prompt, cwd, **kw):
        return runners.Spec([sys.executable, "-c", self.code], None, gitops.child_env())

    def parse(self, stdout, rc):
        return runners.Result(ok=rc == 0, text="done")


def test_an_agent_that_plants_a_filter_fails_it_is_put_back_and_never_runs(tmp_path):
    repo, _home, wt, _br = _setup(tmp_path)
    marker, cfg = tmp_path / "ran", repo / ".git" / "config"
    before = cfg.read_bytes()
    r = _Planter(_plant_code(repo / ".git" / "config", wt, marker)).run("x", str(wt))
    assert not r.ok and "shared git files (config)" in r.error
    assert cfg.read_bytes() == before
    gitops.commit_all(wt, "task")
    assert not marker.exists()
    kept = list(cfg.parent.glob("config.jarves-rejected-*"))
    assert len(kept) == 1 and b"evil" in kept[0].read_bytes()


def test_rejected_copies_are_ordered_capped_and_the_file_mode_is_kept(tmp_path, monkeypatch):
    """Review R5/R6 (#1702): a coarse clock (one tick for every rejection) neither reuses nor misorders a copy name;
    only the newest KEEP_REJECTED copies stay; the restored file keeps its mode."""
    repo, _home, wt, _br = _setup(tmp_path)
    cfg = repo / ".git" / "config"
    cfg.chmod(0o644)
    monkeypatch.setattr(gitops.time, "time_ns", lambda: 1)
    for i in range(gitops.KEEP_REJECTED + 2):
        ok, out = gitops.run_check(wt, [sys.executable, "-c", f"open({str(cfg)!r}, 'ab').write(b'#{i}\\n')"])
        assert not ok and "shared git files (config)" in out
    kept = [f.read_bytes()[-3:] for f in sorted(cfg.parent.glob("config.jarves-rejected-*"))]
    assert kept == [f"#{i}\n".encode() for i in range(2, gitops.KEEP_REJECTED + 2)]
    if os.name != "nt":
        assert cfg.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("where", [".gitconfig", "xdg/git/config"])
def test_a_filter_planted_in_the_user_git_config_never_runs(tmp_path, where):
    """Review R1 (#1702): ~/.gitconfig and $XDG_CONFIG_HOME/git/config are read by JARVES git as well."""
    _repo, _home, wt, _br = _setup(tmp_path)
    cfg, marker = tmp_path / "user" / where, tmp_path / "ran"
    r = _Planter(_plant_code(cfg, wt, marker)).run("x", str(wt))
    assert not r.ok and where in r.error
    assert not cfg.exists() or b"evil" not in cfg.read_bytes()
    gitops.commit_all(wt, "task")
    exec(_plant_code(cfg, wt, marker))                            # after the last run
    with pytest.raises(gitops.GitError, match="changed after the last agent run or check"):
        gitops.commit_all(wt, "task")
    assert not marker.exists()


def test_a_filter_planted_after_the_last_check_never_runs(tmp_path):
    repo, _home, wt, _br = _setup(tmp_path)
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    marker = tmp_path / "ran"
    exec(_plant_code(repo / ".git" / "config", wt, marker))                  # a process the check left behind
    planted = (repo / ".git" / "config").read_bytes()
    with pytest.raises(gitops.GitError, match="changed after the last agent run or check"):
        gitops.commit_all(wt, "task")
    assert not marker.exists() and (repo / ".git" / "config").read_bytes() == planted    # an owner edit is never lost
    assert "changed after the last" in _Planter("pass").run("x", str(wt)).error             # no new run starts
    assert not gitops.is_repo(str(wt))                          # check=False calls fail too, without raising


def test_a_planted_worktree_pointer_to_a_fake_git_folder_is_never_followed(tmp_path):
    repo, _home, wt, br = _setup(tmp_path)
    fake = tmp_path / "fake.git"
    shutil.copytree(repo / ".git", fake)
    marker = tmp_path / "ran"
    exec(_plant_code(fake / "config", wt, marker))
    (fake / "worktrees" / wt.name / "commondir").write_text(str(fake), encoding="utf-8")
    _point(wt / ".git", fake / "worktrees" / wt.name)
    assert gitops.commit_all(wt, "task")
    assert not marker.exists()
    assert gitops.git(repo, "log", "-1", "--format=%s", br).stdout.strip() == "task"     # the real repository


def test_jarves_own_worktree_changes_after_a_check_do_not_stop_project_work(tmp_path):
    """The CI failure of #1634: JARVES's own worktree add/remove (even with branch.autoSetupMerge=always) is no tamper."""
    repo, home, wt, _br = _setup(tmp_path)
    gitops.git(repo, "config", "branch.autoSetupMerge", "always")
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    wt2, br2 = gitops.new_task_worktree(home, str(repo), 1, 2, 1)
    gitops.remove_worktree(str(repo), wt2, br2)
    (wt / "n.txt").write_text("n", encoding="utf-8")
    assert gitops.commit_all(wt, "task")
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")


def test_a_jarves_commit_during_another_run_puts_the_file_back_fails_and_fails_that_run(tmp_path):
    repo, _home, wt, _br = _setup(tmp_path)
    marker = tmp_path / "ran"
    with gitops.shared_config_guard(wt) as changed:
        exec(_plant_code(repo / ".git" / "config", wt, marker))
        with pytest.raises(gitops.GitError, match="a running agent or check changed the shared git files"):
            gitops.commit_all(wt, "task")
    assert changed == ["config"] and not marker.exists()


def test_a_file_that_cannot_be_put_back_fails_the_check_and_the_run(tmp_path, monkeypatch):
    def boom(f, old):
        raise PermissionError("locked")
    monkeypatch.setattr(gitops, "_put_back", boom)
    for i, is_check in enumerate((True, False)):
        (tmp_path / str(i)).mkdir()
        repo, _home, wt, _br = _setup(tmp_path / str(i))
        code = f"open({str(repo / '.git' / 'config')!r}, 'a').write('#x\\n')"
        out = gitops.run_check(wt, [sys.executable, "-c", code])[1] if is_check else _Planter(code).run("x", str(wt)).error
        assert "could not be checked or put back: locked" in out


def test_a_folder_below_a_worktree_or_an_unknown_folder_is_guarded_too(tmp_path, monkeypatch):
    """Review R2 (#1702): the guard is not skipped for a cwd that is neither pinned nor known."""
    repo, _home, wt, _br = _setup(tmp_path)
    (wt / "sub").mkdir()
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    marker = tmp_path / "ran"
    exec(_plant_code(repo / ".git" / "config", wt, marker))
    with pytest.raises(gitops.GitError, match="changed after the last"):
        gitops.git(wt / "sub", "add", "-A")
    monkeypatch.setattr(gitops, "_COMMON", {})                    # a folder JARVES has not seen yet
    assert "changed after the last" in gitops.git(repo, "status", check=False).stderr
    assert not marker.exists()


def test_after_a_restart_a_planted_integration_pointer_is_refused(tmp_path, monkeypatch):
    """Review R3 (#1702): an existing integration worktree is pinned only if it points into the repository itself."""
    repo, home, _wt, _br = _setup(tmp_path)
    monkeypatch.setattr(gitops, "_PIN", {})
    monkeypatch.setattr(gitops, "_COMMON", {})
    fake, integ = tmp_path / "fake.git", home / "projects" / "1" / "integration"
    shutil.copytree(repo / ".git", fake)
    (fake / "worktrees" / "integration" / "commondir").write_text(str(fake), encoding="utf-8")
    _point(integ / ".git", fake / "worktrees" / "integration")
    with pytest.raises(gitops.GitError, match="does not point into the repository's own git folder"):
        gitops.ensure_integration(home, str(repo), 1)
    assert gitops._PIN == {}


def test_after_the_last_run_ordinary_owner_config_edits_are_kept_and_work_goes_on(tmp_path):
    """Review R4 (#1702): only a late change that adds a key git may run stops project work."""
    repo, _home, wt, _br = _setup(tmp_path)
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    for argv in (["remote", "add", "origin", "https://example.invalid/r.git"], ["config", "branch.main.remote", "origin"]):
        subprocess.run(["git", "-C", str(repo), *argv], check=True)              # the owner's own git, not JARVES
    (wt / "n.txt").write_text("n", encoding="utf-8")
    assert gitops.commit_all(wt, "task")
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    assert b"origin" in (repo / ".git" / "config").read_bytes()
    subprocess.run(["git", "-C", str(repo), "config", "core.sshCommand", "evil"], check=True)
    with pytest.raises(gitops.GitError, match=r"\(config\) changed after the last.*core\.sshcommand=evil"):
        gitops.commit_all(wt, "task 2")                         # review R3 of 82f81a11: the added key is named


@pytest.mark.parametrize("argv", [["core.alternateRefsCommand", "evil"], ["protocol.ext.allow", "always"],
                                  ["remote.x.url", "ext::sh -c evil"], ["submodule.s.update", "!evil"]])
def test_after_the_last_run_a_key_outside_the_allowlist_is_refused(tmp_path, argv):
    """Review R2 of 82f81a11: late edits are accepted from an allowlist only, not missed by a denylist."""
    repo, _home, wt, _br = _setup(tmp_path)
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    subprocess.run(["git", "-C", str(repo), "config", *argv], check=True)
    with pytest.raises(gitops.GitError, match=f"changed after the last.*{argv[0].lower()}="):
        gitops.commit_all(wt, "task")


def test_a_filter_planted_in_a_file_the_guarded_config_includes_is_put_back(tmp_path):
    """Review R1 of 82f81a11: `include.path` / `includeIf.*.path` targets of a guarded config are guarded too."""
    _repo, _home, wt, _br = _setup(tmp_path)
    inc, marker = tmp_path / "inc", tmp_path / "ran"
    (tmp_path / "user" / ".gitconfig").write_text(f"[include]\n\tpath = {inc.as_posix()}\n", encoding="utf-8")
    r = _Planter(_plant_code(inc, wt, marker)).run("x", str(wt))
    assert not r.ok and inc.as_posix() in r.error and not inc.exists()
    gitops.commit_all(wt, "task")
    assert not marker.exists()


def test_after_a_restart_a_planted_task_worktree_pointer_is_refused_on_first_use(tmp_path, monkeypatch):
    """Review R4 of 82f81a11: a task worktree reused after a restart is pinned (and checked) on its first use."""
    repo, home, wt, _br = _setup(tmp_path)
    monkeypatch.setattr(gitops, "_PIN", {})
    monkeypatch.setattr(gitops, "_COMMON", {})
    gitops.ensure_integration(home, str(repo), 1)                 # the project is known again
    fake, marker = tmp_path / "fake.git", tmp_path / "ran"
    shutil.copytree(repo / ".git", fake)
    exec(_plant_code(fake / "config", wt, marker))
    (fake / "worktrees" / wt.name / "commondir").write_text(str(fake), encoding="utf-8")
    _point(wt / ".git", fake / "worktrees" / wt.name)
    with pytest.raises(gitops.GitError, match="does not point into the repository's own git folder"):
        gitops.commit_all(wt, "task")
    assert not marker.exists()


def _two(tmp_path: Path):
    for n in ("a", "b"):
        (tmp_path / n).mkdir()
    return _setup(tmp_path / "a"), _setup(tmp_path / "b")


def test_a_user_config_filter_planted_during_a_run_in_one_repo_never_runs_in_another(tmp_path):
    """Review R1 (a) of 3d423b40: repository B has no run and no baseline of its own."""
    (_ra, _ha, wta, _), (_rb, _hb, wtb, _) = _two(tmp_path)
    cfg, marker = tmp_path / "user" / ".gitconfig", tmp_path / "ran"
    with gitops.shared_config_guard(wta) as changed:
        exec(_plant_code(cfg, wtb, marker))
        with pytest.raises(gitops.GitError, match="a running agent or check changed the shared git files"):
            gitops.commit_all(wtb, "task")
    assert not marker.exists() and not cfg.exists() and changed == [cfg.as_posix()]


def test_a_run_started_in_another_repo_after_the_plant_never_adopts_or_replants_it(tmp_path):
    """Review R1 (b) of 3d423b40: one process-wide baseline, so B never takes the planted bytes as clean."""
    (_ra, _ha, wta, _), (_rb, _hb, wtb, _) = _two(tmp_path)
    cfg, marker = tmp_path / "user" / ".gitconfig", tmp_path / "ran"
    cfg.write_text("[user]\n\tname = me\n", encoding="utf-8")
    clean = cfg.read_bytes()
    with gitops.shared_config_guard(wta):
        exec(_plant_code(cfg, wtb, marker))
        ok, out = gitops.run_check(wtb, [sys.executable, "-c", "pass"])
        assert not ok and ".gitconfig" in out and cfg.read_bytes() == clean
    assert cfg.read_bytes() == clean                              # A's exit put back nothing planted
    (wtb / "z.txt").write_text("z", encoding="utf-8")
    assert gitops.commit_all(wtb, "task") and not marker.exists()


def test_the_put_back_file_gets_the_baseline_mode_not_the_agents(tmp_path):
    """Review R3 of 3d423b40: an agent that also ran chmod does not keep its mode."""
    repo, _home, wt, _br = _setup(tmp_path)
    cfg = repo / ".git" / "config"
    cfg.chmod(0o644)
    code = f"import os; p = {str(cfg)!r}; open(p, 'a').write('#x\\n'); os.chmod(p, 0o666)"
    assert not gitops.run_check(wt, [sys.executable, "-c", code])[0]
    assert cfg.stat().st_mode & 0o777 == (0o644 if os.name != "nt" else cfg.stat().st_mode & 0o777)    # no POSIX modes


def test_an_oserror_from_the_agent_run_itself_is_not_reported_as_a_put_back_failure(tmp_path):
    """Review R4 of 3d423b40: only the guard's own OSError becomes the "could not be put back" result."""
    _repo, _home, wt, _br = _setup(tmp_path)

    class _Boom(_Planter):
        def parse(self, stdout, rc):
            raise OSError("parser failed")
    with pytest.raises(OSError, match="parser failed"):
        _Boom("pass").run("x", str(wt))
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")    # the guard was closed
