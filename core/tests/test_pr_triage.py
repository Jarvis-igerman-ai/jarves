"""core/tools/pr_triage.py: size/area labels and the green/waiting/blocked/stale classification. Pure, offline."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core" / "tools"))

import pr_triage  # noqa: E402

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def _pr(n, **kw):
    base = {"number": n, "title": "t", "isDraft": False, "mergeable": "MERGEABLE", "reviewDecision": "",
            "labels": [], "updatedAt": NOW.isoformat(), "statusCheckRollup": [{"name": "CI result", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
    base.update(kw)
    return base


def test_size_label_boundaries():
    assert pr_triage.size_label(0) == "size/XS" and pr_triage.size_label(10) == "size/XS"
    assert pr_triage.size_label(11) == "size/S" and pr_triage.size_label(250) == "size/M"
    assert pr_triage.size_label(1000) == "size/L" and pr_triage.size_label(1001) == "size/XL"


def test_area_labels():
    got = pr_triage.area_labels(["core/src/a.py", "docs/X.md", ".github/workflows/ci.yml", "core/tools/hotfix.py"])
    assert got == ["area/ci", "area/core", "area/docs"]


def test_checks_state():
    assert pr_triage.checks_state({}) == "none"
    assert pr_triage.checks_state(_pr(1)) == "green"
    assert pr_triage.checks_state(_pr(1, statusCheckRollup=[{"name": "CI result", "status": "IN_PROGRESS"}])) == "pending"
    assert pr_triage.checks_state(_pr(1, statusCheckRollup=[{"name": "CI result", "status": "COMPLETED", "conclusion": "FAILURE"}])) == "red"


def test_classification_and_order():
    old = (NOW - timedelta(days=8)).isoformat()
    prs = [_pr(5, mergeable="CONFLICTING"), _pr(3), _pr(4, isDraft=True, updatedAt=old), _pr(2, isDraft=True),
           _pr(6, isDraft=True, updatedAt=old, labels=[{"name": "keep"}]), _pr(1, statusCheckRollup=[])]
    t = pr_triage.triage(prs, NOW)
    nums = {k: [p["number"] for p in v] for k, v in t.items()}
    assert nums == {"green": [3], "waiting": [1, 2, 6], "blocked": [5], "stale": [4]}


def test_no_auto_close_surface():
    assert not hasattr(pr_triage, "stale_actions")
    assert not hasattr(pr_triage, "STALE_CLOSE_DAYS")
