"""PR hygiene (issue #89): daily triage list and size/area labels. It never closes or comments on a PR.

    python core/tools/pr_triage.py list [--json]                  # green / waiting / blocked / stale, oldest first
    python core/tools/pr_triage.py label --pr 123 [--apply]       # size/XS..XL and area/<x> labels from the file list

Read-only unless --apply (label only). Uses the `gh` CLI. Labelling reads only the PR's file LIST through the API; it
never checks out or runs PR code. "stale" in the list view only marks a draft with no update for 7 days (label `keep`
exempts); nothing acts on it. All classification functions are pure.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = "Jarvis-igerman-ai/jarves"
STALE_DAYS = 7
KEEP = "keep"
AREAS = [("core", r"^core/"), ("godot", r"^godot/"), ("packaging", r"^packaging/"), ("ci", r"^\.github/|^core/tools/(ci_|release|hotfix|bisect|pr_)"),
         ("docs", r"^docs/|\.md$"), ("bridge", r"^bridge/"), ("cad", r"^(cad|print|blender)/")]
SIZES = [(10, "XS"), (50, "S"), (250, "M"), (1000, "L")]
def _t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def size_label(changed_lines: int) -> str:
    return "size/" + next((n for lim, n in SIZES if changed_lines <= lim), "XL")


def area_labels(files: list[str]) -> list[str]:
    return sorted({f"area/{name}" for name, pat in AREAS if any(re.search(pat, f) for f in files)})


def checks_state(pr: dict) -> str:
    """'green' | 'red' | 'pending' | 'none' from statusCheckRollup (the `CI result` check decides when present)."""
    rollup = pr.get("statusCheckRollup") or []
    if not rollup:
        return "none"
    named = [c for c in rollup if c.get("name") == "CI result"]
    cs = named or rollup
    if any((c.get("status") or "COMPLETED") != "COMPLETED" for c in cs):
        return "pending"
    bad = [c for c in cs if (c.get("conclusion") or c.get("state") or "SUCCESS").upper() not in ("SUCCESS", "SKIPPED", "NEUTRAL")]
    return "red" if bad else "green"


def classify_pr(pr: dict, now: datetime) -> str:
    labels = {l["name"] if isinstance(l, dict) else l for l in pr.get("labels") or []}
    idle = now - _t(pr.get("updatedAt") or pr.get("createdAt"))
    if pr.get("isDraft") and KEEP not in labels and idle >= timedelta(days=STALE_DAYS):
        return "stale"
    if pr.get("mergeable") == "CONFLICTING":
        return "blocked"
    st = checks_state(pr)
    if st == "red" or pr.get("reviewDecision") == "CHANGES_REQUESTED":
        return "blocked"
    if pr.get("isDraft") or st in ("pending", "none"):
        return "waiting"
    return "green"


def triage(prs: list[dict], now: datetime | None = None) -> dict[str, list[dict]]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, list[dict]] = {"green": [], "waiting": [], "blocked": [], "stale": []}
    for pr in sorted(prs, key=lambda p: p["number"]):
        out[classify_pr(pr, now)].append(pr)
    return out


def gh(*a: str) -> str:
    r = subprocess.run(["gh", *a], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(a[:3])} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def open_prs() -> list[dict]:
    fields = "number,title,isDraft,mergeable,reviewDecision,labels,updatedAt,createdAt,statusCheckRollup,baseRefName"
    return json.loads(gh("pr", "list", "--repo", REPO, "--state", "open", "--limit", "200", "--json", fields))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pr_triage.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    lb = sub.add_parser("label")
    lb.add_argument("--pr", type=int, required=True)
    lb.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        t = triage(open_prs())
        if a.json:
            print(json.dumps({k: [p["number"] for p in v] for k, v in t.items()}))
        for k, v in t.items():
            print(f"{k.upper()} ({len(v)})")
            for p in v:
                print(f"  #{p['number']:<5} {p.get('baseRefName', '?'):<6} {p['title'][:70]}")
        return 0
    files = json.loads(gh("api", f"repos/{REPO}/pulls/{a.pr}/files?per_page=100", "--paginate", "--jq", "[.[]|{f:.filename,c:(.additions+.deletions)}]"))
    flat = files if files and isinstance(files[0], dict) else [x for chunk in files for x in chunk]
    labels = [size_label(sum(x["c"] for x in flat))] + area_labels([x["f"] for x in flat])
    print(f"#{a.pr}: " + ", ".join(labels))
    if a.apply:
        gh("pr", "edit", str(a.pr), "--repo", REPO, *[x for l in labels for x in ("--add-label", l)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
