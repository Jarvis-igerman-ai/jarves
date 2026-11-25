"""#1622: `gh` runs its own git reads (remotes, config) in its working folder. Those reads bypass gitops.git(), so a
project's agent-writable config (core.fsmonitor, url.*.insteadOf, remote.*.url) must never be visible to gh. JARVES
runs gh from an empty folder outside the project, with GIT_DIR pinned to a missing path, and names the repository
itself with a trailing --repo=<owner/repo> read through gitops.git()."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from jarves.projectmgr import forge, gitops

# the fake gh records where it ran and what a git read there sees (as the real gh's remote resolution would)
FAKE = textwrap.dedent('''
    import json, os, subprocess, sys
    a = sys.argv[1:]
    seen = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True)
    log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.jsonl")
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": a, "cwd": os.getcwd(), "git_dir": os.environ.get("GIT_DIR"),
                            "origin_seen": seen.stdout.strip()}) + "\\n")
    if a[:2] == ["pr", "create"]:
        print("https://github.com/o/r/pull/9")
    elif a[:2] == ["pr", "checks"]:
        print(json.dumps([{"name": "ci", "state": "SUCCESS", "bucket": "pass"}]))
    else:
        print("[]")
''')


@pytest.fixture
def ghlog(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    (d / "fake_gh.py").write_text(FAKE, encoding="utf-8")
    w = d / "gh"
    w.write_text(f"#!/bin/sh\nexec {sys.executable} {d / 'fake_gh.py'} \"$@\"\n")
    w.chmod(0o755)
    (d / "gh.cmd").write_text(f'@echo off\r\n"{sys.executable}" "{d / "fake_gh.py"}" %*\r\n')
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    return d / "calls.jsonl"


def _calls(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    gitops.git(r, "init", "-q", "-b", "main")
    gitops.git(r, "remote", "add", "origin", "https://github.com/o/r.git")
    return r


def test_1622_gh_runs_outside_the_project_and_names_the_repo(ghlog, repo):
    assert forge.create_pr(str(repo), "jarves/p1/t1-a1", "main", "t", "b").endswith("/pull/9")
    forge.issues(str(repo))
    assert forge.pr_checks(str(repo), "9")[0]["bucket"] == "pass"
    calls = _calls(ghlog)
    assert len(calls) == 3
    for c in calls:
        cwd = Path(c["cwd"]).resolve()
        assert repo.resolve() not in (cwd, *cwd.parents)          # not in the project folder or below it
        assert c["origin_seen"] == ""                             # gh's own git reads see no project config
        assert c["git_dir"] and not os.path.exists(c["git_dir"])
        assert c["argv"][-1] == "--repo=o/r"                      # last: pflag's last value wins


def test_1622_negative_control_gh_in_the_project_would_read_its_config(ghlog, repo):
    """Without the fix gh ran with cwd=<project>: its git read there returns the agent-writable origin."""
    subprocess.run(["gh", "issue", "list"], cwd=repo, check=True, capture_output=True,
                   shell=os.name == "nt")
    assert _calls(ghlog)[-1]["origin_seen"] == "https://github.com/o/r.git"


def test_1622_no_github_origin_or_another_repo_is_refused_before_gh(ghlog, repo):
    with pytest.raises(forge.ForgeError):
        forge.pr_checks(str(repo), "https://github.com/evil/x/pull/1")    # a URL would override --repo
    with pytest.raises(forge.ForgeError):
        forge.pr_checks(str(repo), "--repo=evil/x")
    assert forge.pr_checks(str(repo), "https://github.com/o/r/pull/9")    # the recorded repo's own URL is fine
    gitops.git(repo, "remote", "set-url", "origin", "https://example.com/o/r.git")
    with pytest.raises(forge.ForgeError):
        forge.issues(str(repo))
    assert len(_calls(ghlog)) == 1
