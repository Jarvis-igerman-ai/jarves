"""#1533: an agent (or the check command running its code) that writes the repository's shared git files
(`.git/config`, `config.worktree`, `info/attributes`) could make git run a command for JARVES or later for the owner.
JARVES keeps one baseline per repository while agents or checks run: a change is restored and fails that run, and a
JARVES commit refuses to run while the shared files differ. No network; throw-away local repositories."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarves.projectmgr import gitops, runners


def _task(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    gitops.ensure_integration(tmp_path / "home", str(repo), 1)
    wt, _ = gitops.new_task_worktree(tmp_path / "home", str(repo), 1, 1, 1)
    return repo, wt


def _plant(repo: Path, tmp_path: Path) -> list[str]:
    """argv of a process that sets a clean filter (it writes a marker) for every file in the shared git files."""
    script = tmp_path / "evil.py"
    script.write_text(f"import sys\nopen({str(tmp_path / 'ran.txt')!r}, 'w').close()\n"
                      "sys.stdout.write(sys.stdin.read())\n", encoding="utf-8")
    cmd = f'"{sys.executable}" "{script}"'.replace("\\", "/")
    code = (f"import pathlib; g = pathlib.Path({str(repo / '.git')!r})\n"
            f"open(g / 'config', 'a').write('[filter \"evil\"]\\n\\tclean = {cmd}\\n')\n"
            "(g / 'info').mkdir(exist_ok=True); (g / 'info' / 'attributes').write_text('* filter=evil\\n')\n")
    return [sys.executable, "-c", code]


class _Agent(runners.Runner):
    name = "fake"

    def __init__(self, argv):
        super().__init__()
        self.argv = argv

    def build(self, prompt, cwd, **kw):
        return runners.Spec(self.argv, None, gitops.child_env())

    def parse(self, stdout, rc):
        return runners.Result(ok=rc == 0, text="done")


def test_an_agent_that_writes_the_shared_git_config_fails_and_it_is_restored(tmp_path):
    repo, wt = _task(tmp_path)
    before = (repo / ".git" / "config").read_bytes()
    res = _Agent(_plant(repo, tmp_path)).run("work", str(wt))
    assert not res.ok and "shared git" in res.error and "config" in res.error
    assert (repo / ".git" / "config").read_bytes() == before
    assert not (repo / ".git" / "info" / "attributes").exists()
    kept = list((repo / ".git").glob("config.jarves-rejected-*"))                               # review R4: kept
    assert len(kept) == 1 and "evil" in kept[0].read_text(encoding="utf-8")
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a") and not (tmp_path / "ran.txt").exists()


def test_a_check_that_writes_the_shared_git_config_fails_and_it_is_restored(tmp_path):
    repo, wt = _task(tmp_path)
    before = (repo / ".git" / "config").read_bytes()
    ok, out = gitops.run_check(wt, _plant(repo, tmp_path))
    assert not ok and "shared git" in out and "info/attributes" in out
    assert (repo / ".git" / "config").read_bytes() == before
    assert not (repo / ".git" / "info" / "attributes").exists()


def test_a_jarves_commit_never_runs_a_filter_planted_while_another_agent_runs(tmp_path):
    repo, wt = _task(tmp_path)
    before = (repo / ".git" / "config").read_bytes()
    with gitops.shared_config_guard(wt) as changed:              # another task's agent is still running
        import subprocess
        subprocess.run(_plant(repo, tmp_path), check=True)
        (wt / "a.py").write_text("1\n", encoding="utf-8")
        with pytest.raises(gitops.GitError, match="shared git"):
            gitops.commit_all(wt, "a")
        assert (repo / ".git" / "config").read_bytes() == before
    assert changed == ["config", "info/attributes"] and not (tmp_path / "ran.txt").exists()     # review R1
    assert gitops.git(wt, "log", "-1", "--format=%s").stdout.strip() != "a"


def test_claude_agents_may_not_edit_the_shared_git_folder(tmp_path):
    repo, wt = _task(tmp_path)
    argv = runners.ClaudeRunner(None).build("work", str(wt)).argv
    common = (repo / ".git").resolve().as_posix()
    common = "/" + common[0].lower() + common[2:] if common[1:2] == ":" else common
    deny = argv[argv.index("--disallowedTools"):]
    assert f"Edit(/{common}/**)" in deny and f"Write(/{common}/**)" in deny


def test_an_edit_after_the_last_run_ended_is_kept_but_stops_project_work(tmp_path):
    """Review R1 (round 1) + R2 (round 2): a later edit is never reverted, but commits and new runs refuse it."""
    repo, wt = _task(tmp_path)
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    with open(repo / ".git" / "config", "a", encoding="utf-8") as h:
        h.write("[x]\n\ty = 1\n")
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    with pytest.raises(gitops.GitError, match="restart JARVES to accept"):
        gitops.commit_all(wt, "a")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", "pass"])
    assert not ok and "(config) changed after the last agent run" in out
    assert "[x]" in (repo / ".git" / "config").read_text(encoding="utf-8") and gitops._GUARDS == {}


def test_a_filter_planted_after_the_check_ended_never_runs(tmp_path):
    """Review R2 (round 2): a process the check left behind writes after it returned; commit_all runs no git."""
    repo, wt = _task(tmp_path)
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    import subprocess
    subprocess.run(_plant(repo, tmp_path), check=True)
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    with pytest.raises(gitops.GitError, match="config, info/attributes"):
        gitops.commit_all(wt, "a")
    assert not (tmp_path / "ran.txt").exists() and "evil" in (repo / ".git" / "config").read_text(encoding="utf-8")
    res = _Agent([sys.executable, "-c", "pass"]).run("work", str(wt))
    assert not res.ok and "changed after the last agent run" in res.error


def test_an_agent_run_whose_files_cannot_be_put_back_fails_with_a_result(monkeypatch, tmp_path):
    """Review R3 (round 2): an OSError from the guard is a failed Result, not an exception out of Runner.run."""
    repo, wt = _task(tmp_path)

    def broken(f, data):
        raise PermissionError("read-only")
    monkeypatch.setattr(gitops, "_put_back", broken)
    res = _Agent(_plant(repo, tmp_path)).run("work", str(wt))
    assert not res.ok and res.error == "the shared git files could not be checked or put back: read-only"


def test_a_check_that_times_out_still_reports_the_changed_files(monkeypatch, tmp_path):
    """Review R3: a timeout no longer hides a change to the shared git files."""
    repo, wt = _task(tmp_path)
    real = gitops.subprocess.run

    def run(argv, **kw):
        if argv[0] != "CHECK":
            return real(argv, **kw)
        real(_plant(repo, tmp_path), check=True)
        raise gitops.subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(gitops.subprocess, "run", run)
    ok, out = gitops.run_check(wt, ["CHECK"])
    assert not ok and out.startswith("check timed out; the check changed the shared git files")
    assert not (repo / ".git" / "info" / "attributes").exists()


def test_a_failed_put_back_is_not_reported_as_a_check_that_could_not_start(monkeypatch, tmp_path):
    """Review R3: an OSError while putting the files back says so (fails closed), not 'could not start'."""
    repo, wt = _task(tmp_path)

    def broken(f, data):
        raise PermissionError("read-only")
    monkeypatch.setattr(gitops, "_put_back", broken)
    ok, out = gitops.run_check(wt, _plant(repo, tmp_path))
    assert not ok and "could not be checked or put back: read-only" in out and "could not start" not in out


def test_runner_run_keeps_its_explicit_keywords():
    """Review R4: a misspelled keyword fails at `run` itself, before any agent starts."""
    import inspect
    params = inspect.signature(runners.Runner.run).parameters
    assert "timeout" in params and all(p.kind != p.VAR_KEYWORD for p in params.values())


def test_every_rejected_copy_is_kept(tmp_path):
    """Review R4 (round 3): a second rejection no longer overwrites the first owner-visible copy."""
    repo, wt = _task(tmp_path)
    for n in ("one", "two"):
        line = f"[{n}]\\n\\tx = 1\\n"
        ok, _ = gitops.run_check(wt, [sys.executable, "-c", f"open({str(repo / '.git' / 'config')!r}, 'a').write('{line}')"])
        assert not ok
    kept = sorted(f.read_text(encoding="utf-8") for f in (repo / ".git").glob("config.jarves-rejected-*"))
    assert len(kept) == 2 and "[one]" in kept[0] + kept[1] and "[two]" in kept[0] + kept[1]


def _fake_common(repo: Path, tmp_path: Path) -> Path:
    """A copy of the real git folder whose config and attributes run a filter (writes ran.txt)."""
    import shutil
    fake = tmp_path / "fake"
    shutil.copytree(repo / ".git", fake)
    code = _plant(repo, tmp_path)[2].replace(repr(str(repo / ".git")), repr(str(fake)))
    import subprocess
    subprocess.run([sys.executable, "-c", code], check=True)
    return fake


def test_a_check_that_rewrites_commondir_fails_and_it_is_restored(tmp_path):
    """Review R1 (round 3): worktrees/<task>/commondir is a shared file too."""
    repo, wt = _task(tmp_path)
    fake = _fake_common(repo, tmp_path)
    cd = repo / ".git" / "worktrees" / "t1-a1" / "commondir"
    before = cd.read_bytes()
    ok, out = gitops.run_check(wt, [sys.executable, "-c", f"open({str(cd)!r}, 'w').write({str(fake)!r} + '\\n')"])
    assert not ok and "worktrees/t1-a1/commondir" in out and cd.read_bytes() == before
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a") and not (tmp_path / "ran.txt").exists()


def test_a_planted_worktree_pointer_to_a_fake_git_folder_is_never_followed(tmp_path):
    """Review R1 (round 3): a process a check left behind points `wt/.git` at a fake folder with a filter."""
    repo, wt = _task(tmp_path)
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    fake = _fake_common(repo, tmp_path)
    (fake / "worktrees" / "t1-a1" / "commondir").write_text(str(fake) + "\n", encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {fake / 'worktrees' / 't1-a1'}\n", encoding="utf-8")
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a") and not (tmp_path / "ran.txt").exists()
    assert gitops.git(repo, "log", "-1", "--format=%s", "jarves/p1/t1-a1").stdout.strip() == "a"


def test_jarves_own_worktree_changes_after_a_run_do_not_stop_project_work(tmp_path):
    """Review R3 (round 3): worktree add/remove by JARVES (even with branch.autoSetupMerge=always) joins the baseline."""
    repo, wt = _task(tmp_path)
    gitops.git(repo, "config", "branch.autoSetupMerge", "always")
    assert gitops.run_check(wt, [sys.executable, "-c", "pass"]) == (True, "")
    wt2, _ = gitops.new_task_worktree(tmp_path / "home", str(repo), 1, 2, 1)
    gitops.remove_worktree(str(repo), wt)
    (wt2 / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt2, "a")
    assert gitops.run_check(wt2, [sys.executable, "-c", "pass"]) == (True, "")
