"""Find flaky tests in the jarves-checks run history and open ONE `showstopper` issue per test (owner rule 24 Oct 2026,
CLAUDE.md "A flaky test is a show-stopper", issue #160).

    python core/tools/flake_scan.py [--days 7] [--repo Jarvis-igerman-ai/jarves]            # print flakes (read-only)
    python core/tools/flake_scan.py --issues                                         # also open/update the issues

A failure counts as a flake when the SAME commit later passed: either a re-run attempt of the run turned green, or
another jarves-checks run on the same head SHA succeeded (nothing in the code changed, so the failure was not the code).
Failing test names come from the failed jobs' logs (pytest's "____ test_name ____" header plus the "tests/x.py:N:"
line under it). Each flake gets an issue "Flaky test: <file>::<test>" (labels showstopper, P1, ci) with the failed run,
the suspect PR and one log line; a later flake of the same test adds a comment, never a second issue. Quarantine itself
is a one-line entry in core/tests/quarantine.toml (the issue says which). Tests already in the ledger are reported as
"quarantined". Uses only `gh` (GITHUB_TOKEN with actions: read, issues: write) and the standard library.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[1] / "tests" / "quarantine.toml"
HEADER = re.compile(r"_{3,} (?:[\w.]+\.)?(test_\w+)(?:\[[^\]]*\])? _{3,}")
LOCATION = re.compile(r"tests[\\/]+(test_\w+\.py):\d+: (\w+)")
TIMEOUT_FRAME = re.compile(r"tests[\\/]+(test_\w+\.py)\", line \d+ in (test_\w+)")
PREPUSH_FAIL = re.compile(r"^\s*\w+\s+FAIL\s+[\d.]+s\s+tests[\\/]+(test_\w+\.py)::(test_\w+)")
STAMP = re.compile(r"^[^\t]*\t[^\t]*\t\S+Z ")   # "<job name with spaces>\t<step>\t<timestamp> "
TITLE = "Flaky test: "
# The repeated DST hour (Israel clocks went back 25 Oct 2026): clock-dependent tests failed for every PR, not as flakes.
IGNORE_WINDOWS = [("2026-10-24T23:00:00Z", "2026-10-25T00:00:00Z")]


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def failed_tests(log: str) -> dict[str, str]:
    """'<file>::<test>' -> first error line, from a `gh run view --log-failed` / job log text."""
    out: dict[str, str] = {}
    lines = [STAMP.sub("", ln) for ln in log.splitlines()]
    current = None
    for ln in lines:
        h = HEADER.search(ln)
        if h:
            current = h.group(1)
            continue
        loc = LOCATION.search(ln)
        if loc and current:
            out.setdefault(f"{loc.group(1)}::{current}", ln.strip()[:200])
            current = None
        gate = PREPUSH_FAIL.search(ln)            # the quick gate's table: "perf  FAIL  3.2s  tests/x.py::test_y: ..."
        if gate:
            out.setdefault(f"{gate.group(1)}::{gate.group(2)}", ln.strip()[:200])
    if not out:                                   # a hang: pytest-timeout dumps the stack, the test frame names it
        for ln in lines:
            t = TIMEOUT_FRAME.search(ln)
            if t:
                out.setdefault(f"{t.group(1)}::{t.group(2)}", "hung: pytest-timeout stack dump")
    return out


def flaky_runs(runs: list[dict]) -> list[tuple[dict, str]]:
    """(failed run, evidence) where the same commit later passed. `runs` = Actions API workflow_runs."""
    green = {r["head_sha"]: r for r in runs if r.get("conclusion") == "success"}
    out = []
    for r in runs:
        if r.get("conclusion") == "failure" and r["head_sha"] in green:
            out.append((r, f"same commit green in run {green[r['head_sha']]['id']}"))
        elif r.get("conclusion") == "success" and int(r.get("run_attempt") or 1) > 1:
            out.append((r, f"re-run attempt {r['run_attempt']} green"))
    return out


def ledger_ids(path: Path = LEDGER) -> set[str]:
    if not path.exists():
        return set()
    return {str(e.get("id", "")) for e in tomllib.loads(path.read_text(encoding="utf-8")).get("test", [])}


def _runs(repo: str, days: int) -> list[dict]:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    raw = gh("api", "--paginate", f"repos/{repo}/actions/workflows/jarves-checks.yml/runs?created=>={since}&per_page=100",
             "--jq", ".workflow_runs[]|{id,head_sha,head_branch,event,conclusion,run_attempt,html_url,run_started_at,"
                     "pull_requests:[.pull_requests[].number]}")
    return [json.loads(x) for x in raw.splitlines() if x.strip()]


def _log(repo: str, run: dict) -> str:
    args = ["run", "view", str(run["id"]), "-R", repo, "--log-failed"]
    if run.get("conclusion") == "success":            # the failed attempt is an earlier one
        args += ["--attempt", str(int(run["run_attempt"]) - 1)]
    try:
        return gh(*args)
    except subprocess.CalledProcessError:
        return ""                                     # logs expired or not accessible: nothing to report


def in_ignored_window(started: str) -> bool:
    return any(a <= started <= b for a, b in IGNORE_WINDOWS)


def last_fix_time(repo: str, test: str) -> str:
    """ISO time of the newest commit on main touching the test's file ('' if unknown). A failure that started BEFORE it
    cannot be told apart from the bug that commit fixed, so it is not reported again (false positives of 25 Oct 2026)."""
    path = "core/tests/" + test.split("::")[0]
    try:
        out = gh("api", f"repos/{repo}/commits?path={path}&per_page=1", "--jq", ".[0].commit.committer.date")
    except subprocess.CalledProcessError:
        return ""
    return out.strip()


def drop_stale(found: dict[str, list[dict]], fix_time) -> dict[str, list[dict]]:
    """Keep only failures that started after the last commit touching the test file and outside ignored windows."""
    out: dict[str, list[dict]] = {}
    for test, hits in found.items():
        fix = fix_time(test)
        keep = [h for h in hits if not in_ignored_window(h.get("started", "")) and (not fix or h.get("started", "") > fix)]
        if keep:
            out[test] = keep
    return out


def scan(repo: str, days: int) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for run, why in flaky_runs(_runs(repo, days)):
        for test, line in failed_tests(_log(repo, run)).items():
            found.setdefault(test, []).append({"run": run["id"], "url": run.get("html_url", ""), "why": why,
                                               "branch": run.get("head_branch", ""), "started": run.get("run_started_at") or "", "prs": run.get("pull_requests", []),
                                               "line": line})
    cache: dict[str, str] = {}
    return drop_stale(found, lambda t: cache.setdefault(t.split("::")[0], last_fix_time(repo, t)))


def issue_body(test: str, hits: list[dict]) -> str:
    rows = "\n".join(f"- run {h['run']} ({h['branch']}, PR {', '.join('#%s' % p for p in h['prs']) or '-'}): {h['why']}\n"
                     f"  `{h['line'].replace('`', '')}`" for h in hits)
    file_, name = test.split("::", 1)
    return (f"Show-stopper flake (CLAUDE.md rule, #160). `{test}` failed and the same commit then passed:\n\n{rows}\n\n"
            "Pause it now: add to `core/tests/quarantine.toml`\n\n```toml\n[[test]]\n"
            f'id = "{test}"\nissue = <this issue>\nsince = "{dt.date.today().isoformat()}"\nreason = "..."\n```\n\n'
            f"Then fix `{file_}` (`{name}`), delete the entry and close this issue in the same PR. "
            "Opened by core/tools/flake_scan.py.")


def report(repo: str, found: dict[str, list[dict]], issues: bool) -> int:
    quarantined = ledger_ids()
    for test, hits in sorted(found.items()):
        state = "quarantined" if test in quarantined else "NOT quarantined"
        print(f"FLAKE {test}: {len(hits)} run(s), {state}")
        if not issues:
            continue
        title = TITLE + test
        known = json.loads(gh("issue", "list", "-R", repo, "--state", "open", "--search", f"\"{title}\" in:title",
                              "--json", "number,title,body"))
        mine = [i for i in known if i["title"] == title]
        if mine:
            n = mine[0]["number"]
            seen = gh("issue", "view", str(n), "-R", repo, "--comments", "--json", "body,comments")
            new = [h for h in hits if str(h["run"]) not in seen]
            if new:
                gh("issue", "comment", str(n), "-R", repo, "--body",
                   "Flaked again:\n" + "\n".join(f"- run {h['run']} ({h['branch']}): {h['why']}" for h in new))
        else:
            gh("issue", "create", "-R", repo, "--title", title, "--label", "showstopper", "--label", "P1",
               "--label", "ci", "--body", issue_body(test, hits))
    if not found:
        print("no flakes in the window")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="Jarvis-igerman-ai/jarves")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--issues", action="store_true", help="open or update one showstopper issue per flaky test")
    ap.add_argument("--ledger-files", action="store_true",
                    help="print the test files of the quarantined tests (tests/x.py ...) for the non-blocking CI step")
    a = ap.parse_args(argv)
    if a.ledger_files:
        print(" ".join(sorted({f"tests/{t.split('::')[0]}" for t in ledger_ids() if "::" in t})))
        return 0
    return report(a.repo, scan(a.repo, a.days), a.issues)


if __name__ == "__main__":
    sys.exit(main())
