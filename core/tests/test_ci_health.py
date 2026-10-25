"""CI health numbers (E3, #160): red-main rate and minutes, PR wait, cancelled minutes, and the nightly wiring."""
from __future__ import annotations

from pathlib import Path

import yaml

import ci_health as ch

NIGHTLY = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nightly.yml"


def _run(event, branch, concl, created, updated, sha="s", started=None):
    return {"event": event, "head_branch": branch, "conclusion": concl, "created_at": created, "updated_at": updated,
            "head_sha": sha, "run_started_at": started or created}


RUNS = [
    _run("push", "main", "success", "2026-10-24T10:00:00Z", "2026-10-24T10:10:00Z"),
    _run("push", "main", "failure", "2026-10-24T11:00:00Z", "2026-10-24T11:15:00Z"),
    _run("push", "main", "cancelled", "2026-10-24T11:05:00Z", "2026-10-24T11:06:00Z"),
    _run("push", "main", "failure", "2026-10-24T11:20:00Z", "2026-10-24T11:30:00Z"),
    _run("push", "main", "success", "2026-10-24T11:40:00Z", "2026-10-24T12:15:00Z"),
    _run("pull_request", "b", "success", "2026-10-24T09:00:00Z", "2026-10-24T09:12:00Z", sha="h1"),
    _run("pull_request", "c", "success", "2026-10-24T09:00:00Z", "2026-10-24T09:04:00Z", sha="h2"),
]


def test_red_main_counts_failures_and_minutes_until_the_next_green():
    assert ch.red_main(RUNS) == (2, 2, 60.0)          # red from 11:15 until the green run ended at 12:15


def test_pr_wait_and_green_to_merged():
    assert sorted(ch.pr_to_green(RUNS)) == [4.0, 12.0]
    prs = [{"headRefOid": "h1", "mergedAt": "2026-10-24T09:20:00Z"}, {"headRefOid": "zz", "mergedAt": "2026-10-24T09:20:00Z"}]
    assert ch.green_to_merged(RUNS, prs) == [8.0]


def test_render_has_every_number():
    text = ch.render(7, RUNS, [], 1, 1)
    for k in ("red-main rate", "50% (2/4)", "red-main minutes", "PR run to green", "green to merged",
              "open flaky-test issues", "quarantined tests", "cancelled runner-minutes"):
        assert k in text


def test_nightly_posts_the_number_with_issue_write_only():
    job = yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))["jobs"]["flake-scan"]
    assert job["permissions"] == {"contents": "read", "actions": "read", "issues": "write"}
    assert any("ci_health.py" in s.get("run", "") and "--comment 160" in s["run"] for s in job["steps"])
