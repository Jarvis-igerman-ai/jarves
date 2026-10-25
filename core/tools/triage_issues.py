"""Triage of the issues JARVES files about itself (label `from-diagnostics`, docs/BUGFIX_LANE.md).

    python tools/triage_issues.py [--json issues.json] [--repo Jarvis-igerman-ai/jarves] [--changelog CHANGELOG.md] [--close-fixed]

Reads the open `from-diagnostics` issues (GitHub API through `gh`, or a saved JSON list for offline use and tests),
groups them by the registry id in their body (`<!-- jarves-issue: JI-xxxxxxxx -->`), ranks them (P1 first, then how often
and how many installs), and says what to do:

  - FIX      open, not mentioned as fixed in the CHANGELOG
  - CLOSE    the CHANGELOG lists the id or code on a Fixes line of a released version: safe to close with a comment
  - DUPLICATE  two GitHub issues carry the same registry id: keep the oldest

Read-only by default. `--close-fixed` closes only the CLOSE ones (comment "Fixed in <version>"), needs `gh` logged in.
The reports are content-free by construction (jarves.issue_registry), so nothing private is read here either.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jarves import issue_registry as ir  # noqa: E402

REPO = ir.REPO
MARK = re.compile(r"jarves-issue:\s*(JI-[0-9a-f]{8})")
SEV = re.compile(r"^\[(P[123])\]")
COUNT = re.compile(r"\*\*Count:\*\*\s*(\d+)")
CODE = re.compile(r"\*\*Code:\*\*\s*`([^`]+)`")
VERSION_HEAD = re.compile(r"^##\s+v?(\d+\.\d+(?:\.\d+)*)", re.M)


def released_fixes(changelog: str) -> dict[str, str]:
    """{id-or-code: version} from the Fixes lines of each released section of a changelog (unreleased sections are skipped)."""
    out: dict[str, str] = {}
    parts = VERSION_HEAD.split(changelog)
    heads = re.findall(r"^##\s+v?\d+\.\d+(?:\.\d+)*.*$", changelog, re.M)
    for i, ver in enumerate(parts[1::2]):
        if "unreleased" in heads[i].lower():
            continue
        ids, codes = ir.fixes_in(parts[2 + 2 * i])
        for k in ids | codes:
            out.setdefault(k, ver)
    return out


def group(issues: list[dict]) -> list[dict]:
    """One row per registry id, ranked. Issues without the marker are ignored (not ours)."""
    rows: dict[str, dict] = {}
    for it in issues:
        body = str(it.get("body") or "")
        m = MARK.search(body)
        if not m or it.get("pull_request"):
            continue
        rid = m.group(1)
        sev = (SEV.match(str(it.get("title") or "")) or [None, "P3"])[1]
        row = rows.setdefault(rid, {"id": rid, "numbers": [], "severity": sev, "count": 0, "code": "", "title": str(it.get("title") or "")})
        row["numbers"].append(it["number"])
        row["severity"] = min(row["severity"], sev)
        row["count"] += int((COUNT.search(body) or [0, 1])[1])
        row["code"] = row["code"] or (CODE.search(body) or [0, ""])[1]
    return sorted(rows.values(), key=lambda r: (r["severity"], -len(r["numbers"]), -r["count"], r["id"]))


def decide(rows: list[dict], fixes: dict[str, str]) -> list[dict]:
    for r in rows:
        ver = fixes.get(r["id"]) or fixes.get(r["code"])
        r["fixed_in"] = ver or ""
        r["action"] = "CLOSE" if ver else "DUPLICATE" if len(r["numbers"]) > 1 else "FIX"
    return rows


def render(rows: list[dict]) -> str:
    if not rows:
        return "No open from-diagnostics issues."
    lines = [f"{len(rows)} registry issue(s), worst first:"]
    for r in rows:
        nums = ", ".join(f"#{n}" for n in sorted(r["numbers"]))
        extra = f" (fixed in {r['fixed_in']})" if r["fixed_in"] else ""
        lines.append(f"  {r['action']:<9} {r['severity']}  {r['id']}  seen x{r['count']:<4} {nums:<14} {r['title'][:70]}{extra}")
    return "\n".join(lines)


def fetch(repo: str) -> list[dict]:
    p = subprocess.run(["gh", "api", f"repos/{repo}/issues?labels={ir.LABEL}&state=open&per_page=100", "--paginate"],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise SystemExit("gh could not read the issues (is it logged in?): " + p.stderr.strip()[:200])
    return json.loads(p.stdout or "[]")


def close_fixed(repo: str, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        if r["action"] != "CLOSE":
            continue
        for num in r["numbers"]:
            subprocess.run(["gh", "issue", "comment", str(num), "-R", repo, "--body", f"Fixed in {r['fixed_in']} (CHANGELOG)."], check=True)
            subprocess.run(["gh", "issue", "close", str(num), "-R", repo, "--reason", "completed"], check=True)
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="triage_issues.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", type=Path, help="a saved list of GitHub issues instead of asking GitHub")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--changelog", type=Path, default=Path(__file__).resolve().parents[2] / "CHANGELOG.md")
    ap.add_argument("--close-fixed", action="store_true", help="close the issues the CHANGELOG lists as fixed (needs gh)")
    a = ap.parse_args(argv)
    issues = json.loads(a.json.read_text(encoding="utf-8")) if a.json else fetch(a.repo)
    log = a.changelog.read_text(encoding="utf-8") if a.changelog.is_file() else ""
    rows = decide(group(issues), released_fixes(log))
    print(render(rows))
    if a.close_fixed:
        print(f"closed {close_fixed(a.repo, rows)} issue(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
