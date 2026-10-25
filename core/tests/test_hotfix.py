"""core/tools/hotfix.py: pure helpers and the dry-run flow with a fake runner. Offline, no git, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "tools"))

import hotfix  # noqa: E402

OID = "a" * 40
MERGED = {"number": 7, "state": "MERGED", "baseRefName": "main", "mergeCommit": {"oid": OID}, "title": "fix x"}


def test_release_minor():
    assert hotfix.release_minor("release/4.0") == "4.0"
    for bad in ("main", "release/4", "release/4.0.1", ""):
        with pytest.raises(SystemExit):
            hotfix.release_minor(bad)


def test_next_patch():
    assert hotfix.next_patch([], "4.0") == "4.0.1"
    assert hotfix.next_patch(["v4.0.0", "v4.0.2", "v4.0.10", "v4.1.0", "v4.0.1-beta"], "4.0") == "4.0.11"


def test_check_pr():
    assert hotfix.check_pr(MERGED) == []
    assert hotfix.check_pr({**MERGED, "state": "OPEN"})
    assert hotfix.check_pr({**MERGED, "baseRefName": "release/4.0"})
    assert hotfix.check_pr({**MERGED, "mergeCommit": None})


def test_check_remote_only_public_repo():
    assert hotfix.check_remote("https://github.com/Jarvis-igerman-ai/jarves.git") == []
    assert hotfix.check_remote("git@github.com:Jarvis-igerman-ai/jarves.git") == []
    assert hotfix.check_remote("git@github.com:igalgerman/jarves.git") == []      # the old address still redirects
    assert hotfix.check_remote("https://github.com/igalgerman/JARVIS.git")
    assert hotfix.check_remote("https://example.com/Jarvis-igerman-ai/jarves.git")


def test_commands_cherry_pick_and_pr():
    cmds = hotfix.commands(7, "4.0", OID, "jarves", "fix x", "4.0.1")
    assert ["git", "cherry-pick", "-x", "-m", "1", OID] in cmds
    assert ["git", "switch", "-c", "hotfix/7-to-4.0", "jarves/release/4.0"] in cmds
    pr = cmds[-1]
    assert pr[:3] == ["gh", "pr", "create"] and "release/4.0" in pr and "backport" in pr


def _runner(info, url, tags, calls):
    def run(cmd):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return json.dumps(info)
        if cmd[:3] == ["git", "remote", "get-url"]:
            return url
        if cmd[:2] == ["git", "tag"]:
            return tags
        return ""
    return run


def test_dry_run_runs_nothing_mutating():
    calls, lines = [], []
    rc = hotfix.backport(7, "release/4.0", "jarves", True, _runner(MERGED, "https://github.com/Jarvis-igerman-ai/jarves.git", "v4.0.0\nv4.0.1", calls), lines.append)
    assert rc == 0
    assert not any(c[:2] in (["git", "push"], ["git", "cherry-pick"], ["git", "switch"]) or c[:3] == ["gh", "pr", "create"] for c in calls)
    assert "v4.0.2" in lines[0]


def test_real_run_executes_commands_and_refuses_wrong_remote():
    calls, lines = [], []
    assert hotfix.backport(7, "release/4.0", "jarves", False, _runner(MERGED, "https://github.com/Jarvis-igerman-ai/jarves.git", "", calls), lines.append) == 0
    assert any(c[:2] == ["git", "push"] for c in calls)
    calls.clear()
    assert hotfix.backport(7, "release/4.0", "origin", False, _runner(MERGED, "https://github.com/igalgerman/JARVIS.git", "", calls), lines.append) == 1
    assert not any(c[:2] == ["git", "push"] for c in calls)
