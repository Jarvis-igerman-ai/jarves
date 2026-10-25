"""Offline, targeted regression tests for the overlapping-PR preflight."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "tools"))

import pr_overlap as po  # noqa: E402


def test_paths_are_repo_relative_and_windows_safe():
    assert po.paths([" ./core/src/jarves/app.py ", r"core\src\jarves\brain.py", "", " "]) == {
        "core/src/jarves/app.py", "core/src/jarves/brain.py",
    }


def test_find_overlaps_exact_matches_only():
    my = {"core/src/jarves/dashboard.py", "docs/handoff/new.md", "core/src/jarves/cli.py"}
    theirs = {
        91: {"core/src/jarves/dashboard.py", "docs/handoff/old.md"},
        93: {"core/src/jarves/brain.py"},
        95: {"core/src/jarves/cli.py", "core/src/jarves/dashboard.py"},
    }
    assert po.find_overlaps(my, theirs) == [
        (91, ["core/src/jarves/dashboard.py"]),
        (95, ["core/src/jarves/cli.py", "core/src/jarves/dashboard.py"]),
    ]


def test_preflight_excludes_self_and_only_reads_other_prs(monkeypatch):
    monkeypatch.setattr(po, "list_open_prs", lambda repo, base: [
        {"number": 1}, {"number": 2}, {"number": 3},
    ])
    queried = []
    def fake_files(repo, n):
        queried.append(n)
        return {f"f{n}.py", "common.py" if n == 2 else "none.py"}
    monkeypatch.setattr(po, "pr_files", fake_files)
    assert po.check("Jarvis-igerman-ai/jarves", "main", {"common.py"}, exclude_pr=1) == [(2, ["common.py"])]
    assert sorted(queried) == [2, 3]


def test_paginated_files_and_true_nonempty_checks(monkeypatch):
    def fake_command(args, timeout=20):
        assert "--paginate" in args and "--slurp" in args and "per_page=100" in args[-1]
        return json.dumps([[{"filename": "a.py"}], [{"filename": "b.py"}]])
    monkeypatch.setattr(po, "command", fake_command)
    assert po.pr_files("Jarvis-igerman-ai/jarves", 25) == {"a.py", "b.py"}


def test_fail_closed_on_partial_or_unavailable_inventory(monkeypatch, capsys):
    monkeypatch.setattr(po, "list_open_prs", lambda repo, base: [{"number": 5}, {"number": 9}])
    def fail_one(repo, n):
        if n == 9:
            raise po.PreflightError("API rate limit")
        return {"x.py"}
    monkeypatch.setattr(po, "pr_files", fail_one)
    monkeypatch.setattr(po, "changed_files", lambda base: {"x.py"})
    assert po.main([]) == 2
    assert "UNKNOWN" in capsys.readouterr().err


def test_main_overlap_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(po, "check", lambda *args: [(42, ["core/src/jarves/app.py"])])
    assert po.main(["--files", "core/src/jarves/app.py"]) == 1
    assert "PR #42" in capsys.readouterr().out
    monkeypatch.setattr(po, "check", lambda *args: [])
    assert po.main(["--files", "core/src/jarves/app.py"]) == 0
    assert "No file overlap" in capsys.readouterr().out


def test_open_pr_limit_fails_instead_of_lying(monkeypatch):
    monkeypatch.setattr(po, "command", lambda args, timeout=20: json.dumps(
        [{"number": n, "url": "", "title": ""} for n in range(31)]
    ))
    try:
        po.list_open_prs("Jarvis-igerman-ai/jarves", "main")
    except po.PreflightError as exc:
        assert "More than 30" in str(exc)
    else:
        assert False, "the incomplete inventory must not be presented as safe"
