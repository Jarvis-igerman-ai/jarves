"""Weekly issue sweep: pure functions only (no gh call)."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location("issue_sweep", Path(__file__).resolve().parents[1] / "tools" / "issue_sweep.py")
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)


def test_fixed_issues_only_from_merged_prs():
    prs = [{"number": 1, "title": "a", "body": "Fixes #10 and closes #11", "mergedAt": "2026-10-01T00:00:00Z"},
           {"number": 2, "title": "b", "body": "Fixes #12", "mergedAt": None},
           {"number": 3, "title": "Resolves #10 again", "body": "", "mergedAt": "2026-10-02T00:00:00Z"},
           {"number": 4, "title": "mentions #13 only", "body": "see #14", "mergedAt": "2026-10-03T00:00:00Z"}]
    out = sweep.fixed_issues(prs)
    assert set(out) == {10, 11} and out[10]["number"] == 3 and out[11]["number"] == 1


def test_stale_p3_and_counts():
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    iss = [{"number": 1, "createdAt": "2026-10-01T00:00:00Z", "labels": [{"name": "P3"}]},
           {"number": 2, "createdAt": "2026-10-20T00:00:00Z", "labels": [{"name": "P3"}]},
           {"number": 3, "createdAt": "2026-10-01T00:00:00Z", "labels": [{"name": "P2"}, {"name": "bug"}]},
           {"number": 4, "createdAt": "2026-10-01T00:00:00Z", "labels": [{"name": "P1"}]}]
    assert [i["number"] for i in sweep.stale_p3(iss, now)] == [1]
    assert sweep.open_p0_p2(iss) == {"P0": 0, "P1": 1, "P2": 1}
