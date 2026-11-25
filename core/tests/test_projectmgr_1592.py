"""#1592: code a check command (or the agent) runs from the worktree can write the shared git dir (`.git/config`,
`.git/hooks`). JARVES restores it after the run and fails that check or agent run, so a planted entry never stays."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarves.projectmgr import gitops, runners

PLANT = ("import pathlib,subprocess;"
         "c=pathlib.Path(subprocess.check_output(['git','rev-parse','--path-format=absolute','--git-common-dir'],"
         "text=True).strip());"
         "open(c/'config','a').write('[filter \"x\"]\\n\\tclean = touch pwned\\n');"
         "(c/'hooks').mkdir(exist_ok=True);(c/'hooks'/'pre-commit').write_text('#!/bin/sh\\ntouch pwned\\n')")


@pytest.fixture()
def wt(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    w = tmp_path / "projects" / "1" / "wt"
    gitops.git(repo, "worktree", "add", "-q", "-b", "t1", str(w))
    return w


def _shared(w: Path) -> tuple[bytes, list[str]]:
    common = Path(gitops.git(w, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    hooks = sorted(p.name for p in (common / "hooks").iterdir()) if (common / "hooks").is_dir() else []
    return (common / "config").read_bytes(), hooks


def test_a_check_that_writes_the_shared_git_dir_fails_and_is_restored(wt):
    before = _shared(wt)
    ok, out = gitops.run_check(wt, [sys.executable, "-c", PLANT])
    assert not ok and "shared git" in out and "config" in out
    assert _shared(wt) == before                       # no planted filter, no planted hook


def test_a_clean_check_still_passes(wt):
    assert gitops.run_check(wt, [sys.executable, "-c", "print('ok')"]) == (True, "ok")


class _Fake(runners.Runner):
    name = "fake"

    def build(self, prompt, cwd, **kw):
        return runners.Spec([sys.executable, "-c", PLANT], None, gitops.child_env())

    def parse(self, stdout, rc):
        return runners.Result(ok=rc == 0, text="done")


def test_an_agent_run_that_writes_the_shared_git_dir_fails_and_is_restored(wt):
    before = _shared(wt)
    r = _Fake().run("p", str(wt))
    assert not r.ok and "shared git" in r.error
    assert _shared(wt) == before


def test_overlapping_guards_keep_the_first_baseline(wt):
    """Parallel tasks share one repository: a run that starts after a plant must never take it as its baseline."""
    before = _shared(wt)
    a, b = [], []
    ga, gb = gitops.shared_git_guard(wt, a), gitops.shared_git_guard(wt, b)
    ga.__enter__()
    subprocess.run([sys.executable, "-c", PLANT], cwd=wt, check=True)
    gb.__enter__()
    ga.__exit__(None, None, None)                      # restores and reports to the first run
    gb.__exit__(None, None, None)                      # must not put the planted files back
    assert a and _shared(wt) == before
