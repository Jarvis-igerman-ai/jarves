"""PR hygiene (docs/RELEASE_TRAIN.md): daily triage list, stale policy, size/area labels.

    python core/tools/pr_triage.py list [--json]                  # green / waiting / blocked / stale, oldest first
    python core/tools/pr_triage.py stale [--apply]                # comment at 7 days, close at 14 (drafts only, label `keep` exempt)
    python core/tools/pr_triage.py label --pr 123 [--apply]       # size/XS..XL and area/<x> labels from the file list

Read-only unless --apply. Uses the `gh` CLI (GH_TOKEN in CI). Labelling reads only the PR's file LIST through the API; it
never checks out or runs PR code, so it is safe under pull_request_target. All classification functions are pure.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = "igalgerman/jarves"
STALE_WARN_DAYS, STALE_CLOSE_DAYS = 7, 14
KEEP = "keep"
MARKER = "<!-- stale-bot -->"
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
    if pr.get("isDraft") and KEEP not in labels and idle >= timedelta(days=STALE_WARN_DAYS):
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


def stale_actions(prs: list[dict], now: datetime | None = None) -> list[dict]:
    """[{number, action: 'warn'|'close'}]. Drafts only; `keep` exempts; the warning comment is posted once (MARKER)."""
    now = now or datetime.now(timezone.utc)
    acts = []
    for pr in prs:
        labels = {l["name"] if isinstance(l, dict) else l for l in pr.get("labels") or []}
        if not pr.get("isDraft") or KEEP in labels:
            continue
        idle = now - _t(pr.get("pushedAt") or pr.get("updatedAt") or pr.get("createdAt"))
        warned = any(MARKER in str(c.get("body", "")) for c in pr.get("comments") or [])
        if idle >= timedelta(days=STALE_CLOSE_DAYS) and warned:
            acts.append({"number": pr["number"], "action": "close"})
        elif idle >= timedelta(days=STALE_WARN_DAYS) and not warned:
            acts.append({"number": pr["number"], "action": "warn"})
    return acts


def gh(*a: str) -> str:
    r = subprocess.run(["gh", *a], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(a[:3])} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def open_prs() -> list[dict]:
    fields = "number,title,isDraft,mergeable,reviewDecision,labels,updatedAt,createdAt,statusCheckRollup,baseRefName,comments,commits"
    prs = json.loads(gh("pr", "list", "--repo", REPO, "--state", "open", "--limit", "200", "--json", fields))
    for p in prs:
        c = p.get("commits") or []
        p["pushedAt"] = (c[-1].get("committedDate") if c else None) or p.get("updatedAt")
    return prs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pr_triage.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    st = sub.add_parser("stale")
    st.add_argument("--apply", action="store_true")
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
    if a.cmd == "stale":
        for act in stale_actions(open_prs()):
            n = act["number"]
            print(f"#{n}: {act['action']}")
            if not a.apply:
                continue
            if act["action"] == "warn":
                gh("pr", "comment", str(n), "--repo", REPO, "--body",
                   f"{MARKER}\nThis draft has had no push for {STALE_WARN_DAYS} days. It is closed after {STALE_CLOSE_DAYS} days "
                   f"without a push. Push, mark it ready, or add the `{KEEP}` label to keep it.")
            else:
                gh("pr", "close", str(n), "--repo", REPO, "--comment", "Closed: no push for 14 days (stale policy). Reopen any time.")
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
