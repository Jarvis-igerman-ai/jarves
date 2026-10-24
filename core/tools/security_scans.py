"""Hosted entry point of the recurring security scans (.github/workflows/security-scans.yml); same code as `jarvis scans`.

    python core/tools/security_scans.py --kind light|full [--repo OWNER/NAME] [--no-report] [--dry-run] [--json]

Stateless: no home folder, no history file (the ONE tracker issue carries the last signature). Read-only inventory plus,
at most, creating or updating that one issue. Never merges, dismisses, deploys or sends anything else; no model calls.
Token: GH_TOKEN / GITHUB_TOKEN from the environment only. The result goes to stdout and, in Actions, to the job summary.
Exit 0 = the scan ran (findings are NOT a failing exit: they are in the issue); 1 = offline / failed (the next cron
retries); 2 = bad usage. Full scans also run the boundary tests (approvals, privacy, redaction) and report a summary.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "src"))
from jarves import security_scans as ss  # noqa: E402

BOUNDARY_TESTS = ["tests/test_helper_approvals.py", "tests/test_privacy.py", "tests/test_redaction_audit.py",
                  "tests/test_redaction_planted.py", "tests/test_netguard.py"]


def boundary_runner() -> dict:
    """pytest on the boundary tests. Exit 0 = pass, 1 = failures (-> findings), anything else = not run (-> incomplete)."""
    files = [t for t in BOUNDARY_TESTS if (CORE / t).exists()]
    if not files:
        return {"not_run": True}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *files],
                           cwd=CORE, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return {"not_run": True}
    if p.returncode == 0:
        return {"failed": 0}
    if p.returncode == 1:
        failed = sorted({Path(m).name for m in re.findall(r"^FAILED (\S+?)::", p.stdout or "", re.M)})
        return {"failed": 1, "failed_files": failed}
    return {"not_run": True}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kind", choices=ss.KINDS, required=True)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or ss.REPO)
    ap.add_argument("--no-report", action="store_true", help="never create or update the tracker issue")
    ap.add_argument("--dry-run", action="store_true", help="scan only; no GitHub write at all")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.repo != ss.REPO:
        print(f"refusing to scan {a.repo}: only {ss.REPO}", file=sys.stderr)
        return 2
    token = ss.find_token(env_only=True)
    if not token:
        print("no GH_TOKEN / GITHUB_TOKEN in the environment", file=sys.stderr)
        return 1
    r = ss.run_scan(None, a.kind, trigger="hosted", client=ss.http_client(token, a.repo), now=datetime.now(timezone.utc),
                    report=not a.no_report, dry_run=a.dry_run, boundary=boundary_runner if a.kind == "full" else None)
    counts = {s: sum(f["severity"] == s for f in r.get("findings", [])) for s in ("P1", "P2", "P3")}
    out = {"kind": a.kind, "status": r["status"], "findings": len(r.get("findings", [])), **counts,
           "coverage": r.get("coverage", {}), "issue": (r.get("issue") or {}).get("status", "-"),
           "resolved": r.get("resolved", 0), "llm_calls": 0}
    text = json.dumps(out, indent=2) if a.json else (
        f"security scan ({a.kind}): {out['status']}; findings {out['findings']} (P1 {counts['P1']}, P2 {counts['P2']}, "
        f"P3 {counts['P3']}); issue: {out['issue']}; coverage: "
        + ", ".join(f"{k}={v}" for k, v in sorted(out["coverage"].items())))
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Security scan ({a.kind})\n\n```\n{text}\n```\n")
    return 0 if r["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
