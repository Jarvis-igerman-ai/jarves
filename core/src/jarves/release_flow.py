"""One simple release flow (docs/plans/SIMPLE_RELEASES_2026-10-26.md): `jarvis release status` and `jarvis release sheet`.

`status` runs every automatable gate (main green, open P0/P1 issues from live labels, the owner
sheet: its one signature) and says exactly what remains for the owner. `sheet` writes the reduced owner sheet: a tiny smoke
set stays `pending`, every other row is recorded `accepted-blocked` with an "accepted by automation" note naming its
evidence, so the existing gate (release_gate.py) and sign-off file format work unchanged. Pure functions; the network is
injected (`fetch`), so the tests need none. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import release_signoff as so

REPO = "Jarvis-igerman-ai/jarves"
ACCEPTED_LABEL = "known-issue-4.5"   # OWNER-ONLY label: the owner accepts an open P1 as a known issue for the 4.5 stable
AUTOMATION_NOTE = "accepted by automation: {ev}"

SMOKE = (
    ("S1", "Update to the newest beta (`jarvis update --channel beta`), then `jarvis doctor` shows no red line",
     "Doctor is clean"),
    ("S2", "Say \"היי ג'רוויס, מה השעה?\"", "Wakes and answers in Hebrew with the right time"),
    ("S3", "Interrupt an answer in the middle", "It stops fast and listens"),
    ("S4", "Trigger a medium-risk action by voice and say \"כן\"",
     "Popup on top, approved once, no echo self-approval"),
    ("S5", "`jarvis update --rollback`, then update forward again", "Data kept both ways"),
)
SMOKE_IDS = tuple(s[0] for s in SMOKE)
EVIDENCE = ("CI jarves-checks on main (tests, guides, CLI parity, dashboard in a real browser, installer install/upgrade/"
            "uninstall smoke)")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


# ---------------------------------------------------------------------- pure checks
def _when(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def check_main(runs: list[dict], head: str | None) -> Check:
    push = [r for r in runs if r.get("event", "push") == "push" and r.get("status", "completed") == "completed"]
    if head:
        push = [r for r in push if str(r.get("head_sha", "")).lower() == head.lower()]
    if not push:
        return Check("main green", False, "no completed jarves-checks run for the head of main")
    last = max(push, key=lambda r: _when(r["created_at"]))
    ok = last.get("conclusion") == "success"
    return Check("main green", ok, f"jarves-checks {str(last.get('head_sha', ''))[:10]}: {last.get('conclusion')}")


def _label_names(issue: dict) -> list[str]:
    return [(str(l.get("name", "")) if isinstance(l, dict) else str(l)).strip() for l in issue.get("labels", [])]


def _severity(issue: dict) -> str | None:
    """Highest P-label (P0 > P1 > P2 > P3) of an issue, or None when it has none."""
    sevs = [int(l[1]) for l in _label_names(issue) if re.fullmatch(r"(?i)P[0-3]", l)]
    return f"P{min(sevs)}" if sevs else None


# P1s that never block a stable (owner rule 3 Nov 2026): test flakes and developer tooling, not a user scenario.
# Owner-only labels, like ACCEPTED_LABEL. A P1 that also carries one of NEVER_EXEMPT_LABELS still blocks.
NON_BLOCKING_P1_LABELS = ("flaky-test", "dev-tool")
NEVER_EXEMPT_LABELS = ("security", "showstopper")
# Check name: exempt P1s (accepted/carried) may be open, so "zero open P0/P1" would be untruthful.
ISSUES_CHECK = "no blocking P0/P1"


def _classify(issues: list[dict]):
    blockers: list[tuple[int | None, str, str]] = []
    accepted: list[tuple[int | None, str, str]] = []
    carried: list[tuple[int | None, str, str]] = []
    for i in issues:
        if "pull_request" in i or str(i.get("state", "open")) != "open":
            continue
        sev = _severity(i)
        if not sev:
            continue
        row = (i.get("number"), sev, str(i.get("title", ""))[:70])
        if sev == "P0":
            blockers.append(row)                       # P0 always blocks, even with the accepted label
        elif sev == "P1":
            labels = {l.lower() for l in _label_names(i)}
            exempt = bool(labels & set(NON_BLOCKING_P1_LABELS))
            if "security" in labels or (exempt and labels & set(NEVER_EXEMPT_LABELS)):
                blockers.append(row)                   # blocking labels first: no acceptance label can override them
            elif ACCEPTED_LABEL in labels:
                accepted.append(row)
            elif exempt:
                carried.append(row)                    # flaky-test / dev-tool P1: printed as known issue, not blocking
            else:
                blockers.append(row)
        else:
            carried.append(row)                        # P2/P3 no longer block (owner rule 3 Nov 2026)
    return blockers, accepted, carried


def classify_issues(issues: list[dict]) -> tuple[list[tuple[int | None, str, str]], list[tuple[int | None, str, str]]]:
    """THE one release-gate issue rule, shared by `jarvis release status` and core/tools/release_gate.py (#535).

    Returns (blockers, accepted), each a list of (number, severity, title) for open issues (not PRs).
    Blockers: every open P0, every open P1 labelled security, and every other open P1 without the owner-only label
    `known-issue-4.5` (exact), except a P1
    labelled flaky-test/dev-tool and neither security nor showstopper (carried). A flaky-test/dev-tool P1 that also
    carries showstopper blocks, even with `known-issue-4.5`. Accepted: open P1 carrying
    `known-issue-4.5` (printed, not a blocker). Open P2/P3 never block: see carried_issues."""
    b, a, _ = _classify(issues)
    return b, a


def carried_issues(issues: list[dict]) -> list[tuple[int | None, str, str]]:
    """Open P2/P3 and exempt flaky-test/dev-tool P1 issues: printed as 'known issues carried', never blocking."""
    return _classify(issues)[2]


def check_issues(issues: list[dict]) -> Check:
    bad, accepted = classify_issues(issues)
    note = ""
    if accepted:
        note = f"; accepted known issues ({ACCEPTED_LABEL}): " + ", ".join(f"#{n} {s}" for n, s, _ in accepted)
    carried = carried_issues(issues)
    if carried:
        p1 = sum(1 for _, sev, _ in carried if sev == "P1")
        note += f"; known issues carried into this release: {len(carried) - p1} open P2/P3"
        note += f", {p1} open flaky-test/dev-tool P1" if p1 else ""
    detail = ("no blocking P0/P1" if not bad else "open: " + ", ".join(f"#{n} {s}" for n, s, _ in bad)) + note
    return Check(ISSUES_CHECK, not bad, detail)


def check_sheet(root: Path, version: str) -> Check:
    try:
        sh = so.load(root, version)
    except so.SheetError:
        return Check("owner sheet", False, f"missing; run `jarvis release sheet --version {version}`")
    left = [r.id for r in sh.rows if r.problem()]
    if left:
        return Check("owner sheet", False, "rows left for you: " + ", ".join(left))
    if not sh.signed:
        return Check("owner sheet", False, "rows done; sign with `jarvis release signoff --finish`")
    return Check("owner sheet", True, f"signed, beta {sh.beta}")


def collect(root: Path, version: str, fetch=None, head: str | None = None, now: datetime | None = None) -> list[Check]:
    """`fetch(kind)` returns API-shaped lists for 'checks', 'issues' (default: GitHub)."""
    fetch = fetch or github_fetch
    out: list[Check] = []
    for name, fn in (("main green", lambda: check_main(fetch("checks"), head)),
                     (ISSUES_CHECK, lambda: check_issues(fetch("issues")))):
        try:
            out.append(fn())
        except Exception as e:                                    # network or API trouble is a TODO, never a pass
            out.append(Check(name, False, f"could not read GitHub ({type(e).__name__})"))
    out.append(check_sheet(root, version))
    return out


def remaining(checks: list[Check]) -> list[str]:
    """What is left, in plain words, for the owner (or for CI, when the check is a machine one)."""
    todo = []
    for c in checks:
        if c.ok:
            continue
        who = "you" if c.name == "owner sheet" else "CI / agents"
        todo.append(f"[{who}] {c.name}: {c.detail}")
    return todo


def report(version: str, checks: list[Check], today: str | None = None) -> str:
    rows = "\n".join(f"| {c.name} | {'PASS' if c.ok else 'TODO'} | {c.detail} |" for c in checks)
    left = remaining(checks)
    tail = "\n".join(f"- {t}" for t in left) if left else "READY: tag it."
    return (f"# Release evidence for JARVES {version}\n\nGenerated {today or date.today().isoformat()} by "
            f"`jarvis release status`. Do not edit.\n\n| Check | State | Evidence |\n|---|---|---|\n{rows}\n\n"
            f"## What remains\n\n{tail}\n")


# ---------------------------------------------------------------------- the reduced owner sheet
def reduced_sheet(version: str, full: so.Sheet | None = None) -> so.Sheet:
    rows = [so.Row(i, "Smoke", t, e) for i, t, e in SMOKE]
    kept = [r for r in (full.rows if full else []) if r.id not in SMOKE_IDS]
    if kept:
        for r in kept:
            rows.append(so.Row(r.id, r.area, r.todo, r.expected, "accepted-blocked", AUTOMATION_NOTE.format(ev=EVIDENCE)))
    else:
        rows.append(so.Row("A1", "Automation", "Everything that changed since the last stable (changelog items, guides, "
                           "dashboard pages, installer, approvals logic)", "Proven by CI",
                           "accepted-blocked", AUTOMATION_NOTE.format(ev=EVIDENCE)))
    intro = (f"Reduced owner sheet: {len(SMOKE)} smoke rows on your PC; the other rows are accepted by automation with "
             "their evidence. Install the LATEST beta first, then sign off.")
    return so.Sheet(version=version, rows=rows, intro=intro)


def write_sheet(root: Path, version: str, force: bool = False) -> Path:
    path = so.sheet_path(root, version)
    full = None
    if path.is_file():
        full = so.load(root, version)
        if (full.signed or any(r.id in SMOKE_IDS and r.result != "pending" for r in full.rows)) and not force:
            raise so.SheetError(f"{path.name} already has results or a signature; pass --force to rebuild it")
    return so.save(root, reduced_sheet(version, full))


# ---------------------------------------------------------------------- GitHub (not used by the tests)
def github_fetch(kind: str) -> list[dict]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "jarves-release-flow"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"

    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
            return json.loads(r.read())
    base = f"https://api.github.com/repos/{REPO}"
    if kind == "checks":
        return get(f"{base}/actions/workflows/jarves-checks.yml/runs?per_page=30&branch=main&event=push&status=completed")["workflow_runs"]
    out: list[dict] = []
    for label in ("P0", "P1", "P2"):
        out += get(f"{base}/issues?state=open&labels={label}&per_page=100")
    return out
