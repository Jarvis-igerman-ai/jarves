"""Stable release gate (docs/RELEASING.md). Fails (exit 1) unless a stable tag may be published:

  1. the tag is vX.Y.Z and the candidate installer being published is that version (X.Y.Z.<build>)
  2. the SHA being published is exactly the SHA that was verified (candidate commit; its jarves-checks run is green)
  3. the release checklist has no open P0-P2 (and every row done), and optional bug tables list none
  4. the nightly workflow was green on the last 5 scheduled nights
  5. the owner's manual test sign-off exists for this version (docs/reviews/MANUAL_TEST_<version>.md): every row pass
     or accepted-blocked with a note, signed, and the tested beta commit is the tag commit or an ancestor with only
     documentation changes since

    python tools/release_gate.py --tag v4.1.0 --sha <tag sha> --candidate-dir dist [--repo-root ..]
        [--checklist FILE] [--bug-tables 'docs/reviews/*BUG_TABLE*.md'] [--nightly-json F] [--checks-json F]

No GitHub token is needed to read a public repo's runs; GITHUB_TOKEN is used when set. The pure functions below take
plain data, so the tests need no network and no git.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jarves import release_signoff as so  # noqa: E402

TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
NIGHTS = 5
REPO = "igalgerman/jarves"
_NEG = re.compile(r"(?i)\b(?:no|zero|0)\s+open\s+P0(?:\s*(?:-|–|to)\s*P2)?\b")
_OPEN = re.compile(r"(?i)\bopen\b[^|\n]{0,80}?\bP[0-2]\b|\bP[0-2]\b[^|\n]{0,40}?\bopen\b")
_DONE = ("done", "n/a", "accepted", "pass", "passed", "fixed", "closed")


# ---------------------------------------------------------------------- pure checks
def check_tag(tag: str, build_version: str | None = None) -> tuple[str | None, list[str]]:
    m = TAG_RE.match(tag or "")
    if not m:
        return None, [f"tag {tag!r} is not a stable tag vX.Y.Z"]
    v = m.group(1)
    if build_version is not None and not re.fullmatch(rf"{re.escape(v)}\.\d+", build_version):
        return v, [f"the candidate installer is version {build_version}, not {v}.<build>"]
    return v, []


def check_sha(sha: str, verified: str | None) -> list[str]:
    if not verified:
        return ["no verified SHA given (--verified-sha or --candidate-dir with build-info.json)"]
    if sha.strip().lower() != verified.strip().lower():
        return [f"the SHA to publish {sha[:12]} differs from the verified SHA {verified[:12]}"]
    return []


def checks_green(runs: list[dict], sha: str) -> list[str]:
    """`runs` = workflow runs of jarves-checks (API shape). Needs a push run for exactly this SHA that succeeded."""
    ok = [r for r in runs if str(r.get("head_sha", "")).lower() == sha.lower() and r.get("conclusion") == "success"
          and r.get("event", "push") == "push"]
    return [] if ok else [f"no green jarves-checks run for {sha[:12]}"]


def open_p0_p2(text: str) -> list[str]:
    """Lines of a checklist / bug table that say a P0-P2 is still open, and checklist rows that are not done."""
    problems: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        stripped = _NEG.sub("", line)
        if _OPEN.search(stripped):
            problems.append(f"line {n}: open P0-P2: {line.strip()[:110]}")
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if len(cells) >= 3 and re.fullmatch(r"\d+", cells[0]):                       # checklist row: # | item | status
            status = cells[2].lower()
            if not status.startswith(_DONE):
                problems.append(f"checklist row {cells[0]} is '{cells[2]}': {cells[1][:70]}")
    return problems


_SEP_ROW = re.compile(r"^\|?(\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?$|^\|?\s*:?-{3,}:?\s*\|?$")
_SEV_COL = re.compile(r"(?i)^(sev|severity|priority|prio)$")
_STATUS_COL = re.compile(r"(?i)^(status|state)$")
_RETEST_COL = re.compile(r"(?i)^(retest|re-test|result)$")       # tables that record the verification instead of a status
_CLOSED = re.compile(r"(?i)^\W*(fixed|closed|accepted|wontfix|won't fix|duplicate|done|resolved|verified|passed?\b"
                     r"|not a bug|obsolete|superseded)")
_NOT_DONE = re.compile(r"(?i)\b(open|fail(ed|s)?|pending|todo|not yet|blocked|regress(ed|ion)?)\b")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def bug_table_open(text: str, name: str = "") -> list[str]:
    """Rows with severity P0-P2 that are not closed, in every markdown bug table of `text`.

    Columns are found by the table's header row (`Sev`/`Severity`/`Priority` and `Status`/`State`; tables that record a
    `Retest`/`Result` instead of a status count a non-empty, non-negative retest as closed). The bug tables in
    docs/reviews use different column orders, so a fixed position read the area or the symptom as the status and
    reported fixed bugs as open. Tables without a severity column (legends, result tables) are not bug tables.
    Rows without any header keep the original `| id | sev | status |` order."""
    out: list[str] = []
    lines = text.splitlines()
    sev_i: int | None = 1
    st_i: int | None = 2
    retest = False
    where = f"{name}: " if name else ""
    for n, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            sev_i, st_i, retest = 1, 2, False               # a new table starts with its own header
            continue
        if _SEP_ROW.match(line.strip()):
            continue
        cells = _cells(line)
        if n + 1 < len(lines) and _SEP_ROW.match(lines[n + 1].strip()):        # header row
            heads = [c.strip("*` ").lower() for c in cells]
            sev_i = next((k for k, h in enumerate(heads) if _SEV_COL.match(h)), None)
            st_i = next((k for k, h in enumerate(heads) if _STATUS_COL.match(h)), None)
            retest = st_i is None
            if retest:
                st_i = next((k for k, h in enumerate(heads) if _RETEST_COL.match(h)), None)
            continue
        if sev_i is None or sev_i >= len(cells) or not re.match(r"P[0-2]\b", cells[sev_i]):
            continue
        bug = f"{where}bug {cells[0]} ({cells[sev_i]})"
        if st_i is None or st_i >= len(cells):
            out.append(f"{bug} has no status column to show it is closed")
            continue
        status = cells[st_i]
        closed = (bool(status) and not _NOT_DONE.search(status)) if retest else bool(_CLOSED.match(status))
        if not closed:
            out.append(f"{bug} is {status[:80]!r}")
    return out


def _when(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def nightly_problems(runs: list[dict], nights: int = NIGHTS, now: datetime | None = None) -> list[str]:
    """The last `nights` completed scheduled nightly runs must all be green, on consecutive UTC days, the newest not
    older than two days. Manual (workflow_dispatch) runs do not count as nights."""
    now = now or datetime.now(timezone.utc)
    sched = sorted((r for r in runs if r.get("event", "schedule") == "schedule" and r.get("status", "completed") == "completed"),
                   key=lambda r: _when(r["created_at"]), reverse=True)
    if len(sched) < nights:
        return [f"only {len(sched)} scheduled nightly run(s) found, {nights} green nights are required"]
    last = sched[:nights]
    bad = [f"{_when(r['created_at']).date()} {r.get('conclusion')}" for r in last if r.get("conclusion") != "success"]
    if bad:
        return [f"nightly not green: {', '.join(bad)}"]
    days = [_when(r["created_at"]).date() for r in last]
    if len(set(days)) != nights or any((days[i] - days[i + 1]) != timedelta(days=1) for i in range(nights - 1)):
        return [f"the last {nights} nightly runs are not {nights} consecutive nights ({', '.join(map(str, days))})"]
    if now - _when(last[0]["created_at"]) > timedelta(days=2):
        return [f"the newest green nightly is from {days[0]}; wait for a fresh one"]
    return []


def signoff_problems(root: Path, version: str, tag_sha: str, is_ancestor, changed_files) -> list[str]:
    try:
        sheet = so.load(root, version)
    except so.SheetError as e:
        return [str(e)]
    probs = [f"sign-off: {p}" for p in sheet.problems()]
    if sheet.commit and so.SHA_RE.match(sheet.commit):
        why = so.tested_sha_ok(sheet.commit, tag_sha, is_ancestor, changed_files)
        if why:
            probs.append(f"sign-off: {why}")
    return probs


# ---------------------------------------------------------------------- git + GitHub (not used by the tests)
def git_is_ancestor(root: Path):
    def f(a: str, b: str) -> bool:
        return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", a, b], capture_output=True).returncode == 0
    return f


def git_changed(root: Path):
    def f(a: str, b: str) -> list[str]:
        r = subprocess.run(["git", "-C", str(root), "diff", "--name-only", f"{a}..{b}"], capture_output=True, text=True, check=True)
        return [x for x in r.stdout.splitlines() if x]
    return f


def gh_runs(repo: str, workflow: str, query: str) -> list[dict]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "jarves-release-gate"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs?per_page=30&{query}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.loads(r.read())["workflow_runs"]


def find_checklist(root: Path, version: str, explicit: str | None) -> Path | None:
    """The checklist of THIS version. SWV4_RELEASE_CHECKLIST.md is the 4.0.0 checklist: a later version used to fall
    back to it and inherit its stale 'needs-owner' rows instead of being told its own checklist is missing."""
    reviews = root / "docs" / "reviews"
    cands = [Path(explicit)] if explicit else [reviews / f"RELEASE_CHECKLIST_{version}.md"]
    if not explicit and version == "4.0.0":
        cands.append(reviews / "SWV4_RELEASE_CHECKLIST.md")
    return next((c for c in cands if c.is_file()), None)


# ---------------------------------------------------------------------- the gate
def run_gate(*, tag: str, sha: str, verified_sha: str | None, build_version: str | None, root: Path,
             checklist_text: str | None, bug_tables: list, nightly_runs: list[dict], checks_runs: list[dict],
             is_ancestor, changed_files, now: datetime | None = None) -> list[str]:
    version, problems = check_tag(tag, build_version)
    problems += check_sha(sha, verified_sha)
    if verified_sha:
        problems += checks_green(checks_runs, verified_sha)
    if checklist_text is None:
        problems.append(f"release checklist not found (docs/reviews/RELEASE_CHECKLIST_{version or '<version>'}.md)")
    else:
        problems += open_p0_p2(checklist_text)
    for t in bug_tables:                                   # table text, or (file name, table text)
        problems += bug_table_open(t[1], t[0]) if isinstance(t, tuple) else bug_table_open(t)
    problems += nightly_problems(nightly_runs, now=now)
    if version:
        problems += signoff_problems(root, version, sha, is_ancestor, changed_files)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release_gate.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--sha", required=True, help="commit the tag points to")
    ap.add_argument("--verified-sha")
    ap.add_argument("--candidate-dir", type=Path, help="dist folder with build-info.json of the candidate")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--checklist")
    ap.add_argument("--bug-tables", default="", help="glob of bug tables whose open P0-P2 rows also block")
    ap.add_argument("--nightly-json", type=Path)
    ap.add_argument("--checks-json", type=Path)
    ap.add_argument("--repo", default=REPO)
    a = ap.parse_args(argv)
    verified, build_version = a.verified_sha, None
    if a.candidate_dir:
        info = json.loads((a.candidate_dir / "build-info.json").read_text(encoding="utf-8-sig"))
        verified, build_version = verified or info.get("commit"), info.get("version")
    root = a.repo_root.resolve()
    version = (TAG_RE.match(a.tag) or [None, "0.0.0"])[1]
    cl = find_checklist(root, version, a.checklist)
    tables = [(Path(p).name, Path(p).read_text(encoding="utf-8"))
              for p in sorted(glob.glob(str(root / a.bug_tables)))] if a.bug_tables else []
    load = lambda p, fn: json.loads(p.read_text(encoding="utf-8")) if p else fn()   # noqa: E731
    nightly = load(a.nightly_json, lambda: gh_runs(a.repo, "nightly.yml", "branch=main&status=completed"))
    checks = load(a.checks_json, lambda: gh_runs(a.repo, "jarves-checks.yml", "branch=main&event=push&status=completed"))
    problems = run_gate(tag=a.tag, sha=a.sha, verified_sha=verified, build_version=build_version, root=root,
                        checklist_text=cl.read_text(encoding="utf-8") if cl else None, bug_tables=tables,
                        nightly_runs=nightly, checks_runs=checks, is_ancestor=git_is_ancestor(root),
                        changed_files=git_changed(root))
    if problems:
        print(f"RELEASE GATE FAILED for {a.tag} ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"RELEASE GATE PASSED for {a.tag} at {a.sha[:12]}: SHA verified, checklist clear, {NIGHTS} green nights, "
          "manual sign-off complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
