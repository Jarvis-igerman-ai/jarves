"""Failure-summary comment for a pull request: ONE comment per PR, updated in place (docs/PREPUSH.md).

    python core/tools/ci_comment.py build --jobs jobs.json --artifacts dir --run-url URL --out comment.md
    python core/tools/ci_comment.py post  --repo OWNER/REPO --pr N --body comment.md       # needs GH_TOKEN, gh CLI

`jobs.json` is the `gh api repos/.../actions/runs/ID/jobs` answer. `dir` holds the downloaded `junit-*` artifacts
(pytest-junit.xml, flake-report.json). The comment lists the failed jobs, the first failing test with ONE error line
(redacted, 200 characters at most) and the log link; tests that the flake report shows as known flakes are marked.
No log content beyond that one line, no secrets, no user data. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MARKER = "<!-- jarves-ci-summary -->"
BAD = ("failure", "timed_out", "cancelled")
SECRET = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{12,}|Bearer\s+\S+|"
                    r"(?i:token|secret|password|api[_-]?key)\s*[=:]\s*\S+)")
HOME = re.compile(r"(?:[A-Za-z]:)?[\\/](?:Users|home)[\\/][^\\/\s]+")


def one_line(text: str, limit: int = 200) -> str:
    """The first non-empty line, with secrets and home directories removed, cut to `limit` characters."""
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln:
            ln = HOME.sub("~", SECRET.sub("[redacted]", ln))
            ln = ln.replace("|", "\\|").replace("`", "'")
            return ln[:limit]
    return ""


def failed_jobs(jobs_doc: dict) -> list[dict]:
    """Failed jobs (not the aggregate `CI result` job, not cancelled skips), each with the failing step's name."""
    out = []
    for j in jobs_doc.get("jobs", []):
        if j.get("conclusion") not in BAD or j.get("name", "").startswith("CI result"):
            continue
        step = next((s.get("name", "") for s in j.get("steps", []) if s.get("conclusion") in BAD), "")
        out.append({"name": j.get("name", "?"), "url": j.get("html_url", ""), "step": step,
                    "conclusion": j.get("conclusion")})
    return out


def junit_failures(path: Path) -> list[dict]:
    out = []
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return out
    for case in root.iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is not None:
            msg = bad.get("message") or bad.text or ""
            out.append({"test": f"{case.get('classname', '')}::{case.get('name', '')}".strip(":"),
                        "error": one_line(msg)})
    return out


def known_flakes(artifacts: Path) -> set[str]:
    """Tests the flake report lists as passed-on-retry (known flakes)."""
    names: set[str] = set()
    for p in artifacts.rglob("flake-report.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        names.update(f.get("test", "") for f in doc.get("flaky_passed_on_retry", []))
    return names


def prepush_failures(artifacts: Path) -> list[dict]:
    """Failed checks from the quick gate's table (`prepush.out`): one entry per FAIL row."""
    out = []
    for p in sorted(artifacts.rglob("prepush.out")):
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^(\w+)\s+FAIL\s+[\d.]+s\s+(.*)$", ln)
            if m:
                out.append({"test": f"quick gate check {m.group(1)}", "error": one_line(m.group(2))})
    return out


def build(jobs_doc: dict, artifacts: Path, run_url: str) -> str:
    jobs = failed_jobs(jobs_doc)
    fails: list[dict] = []
    for p in sorted(artifacts.rglob("pytest-junit.xml")) if artifacts.is_dir() else []:
        fails.extend(junit_failures(p))
    fails = (prepush_failures(artifacts) if artifacts.is_dir() else []) + fails
    flakes = known_flakes(artifacts) if artifacts.is_dir() else set()
    lines = [MARKER, "### CI failed", ""]
    if not jobs:
        lines.append(f"No failed job was found in this run. [Run]({run_url})")
        return "\n".join(lines) + "\n"
    lines += ["| Job | Failed step | Log |", "|---|---|---|"]
    for j in jobs:
        name = one_line(j["name"], 120)
        lines.append(f"| {name} | {one_line(j['step'], 80) or '-'} | [log]({j['url'] or run_url}) |")
    lines.append("")
    if fails:
        first = fails[0]
        flake = " (known flake: passed on retry elsewhere, re-run first)" if first["test"] in flakes else ""
        lines.append(f"First failing test: `{one_line(first['test'], 160)}`{flake}")
        lines.append(f"First error line: `{first['error']}`" if first["error"] else "")
        if len(fails) > 1:
            lines.append(f"{len(fails) - 1} more failing test(s) in the logs.")
        marked = [f["test"] for f in fails if f["test"] in flakes]
        if marked:
            lines.append(f"Known flakes among the failures: {len(marked)}.")
    else:
        lines.append("No failing test in the junit files (the failure is outside pytest, see the log link).")
    lines += ["", "Run `python core/tools/prepush.py` locally before the next push. "
              f"[Whole run]({run_url})"]
    return "\n".join(x for x in lines if x is not None) + "\n"


def find_comment(comments: list[dict]) -> int | None:
    for c in comments:
        if MARKER in (c.get("body") or ""):
            return int(c["id"])
    return None


def post(repo: str, pr: int, body_file: Path) -> int:
    def gh(*args: str, stdin: str | None = None) -> str:
        return subprocess.run(["gh", "api", *args], input=stdin, capture_output=True, text=True, check=True).stdout
    comments = json.loads(gh(f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp") or "[]")
    flat = [c for page in comments for c in (page if isinstance(page, list) else [page])]
    cid = find_comment(flat)
    body = json.dumps({"body": body_file.read_text(encoding="utf-8")})
    if cid:
        gh(f"repos/{repo}/issues/comments/{cid}", "-X", "PATCH", "--input", "-", stdin=body)
    else:
        gh(f"repos/{repo}/issues/{pr}/comments", "-X", "POST", "--input", "-", stdin=body)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ci_comment.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--jobs", type=Path, required=True)
    b.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    b.add_argument("--run-url", default="")
    b.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("post")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--body", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        a.out.write_text(build(json.loads(a.jobs.read_text(encoding="utf-8")), a.artifacts, a.run_url),
                         encoding="utf-8")
        return 0
    return post(a.repo, a.pr, a.body)


if __name__ == "__main__":
    sys.exit(main())
