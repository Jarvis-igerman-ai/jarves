"""Stable release gate (docs/RELEASING.md). Fails (exit 1) unless a stable tag may be published:

  1. the tag is vX.Y.Z and the candidate installer being published is that version (X.Y.Z.<build>)
  2. the SHA being published is exactly the SHA that was verified (candidate commit; its jarves-checks run is green)
  3. the release checklist has no open P0-P2 (and every row done), and optional bug tables list none
  4. the LATEST completed nightly run on main is green and not older than 2 days (the old "5 green nights in a row"
     rule is gone, owner 26 Oct 2026; the nightly report stays as information)
  5. no open GitHub issue labelled P0-P2 (planned work labelled enhancement or v5-v9 is not a defect and is skipped)
  6. the owner's manual test sign-off exists for this version (docs/reviews/MANUAL_TEST_<version>.md): every row pass
     or accepted-blocked with a note, signed, and the tested beta commit is the tag commit or an ancestor with only
     documentation changes since
  7. the owner's `signed` file docs/release/SIGNED_<version> exists (owner, date, commit lines; see
     docs/release/SIGN_OFF_<version>.md); without it the gate refuses the tag

Main green is item 2 (the jarves-checks run of the tagged SHA); the dry run is this tool without a tag push.

    python tools/release_gate.py --tag v4.1.0 --sha <tag sha> --candidate-dir dist [--repo-root ..]
        [--checklist FILE] [--bug-tables 'docs/reviews/*BUG_TABLE*.md'] [--nightly-json F] [--checks-json F] [--issues-json F]

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
NIGHT_MAX_AGE = timedelta(days=2)
SIGNED_RE = {k: re.compile(rf"(?im)^\s*{k}\s*:\s*(\S.*)$") for k in ("owner", "date", "commit")}
REPO = "Jarvis-igerman-ai/jarves"
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


def nightly_problems(runs: list[dict], now: datetime | None = None) -> list[str]:
    """The newest completed nightly run must be green and not older than two days. One run is enough: a red night
    earlier is history, a fresh green one proves main is healthy now. In-progress runs are ignored."""
    now = now or datetime.now(timezone.utc)
    done = sorted((r for r in runs if r.get("status", "completed") == "completed"),
                  key=lambda r: _when(r["created_at"]), reverse=True)
    if not done:
        return ["no completed nightly run found; run the nightly workflow on main"]
    last = done[0]
    day = _when(last["created_at"]).date()
    if last.get("conclusion") != "success":
        return [f"the latest nightly is not green: {day} {last.get('conclusion')}"]
    if now - _when(last["created_at"]) > NIGHT_MAX_AGE:
        return [f"the latest green nightly is from {day}; run a fresh one"]
    return []


_LATER = re.compile(r"(?i)^(enhancement|v[5-9])$")


def issue_problems(issues: list[dict]) -> list[str]:
    """Open GitHub issues labelled P0-P2 (API shape). Planned work (label enhancement or v5..v9) is not a defect."""
    out = []
    for i in issues:
        if "pull_request" in i or str(i.get("state", "open")) != "open":
            continue
        labels = [str(l.get("name", "")) if isinstance(l, dict) else str(l) for l in i.get("labels", [])]
        sev = next((l.upper() for l in labels if re.fullmatch(r"(?i)P[0-2]", l)), None)
        if sev and not any(_LATER.match(l) for l in labels):
            out.append(f"open {sev} issue #{i.get('number')}: {str(i.get('title', ''))[:70]}")
    return out


def signed_problems(root: Path, version: str, tag_sha: str, is_ancestor, changed_files) -> list[str]:
    """docs/release/SIGNED_<version>: three lines `owner:`, `date:`, `commit:` (the tested commit). The owner creates it
    after ticking docs/release/SIGN_OFF_<version>.md. Any change to the sheet later needs a new signature."""
    f = root / "docs" / "release" / f"SIGNED_{version}"
    if not f.is_file():
        return [f"owner signature missing: docs/release/SIGNED_{version} (owner:, date:, commit: lines)"]
    text = f.read_text(encoding="utf-8-sig")
    got = {}
    for k, rx in SIGNED_RE.items():
        m = rx.search(text)
        got[k] = m.group(1).strip() if m else ""
    probs = [f"signed file: '{k}:' line is missing" for k, v in got.items() if not v]
    if got["commit"]:
        if not so.SHA_RE.match(got["commit"]):
            probs.append("signed file: 'commit:' is not a full SHA")
        else:
            why = so.tested_sha_ok(got["commit"], tag_sha, is_ancestor, changed_files)
            if why:
                probs.append(f"signed file: {why}")
    return probs


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


def gh_issues(repo: str) -> list[dict]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "jarves-release-gate"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    out: list[dict] = []
    for label in ("P0", "P1", "P2"):
        for page in range(1, 11):
            url = f"https://api.github.com/repos/{repo}/issues?state=open&labels={label}&per_page=100&page={page}"
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                chunk = json.loads(r.read())
            out += chunk
            if len(chunk) < 100:
                break
    return out


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
             is_ancestor, changed_files, now: datetime | None = None, issues: list[dict] | None = None) -> list[str]:
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
    problems += issue_problems(issues or [])
    if version:
        problems += signoff_problems(root, version, sha, is_ancestor, changed_files)
        problems += signed_problems(root, version, sha, is_ancestor, changed_files)
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
    ap.add_argument("--issues-json", type=Path, help="open issues (API shape) instead of asking GitHub")
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
    issues = load(a.issues_json, lambda: gh_issues(a.repo))
    problems = run_gate(tag=a.tag, sha=a.sha, verified_sha=verified, build_version=build_version, root=root,
                        checklist_text=cl.read_text(encoding="utf-8") if cl else None, bug_tables=tables,
                        nightly_runs=nightly, checks_runs=checks, is_ancestor=git_is_ancestor(root),
                        changed_files=git_changed(root), issues=issues)
    if problems:
        print(f"RELEASE GATE FAILED for {a.tag} ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"RELEASE GATE PASSED for {a.tag} at {a.sha[:12]}: SHA verified, checklist clear, latest nightly green, "
          "no open P0-P2, manual sign-off complete and signed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
