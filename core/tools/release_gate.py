"""Stable release gate (docs/RELEASING.md). Fails (exit 1) unless a stable tag may be published:

  1. the tag is vX.Y.Z and the candidate installer being published is that version (X.Y.Z.<build>)
  2. the SHA being published is exactly the SHA that was verified (candidate commit; its jarves-checks run is green)
  3. (retired) the legacy bug tables and release checklist rows are printed as information ("legacy, not gating"); the
     live GitHub P0-P2 issues (item 4) are the single source of truth (owner 26 Oct 2026, simple-releases OD-S2)
  4. no open GitHub issue labelled P0-P2. Planned work (label enhancement, v5-v9 or 4.5.1) is not a defect and is
     skipped ONLY when the issue is not also labelled bug.
     An open P0-P2 labelled `known-issue-4.5` (owner-only label) is an accepted known issue: not a blocker, printed in the report
  5. the owner's manual test sign-off exists for this version (docs/reviews/MANUAL_TEST_<version>.md): every row pass
     or accepted-blocked with a note, signed, and the tested beta commit is the tag commit or an ancestor with only
     documentation changes since (docs/, docs/release/, root *.md; git failures refuse). The installer commit must equal
     the sheet's commit; the tag commit is NOT required to equal it (#539: the tag commit contains the signed sheet). This ONE signature is the owner's release signature (owner 26 Oct 2026: no nightly
     requirement, no second SIGNED file)

Main green is item 2 (the jarves-checks run of the tagged SHA); the dry run is this tool without a tag push.

    python tools/release_gate.py --tag v4.1.0 --sha <tag sha> --candidate-dir dist [--repo-root ..]
        [--checklist FILE] [--bug-tables GLOB (accepted, non-gating)] [--checks-json F] [--issues-json F]

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
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jarves import release_flow as rf  # noqa: E402
from jarves import release_signoff as so  # noqa: E402

TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
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


def check_sha(installer: str | None, verified: str | None) -> list[str]:
    """The commit the published installer was built from must be the verified (signed-off) commit. The TAG commit is
    judged separately by the sign-off check (equal to it, or a docs-only descendant): #539."""
    if not verified:
        return ["no verified SHA given (--verified-sha or --candidate-dir with build-info.json)"]
    if installer is not None and installer.strip().lower() != verified.strip().lower():
        return [f"the installer commit {installer[:12]} differs from the verified SHA {verified[:12]}"]
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


ACCEPTED_LABEL = rf.ACCEPTED_LABEL   # OWNER-ONLY label: the owner accepts an open P0-P2 as a known issue for the 4.5 stable


def issue_problems(issues: list[dict]) -> list[str]:
    """Open GitHub issues labelled P0-P2 that block (shared rule: jarves.release_flow.classify_issues, #535)."""
    return [f"open {sev} issue #{n}: {title}" for n, sev, title in rf.classify_issues(issues)[0]]


def accepted_issues(issues: list[dict]) -> list[str]:
    """Open P0-P2 issues the owner accepted for this stable (label `known-issue-4.5`); printed, never blocking."""
    return [f"#{n} ({sev}): {title}" for n, sev, title in rf.classify_issues(issues)[1]]


def legacy_report(checklist_text: str | None, bug_tables: list) -> list[str]:
    """INFORMATION only (retired from the gate): the old checklist rows and bug-table rows that still read as open."""
    out: list[str] = []
    if checklist_text is None:
        out.append("release checklist: none found")
    else:
        out += [f"checklist: {p}" for p in open_p0_p2(checklist_text)]
    for t in bug_tables:
        out += bug_table_open(t[1], t[0]) if isinstance(t, tuple) else bug_table_open(t)
    return out


HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def signoff_problems(root: Path, version: str, tag_sha: str, is_ancestor, changed_files,
                     verified: str | None = None, expect_rows_hash: str | None = None) -> list[str]:
    try:
        sheet = so.load(root, version)
    except so.SheetError as e:
        return [str(e)]
    probs = [f"sign-off: {p}" for p in sheet.problems()]
    # #605: the rows hash lives in the sheet, so whoever edits the sheet can recompute it. A value the owner recorded OUTSIDE
    # the sheet (the annotation of the annotated tag, printed by `jarvis release signoff --finish`) must match it.
    if expect_rows_hash is not None:
        want = expect_rows_hash.strip().lower()
        if not HASH_RE.match(want):
            probs.append("sign-off: the out-of-band rows hash is not a sha256 hex value")
        elif sheet.rows_hash.strip().lower() != want:
            probs.append("sign-off: the sheet rows hash differs from the hash recorded outside the sheet (tag annotation); "
                         "the sheet was changed after the owner signed")
    if verified and sheet.commit and sheet.commit != verified.strip().lower():
        probs.append(f"sign-off: the sheet commit {sheet.commit[:12]} differs from the verified SHA {verified[:12]}")
    if sheet.commit and so.SHA_RE.match(sheet.commit):
        try:
            why = so.tested_sha_ok(sheet.commit, tag_sha, is_ancestor, changed_files)
        except Exception as e:                      # shallow clone, unknown sha, git failed: refuse, never pass blind
            why = f"cannot compare the tested commit with the tag commit ({type(e).__name__}); fetch full history"
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
        # --no-renames: a move out of code (core/x.py -> docs/x.py) must list the old path too, not only docs/x.py
        r = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=off", "diff", "--no-renames", "--no-ext-diff",
                            "--name-only", f"{a}..{b}"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", check=True)
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
             checklist_text: str | None, bug_tables: list, checks_runs: list[dict],
             is_ancestor, changed_files, now: datetime | None = None, issues: list[dict] | None = None,
             installer_sha: str | None = None, expect_rows_hash: str | None = None) -> list[str]:
    version, problems = check_tag(tag, build_version)
    problems += check_sha(installer_sha, verified_sha)
    if verified_sha:
        problems += checks_green(checks_runs, verified_sha)
    # checklist_text / bug_tables are accepted for compatibility but no longer gate (see legacy_report)
    problems += issue_problems(issues or [])
    if version:
        problems += signoff_problems(root, version, sha, is_ancestor, changed_files, verified_sha, expect_rows_hash)
    return problems


def _print_accepted(accepted: list[str]) -> None:
    if accepted:
        print(f"accepted known issues ({len(accepted)}, label {ACCEPTED_LABEL}, owner decision):")
        for a in accepted:
            print(f"  - {a}")


def _print_legacy(legacy: list[str]) -> None:
    print(f"legacy, not gating ({len(legacy)} line(s); bug tables and checklist are retired, live issues decide):")
    for x in legacy:
        print(f"  - {x}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release_gate.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--sha", required=True, help="commit the tag points to")
    ap.add_argument("--verified-sha")
    ap.add_argument("--candidate-dir", type=Path, help="dist folder with build-info.json of the candidate")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--checklist")
    ap.add_argument("--bug-tables", default="", help="accepted for compatibility; legacy bug tables are printed, never gating")
    ap.add_argument("--checks-json", type=Path)
    ap.add_argument("--issues-json", type=Path, help="open issues (API shape) instead of asking GitHub")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--expect-rows-hash", default=os.environ.get("JARVES_EXPECT_ROWS_HASH") or None,
                    help="sha256 the owner recorded outside the sheet (annotated tag message line 'Rows hash: <sha256>'); "
                         "refuse when it differs from the sheet (#605)")
    a = ap.parse_args(argv)
    verified, build_version, installer = a.verified_sha, None, None
    if a.candidate_dir:
        info = json.loads((a.candidate_dir / "build-info.json").read_text(encoding="utf-8-sig"))
        verified, build_version, installer = verified or info.get("commit"), info.get("version"), info.get("commit") or ""
    root = a.repo_root.resolve()
    version = (TAG_RE.match(a.tag) or [None, "0.0.0"])[1]
    if a.bug_tables:
        print("warning: --bug-tables no longer gates (retired, legacy information only); live P0-P2 issues decide")
    cl = find_checklist(root, version, a.checklist)
    tables = [(Path(p).name, Path(p).read_text(encoding="utf-8"))
              for p in sorted(glob.glob(str(root / (a.bug_tables or "docs/reviews/*BUG_TABLE*.md"))))]
    load = lambda p, fn: json.loads(p.read_text(encoding="utf-8")) if p else fn()   # noqa: E731
    checks = load(a.checks_json, lambda: gh_runs(a.repo, "jarves-checks.yml", "branch=main&event=push&status=completed"))
    try:
        issues = load(a.issues_json, lambda: gh_issues(a.repo))
    except Exception as e:                                  # cannot read the issues: refuse, never pass blind
        print(f"RELEASE GATE FAILED for {a.tag}: cannot read open issues from GitHub ({type(e).__name__}: {e})")
        return 1
    accepted = accepted_issues(issues)
    legacy = legacy_report(cl.read_text(encoding="utf-8") if cl else None, tables)
    problems = run_gate(tag=a.tag, sha=a.sha, verified_sha=verified, build_version=build_version, root=root,
                        checklist_text=cl.read_text(encoding="utf-8") if cl else None, bug_tables=tables,
                        checks_runs=checks, is_ancestor=git_is_ancestor(root),
                        changed_files=git_changed(root), issues=issues, installer_sha=installer,
                        expect_rows_hash=a.expect_rows_hash)
    if a.expect_rows_hash is None:
        print("note: no out-of-band rows hash given (annotated tag message 'Rows hash: <sha256>'); the sheet hash alone "
              "only catches accidental edits (#605)")
    if problems:
        print(f"RELEASE GATE FAILED for {a.tag} ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        _print_accepted(accepted)
        _print_legacy(legacy)
        return 1
    print(f"RELEASE GATE PASSED for {a.tag} at {a.sha[:12]}: SHA verified, "
          "no open P0-P2, manual sign-off complete and signed")
    _print_accepted(accepted)
    _print_legacy(legacy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
