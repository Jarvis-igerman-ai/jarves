"""Weekly issue sweep (lean process, agent 1). Read-only by default.

  python core/tools/issue_sweep.py            # report: open issues whose fix PR merged, P3 older than 14 days, P0-P2 count
  python core/tools/issue_sweep.py --close    # also close the fixed ones, with the merged PR link as evidence

An issue is "fixed" only when a MERGED pull request body or title says `Fixes #N` / `Closes #N` / `Resolves #N`. Moving P3
issues into docs/reviews/FINDINGS_LEDGER.md and closing them needs the owner's OK, so this script only LISTS them.
Needs the `gh` CLI (authenticated). No network call is made by the pure functions below (they are what the tests cover).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

REPO = "Jarvis-igerman-ai/jarves"
_FIX = re.compile(r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s+(?:[\w.-]+/[\w.-]+)?#(\d+)", re.I)


def fixed_issues(prs: list[dict]) -> dict[int, dict]:
    """issue number -> the merged PR (the newest one wins) that says it fixes it."""
    out: dict[int, dict] = {}
    for pr in sorted(prs, key=lambda p: p.get("mergedAt") or ""):
        if not pr.get("mergedAt"):
            continue
        for n in _FIX.findall(f"{pr.get('title', '')}\n{pr.get('body', '')}"):
            out[int(n)] = pr
    return out


def labels(issue: dict) -> set[str]:
    return {(x["name"] if isinstance(x, dict) else x) for x in issue.get("labels", [])}


def stale_p3(issues: list[dict], now: datetime, days: int = 14) -> list[dict]:
    res = []
    for i in issues:
        if "P3" in labels(i):
            made = datetime.fromisoformat(i["createdAt"].replace("Z", "+00:00"))
            if (now - made).days >= days:
                res.append(i)
    return sorted(res, key=lambda i: i["createdAt"])


def open_p0_p2(issues: list[dict]) -> dict[str, int]:
    return {p: sum(1 for i in issues if p in labels(i)) for p in ("P0", "P1", "P2")}


def _gh(*args: str) -> list[dict]:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return json.loads(r.stdout or "[]")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--close", action="store_true", help="close issues whose fix PR merged (evidence = PR link)")
    ap.add_argument("--repo", default=REPO)
    a = ap.parse_args(argv)
    issues = _gh("issue", "list", "-R", a.repo, "--state", "open", "--limit", "500", "--json", "number,title,labels,createdAt")
    prs = _gh("pr", "list", "-R", a.repo, "--state", "merged", "--limit", "300", "--json", "number,title,body,mergedAt,url")
    fixed = fixed_issues(prs)
    open_nums = {i["number"]: i for i in issues}
    todo = {n: pr for n, pr in fixed.items() if n in open_nums}
    print("open P0/P1/P2:", open_p0_p2(issues))
    print(f"\nOpen issues whose fix PR merged ({len(todo)}):")
    for n, pr in sorted(todo.items()):
        print(f"  #{n} {open_nums[n]['title'][:70]}  <- PR #{pr['number']} {pr['url']}")
        if a.close:
            subprocess.run(["gh", "issue", "close", str(n), "-R", a.repo, "-c",
                            f"Fixed by merged PR #{pr['number']}: {pr['url']} (weekly sweep)"], check=True)
    old = stale_p3(issues, datetime.now(timezone.utc))
    print(f"\nP3 older than 14 days ({len(old)}; move to the ledger only with the owner's OK):")
    for i in old:
        print(f"  #{i['number']} {i['createdAt'][:10]} {i['title'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
