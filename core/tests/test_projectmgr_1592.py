"""#1592: a check command (run by JARVES or by the agent) executes worktree code that could write the shared git config."""
from __future__ import annotations

import sys
from pathlib import Path

from jarves.projectmgr import gitops, runners

PLANT = '[filter "x"]\n\tclean = evil\n'


def _wt(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    gitops.ensure_integration(tmp_path / "home", str(repo), 1)
    wt, _ = gitops.new_task_worktree(tmp_path / "home", str(repo), 1, 1, 1)
    return wt, repo / ".git" / "config"


def _append(path: Path, text: str) -> list[str]:
    return [sys.executable, "-c", f"open({str(path)!r}, 'a').write({text!r})"]


def test_a_check_that_plants_a_filter_in_the_shared_config_fails_and_is_undone(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    ok, out = gitops.run_check(wt, _append(cfg, PLANT))
    assert not ok and gitops.TAMPERED in out
    assert cfg.read_bytes() == before
    assert "evil" in Path(str(cfg) + ".jarves-rejected").read_text(encoding="utf-8")   # kept for the owner to see


def test_a_check_that_rewrites_the_worktree_pointer_is_undone(tmp_path):
    wt, _cfg = _wt(tmp_path)
    before = (wt / ".git").read_bytes()
    ok, out = gitops.run_check(wt, _append(wt / ".git", "\n"))
    assert not ok and gitops.TAMPERED in out and (wt / ".git").read_bytes() == before


def test_a_clean_check_passes_and_leaves_the_config_alone(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    assert gitops.run_check(wt, [sys.executable, "-c", "print('ok')"])[0] is True
    assert cfg.read_bytes() == before and not Path(str(cfg) + ".jarves-rejected").exists()
    assert gitops._GUARDS == {}


def test_a_check_outside_a_git_worktree_is_refused(tmp_path):
    marker = tmp_path / "ran"
    ok, out = gitops.run_check(tmp_path, _append(marker, "x"))
    assert not ok and out.startswith("check refused") and not marker.exists()


def test_overlapping_runs_never_adopt_an_entry_an_earlier_run_planted(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    files = gitops.guarded_files(wt)
    with gitops.config_guard(files) as first:
        cfg.write_text(cfg.read_text(encoding="utf-8") + PLANT, encoding="utf-8")
        with gitops.config_guard(files) as second:   # starts after the plant: must not take it as the baseline
            pass
        assert cfg.read_bytes() == before and second["tampered"] and first["tampered"]
    assert cfg.read_bytes() == before and gitops._GUARDS == {}


class _Script(runners.Runner):
    name = "fake"

    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self.argv = argv

    def build(self, prompt, cwd, *, model="", resume="", read_only=False, check_argv=None, team=False):
        return runners.Spec(self.argv, None, gitops.child_env())

    def parse(self, stdout, rc):
        return runners.Result(ok=rc == 0, text=stdout)


def test_an_agent_run_with_the_check_rule_cannot_keep_a_planted_entry(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    r = _Script(_append(cfg, PLANT)).run("p", str(wt), check_argv=["python", "-m", "pytest"])
    assert not r.ok and gitops.TAMPERED in r.error and cfg.read_bytes() == before


def test_an_agent_run_with_the_check_rule_outside_a_worktree_is_refused(tmp_path):
    marker = tmp_path / "ran"
    r = _Script(_append(marker, "x")).run("p", str(tmp_path), check_argv=["python", "-m", "pytest"])
    assert not r.ok and r.error.startswith("refused") and not marker.exists()


def test_an_agent_run_without_a_check_command_is_not_guarded(tmp_path):
    r = _Script([sys.executable, "-c", "print('hi')"]).run("p", str(tmp_path), read_only=True)
    assert r.ok and "hi" in r.text
