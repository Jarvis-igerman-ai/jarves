"""#1592: a check command (and an agent allowed to run it) executes task code as the owner. On Linux it runs in a
namespace where the repository's shared git files and every worktree's `.git` pointer are read-only, also for a
process it leaves behind. No network; throw-away local repositories."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import pytest

from jarves.projectmgr import confine, gitops, runners

# every route the review of #1661 listed: shared config, hooks, info/attributes, a new main `commondir`, another
# worktree's metadata and `.git` pointer (the integration one), this worktree's own pointer, and a detached survivor
ATTACK = r"""
import os, subprocess, sys, time
common, integ, own = sys.argv[1:4]
def put(p, text):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a") as f:
            f.write(text)
    except OSError as e:
        print("denied", p, e.errno)
put(common + "/config", '[filter "x"]\n\tclean = touch PWNED\n')
put(common + "/hooks/post-merge", "#!/bin/sh\ntouch PWNED\n")
put(common + "/info/attributes", "* filter=x\n")
put(common + "/commondir", "/tmp/evil\n")
for g in os.listdir(common + "/worktrees"):
    put(common + "/worktrees/" + g + "/gitdir", "x")
    put(common + "/worktrees/" + g + "/commondir", "x")
put(integ + "/.git", "gitdir: /tmp/evil/.git\n")
put(own, "gitdir: /tmp/evil/.git\n")
late = "import time,sys\ntime.sleep(1.5)\nopen(sys.argv[1] + '/config','a').write('[core]\\n\\tfsmonitor = touch PWNED\\n')"
subprocess.Popen([sys.executable, "-c", late, common], start_new_session=True)
"""


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    home = tmp_path / "home"
    integ = gitops.ensure_integration(home, str(repo), 1)
    wt, _br = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    return repo, integ, wt


def _state(repo: Path, integ: Path, wt: Path) -> dict:
    files = [p for p in (repo / ".git").rglob("*") if p.is_file() and "objects" not in p.parts]
    return {str(p): p.read_bytes() for p in [*files, integ / ".git", wt / ".git"]}


def _wait_for_survivor() -> None:
    deadline = time.monotonic() + 4                # the survivor writes after 1.5 s; bounded, sleeping wait
    while time.monotonic() < deadline:
        time.sleep(0.25)


@pytest.mark.linux_only
def test_a_check_cannot_change_any_shared_git_file_even_from_a_survivor(tmp_path):
    repo, integ, wt = _project(tmp_path)
    assert gitops.confine_available(), "user namespaces are needed for this test (#1592)"
    before = _state(repo, integ, wt)
    ok, out = gitops.run_check(wt, [sys.executable, "-c", ATTACK, str(repo / ".git"), str(integ), str(wt / ".git")])
    _wait_for_survivor()
    assert ok and "denied" in out
    assert _state(repo, integ, wt) == before
    assert gitops.git(integ, "config", "--get", "filter.x.clean", check=False).stdout == ""
    assert gitops.run_check(wt, [sys.executable, "-c", "open('built.txt','w').write('ok')"])[0]
    assert (wt / "built.txt").read_text() == "ok"   # the worktree itself stays writable for the check


class _Script(runners.Runner):
    name = "fake"

    def __init__(self, code: str, args: list[str]) -> None:
        super().__init__()
        self.code, self.args = code, args

    def build(self, prompt, cwd, **kw) -> runners.Spec:
        return runners.Spec([sys.executable, "-c", self.code, *self.args], None, gitops.child_env())

    def parse(self, stdout: str, rc: int) -> runners.Result:
        return runners.Result(ok=rc == 0, text=stdout)


@pytest.mark.linux_only
def test_an_agent_with_the_check_rule_can_commit_but_not_change_the_shared_git_files(tmp_path):
    repo, integ, wt = _project(tmp_path)
    commit = ATTACK + r"""
open("new.txt", "w").write("n")
ident = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null"]
subprocess.run(["git", *ident, "add", "new.txt"], check=True)
subprocess.run(["git", *ident, "commit", "-qm", "agent", "--no-verify"], check=True)
"""
    before = _state(repo, integ, wt)
    r = _Script(commit, [str(repo / ".git"), str(integ), str(wt / ".git")]).run(
        "p", str(wt), check_argv=["python", "-m", "pytest"], timeout=60)
    _wait_for_survivor()
    assert r.ok and "denied" in r.text, r.error
    assert gitops.git(wt, "log", "-1", "--format=%s").stdout.strip() == "agent"
    after = _state(repo, integ, wt)
    changed = {k for k in after if after[k] != before.get(k)}
    assert all("/refs/" in k or "/logs/" in k or "/worktrees/t1-a1/" in k for k in changed), changed
    assert not any(k.endswith(("/config", "/gitdir", "/commondir", "/.git")) for k in changed)


@pytest.mark.linux_only
def test_an_agent_without_the_check_rule_is_not_wrapped(monkeypatch, tmp_path):
    seen: list = []
    monkeypatch.setattr(gitops, "confined", lambda argv, wt, git_writes=False: seen.append(argv) or argv)
    _Script("print(1)", []).run("p", str(tmp_path), timeout=30)
    assert seen == []


@pytest.mark.linux_only
def test_a_confinement_that_cannot_be_set_up_never_runs_the_command(tmp_path):
    marker = tmp_path / "ran"
    bad = {"tree": str(tmp_path / "missing"), "ro": [], "rw": [], "mask": []}
    p = subprocess.run(confine.wrap([sys.executable, "-c", f"open({str(marker)!r},'w')"], bad),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == confine.FAIL and "could not confine" in p.stderr and not marker.exists()


def test_where_confinement_is_unavailable_the_check_runs_as_before_and_says_so_once(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(gitops, "_CONFINE", {})
    monkeypatch.setattr(gitops.sys, "platform", "darwin")
    with caplog.at_level(logging.WARNING, logger=gitops.__name__):
        assert gitops.confined(["echo"], tmp_path) == ["echo"]
        assert gitops.confined(["echo"], tmp_path) == ["echo"]
    assert [r.getMessage() for r in caplog.records].count(
        "projectmgr: check runs are not confined, the shared git files stay writable (#1592): "
        "not Linux (tracked in #1665)") == 1


@pytest.mark.linux_only
def test_the_spec_lists_every_worktree_pointer_and_only_this_worktrees_metadata_as_writable(tmp_path):
    repo, integ, wt = _project(tmp_path)
    common = (repo / ".git").resolve()
    s = confine.spec(common, common / "worktrees" / "t1-a1", wt, git_writes=True)
    assert s["tree"] == str(common) and json.loads(json.dumps(s)) == s
    assert str((integ / ".git").resolve()) in s["ro"] and str((wt / ".git").resolve()) in s["ro"]
    assert str(common / "worktrees" / "t1-a1") in s["rw"] and str(common / "worktrees" / "integration") not in s["rw"]
    assert str(common / "worktrees" / "t1-a1" / "gitdir") in s["ro"]
    assert confine.spec(common, common / "worktrees" / "t1-a1", wt, git_writes=False)["rw"] == []
