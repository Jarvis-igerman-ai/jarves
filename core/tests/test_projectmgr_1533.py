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
    assert changed == [] and not (tmp_path / "ran.txt").exists()
    assert gitops.git(wt, "log", "-1", "--format=%s").stdout.strip() != "a"


def test_claude_agents_may_not_edit_the_shared_git_folder(tmp_path):
    repo, wt = _task(tmp_path)
    argv = runners.ClaudeRunner(None).build("work", str(wt)).argv
    common = (repo / ".git").resolve().as_posix()
    common = "/" + common[0].lower() + common[2:] if common[1:2] == ":" else common
    deny = argv[argv.index("--disallowedTools"):]
    assert f"Edit(/{common}/**)" in deny and f"Write(/{common}/**)" in deny
