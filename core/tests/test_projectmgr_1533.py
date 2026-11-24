"""#1533: an agent (or a check running its code) that writes the repository's shared git files must never make JARVES
git run a command. Real git, a throw-away local repository; the planted filter writes a marker file if it ever runs."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from jarves.projectmgr import gitops, runners


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


def _plant_code(git_dir: Path, wt: Path, marker: Path) -> str:
    """Python source that adds a clean filter to `git_dir/config` and selects it for every file in `wt`."""
    evil = marker.with_suffix(".py")
    evil.write_text(f"import sys\nopen({str(marker)!r}, 'w').close()\nsys.stdout.write(sys.stdin.read())\n")
    entry = f'[filter "evil"]\n\tclean = "{Path(sys.executable).as_posix()}" "{evil.as_posix()}"\n'
    return (f"open({str(git_dir / 'config')!r}, 'a').write({entry!r});"
            f"open({str(wt / '.gitattributes')!r}, 'w').write('* filter=evil\\n');"
            f"open({str(wt / 'new.txt')!r}, 'w').write('y')")


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
    r = _Planter(_plant_code(repo / ".git", wt, marker)).run("x", str(wt))
    assert not r.ok and "shared git files (config)" in r.error
    assert cfg.read_bytes() == before
    gitops.commit_all(wt, "task")
    assert not marker.exists()
    kept = list(cfg.parent.glob("config.jarves-rejected-*"))
    assert len(kept) == 1 and b"evil" in kept[0].read_bytes()


def test_every_rejected_copy_is_kept_even_within_one_clock_tick(tmp_path, monkeypatch):
    repo, _home, wt, _br = _setup(tmp_path)
    monkeypatch.setattr(gitops.time, "time_ns", lambda: 1)        # review R1: a coarse clock must not reuse a name
    for i in range(2):
        ok, out = gitops.run_check(wt, [sys.executable, "-c",
                                        f"open({str(repo / '.git' / 'config')!r}, 'a').write('#{i}\\n')"])
        assert not ok and "shared git files (config)" in out
    assert sorted(f.read_bytes()[-3:] for f in (repo / ".git").glob("config.jarves-rejected-*")) == [b"#0\n", b"#1\n"]


def test_a_filter_planted_after_the_last_check_never_runs(tmp_path):
    repo, _home, wt, _br = _setup(tmp_path)
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    marker = tmp_path / "ran"
    exec(_plant_code(repo / ".git", wt, marker))                  # a process the check left behind
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
    exec(_plant_code(fake, wt, marker))
    (fake / "worktrees" / wt.name / "commondir").write_text(str(fake), encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {(fake / 'worktrees' / wt.name).as_posix()}\n", encoding="utf-8")
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
        exec(_plant_code(repo / ".git", wt, marker))
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
