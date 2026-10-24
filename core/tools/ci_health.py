"""Daily CI health numbers (E3, #160): red-main rate, red-main minutes, PR wait, flakes. Read-only except --comment.

    python core/tools/ci_health.py [--days 7] [--repo igalgerman/jarves]          # print the Markdown table
    python core/tools/ci_health.py --comment 160                                  # also post it on that issue

Numbers (jarves-checks runs of the last N days, from `gh`):
  * red-main rate        failed / (failed + green) push runs on main (cancelled = replaced by a newer merge, not counted)
  * red-main minutes     sum of "red run ended -> next green main run ended"
  * PR run to green      median minutes from a PR run's start to its green end (full and light runs)
  * green to merged      median minutes from the head commit's last green PR run to the merge (merged PRs in the window)
  * flakes               open issues titled "Flaky test: ..." and the entries in core/tests/quarantine.toml
  * cancelled runner-min wall minutes of cancelled runs (newer push or merge)
Standard library + gh only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flake_scan import ledger_ids  # noqa: E402


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def _t(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _min(a: str, b: str) -> float:
    return (_t(b) - _t(a)).total_seconds() / 60


def _median(xs: list[float]) -> str:
    return f"{statistics.median(xs):.1f}" if xs else "-"


def red_main(runs: list[dict]) -> tuple[int, int, float]:
    """(red, green, red minutes) for push runs on main, oldest first."""
    main = sorted((r for r in runs if r["event"] == "push" and r["head_branch"] == "main"
                   and r["conclusion"] in ("success", "failure")), key=lambda r: r["created_at"])
    red = sum(r["conclusion"] == "failure" for r in main)
    minutes, since = 0.0, None
    for r in main:
        if r["conclusion"] == "failure" and since is None:
            since = r["updated_at"]
        elif r["conclusion"] == "success" and since is not None:
            minutes += max(0.0, _min(since, r["updated_at"]))
            since = None
    return red, len(main) - red, minutes


def pr_to_green(runs: list[dict]) -> list[float]:
    return [_min(r["run_started_at"], r["updated_at"]) for r in runs
            if r["event"] == "pull_request" and r["conclusion"] == "success" and r.get("run_started_at")]


def green_to_merged(runs: list[dict], prs: list[dict]) -> list[float]:
    last_green: dict[str, str] = {}
    for r in runs:
        if r["event"] == "pull_request" and r["conclusion"] == "success":
            last_green[r["head_sha"]] = max(last_green.get(r["head_sha"], ""), r["updated_at"])
    return [max(0.0, _min(last_green[p["headRefOid"]], p["mergedAt"])) for p in prs
            if p.get("mergedAt") and p.get("headRefOid") in last_green]


def cancelled_minutes(runs: list[dict]) -> float:
    return sum(max(0.0, _min(r["created_at"], r["updated_at"])) for r in runs if r["conclusion"] == "cancelled")


def render(days: int, runs: list[dict], prs: list[dict], flaky_issues: int, quarantined: int) -> str:
    red, green, red_min = red_main(runs)
    rate = f"{100 * red / (red + green):.0f}% ({red}/{red + green})" if red + green else "-"
    rows = [("red-main rate", rate), ("red-main minutes", f"{red_min:.0f}"),
            ("PR run to green, median min", _median(pr_to_green(runs))),
            ("green to merged, median min", _median(green_to_merged(runs, prs))),
            ("open flaky-test issues", str(flaky_issues)), ("quarantined tests", str(quarantined)),
            ("cancelled runner-minutes", f"{cancelled_minutes(runs):.0f}")]
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    return (f"**CI health {today}** (jarves-checks, last {days} days; `core/tools/ci_health.py`)\n\n| Number | Value |\n"
            "|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="igalgerman/jarves")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--comment", type=int, help="post the table as a comment on this issue")
    a = ap.parse_args(argv)
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.days)).strftime("%Y-%m-%d")
    raw = gh("api", "--paginate", f"repos/{a.repo}/actions/workflows/jarves-checks.yml/runs?created=>={since}&per_page=100",
             "--jq", ".workflow_runs[]|{event,head_branch,head_sha,conclusion,created_at,updated_at,run_started_at}")
    runs = [json.loads(x) for x in raw.splitlines() if x.strip()]
    prs = json.loads(gh("pr", "list", "-R", a.repo, "--state", "merged", "--search", f"merged:>={since}", "--limit", "300",
                        "--json", "headRefOid,mergedAt"))
    issues = json.loads(gh("issue", "list", "-R", a.repo, "--state", "open", "--search", "\"Flaky test:\" in:title",
                           "--json", "title"))
    text = render(a.days, runs, prs, sum(i["title"].startswith("Flaky test:") for i in issues), len(ledger_ids()))
    print(text)
    if a.comment:
        gh("issue", "comment", str(a.comment), "-R", a.repo, "--body", text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
