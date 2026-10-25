"""Monthly CI minutes report for docs/HANDOFF.md (SW-V4 M item 6).

    python tools/ci_minutes.py [--repo Jarvis-igerman-ai/jarves] [--month 2026-10]

Sums job run time per workflow and runner OS from the GitHub Actions API through the `gh` CLI (needs `gh auth`).
Public repos are not billed; the report exists to see which jobs are slow and to check that a docs-only PR costs
less than 20% of the Windows minutes of a code PR. Prints a Markdown table.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def os_of(job: dict) -> str:
    labels = " ".join(job.get("labels") or []).lower()
    return "windows" if "windows" in labels else "macos" if "macos" in labels else "linux"


def summarize(jobs: list[dict], runs: dict[int, dict]) -> dict[tuple[str, str], float]:
    """{(workflow, os): minutes} over finished, non-skipped jobs."""
    out: dict[tuple[str, str], float] = defaultdict(float)
    for j in jobs:
        if not j.get("completed_at") or j.get("conclusion") == "skipped":
            continue
        wf = runs.get(j["run_id"], {}).get("name", "?")
        out[(wf, os_of(j))] += (_ts(j["completed_at"]) - _ts(j["started_at"])).total_seconds() / 60
    return dict(out)


def table(sums: dict[tuple[str, str], float]) -> str:
    lines = ["| workflow | os | minutes |", "|---|---|---|"]
    lines += [f"| {wf} | {o} | {m:.1f} |" for (wf, o), m in sorted(sums.items())]
    lines.append(f"| **total** | | {sum(sums.values()):.1f} |")
    return "\n".join(lines)


def _gh(path: str, key: str) -> list:
    p = subprocess.run(["gh", "api", "--paginate", path, "--jq", f".{key}[]"],
                       capture_output=True, text=True, check=True)
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="Jarvis-igerman-ai/jarves")
    ap.add_argument("--month", default=datetime.now().strftime("%Y-%m"))
    a = ap.parse_args(argv)
    runs = {r["id"]: r for r in _gh(f"repos/{a.repo}/actions/runs?created=%3E%3D{a.month}-01&per_page=100", "workflow_runs")}
    jobs = []
    for rid in runs:
        jobs += _gh(f"repos/{a.repo}/actions/runs/{rid}/jobs?per_page=100", "jobs")
    print(table(summarize(jobs, runs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
