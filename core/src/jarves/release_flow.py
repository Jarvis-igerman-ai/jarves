"""One simple release flow (docs/plans/SIMPLE_RELEASES_2026-10-26.md): `jarvis release status` and `jarvis release sheet`.

`status` runs every automatable gate (main green, open P0-P2 issues from live labels, the owner
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
_LATER = re.compile(r"(?i)^(enhancement|v[5-9]|4\.5\.1)$")
ACCEPTED_LABEL = "known-issue-4.5"   # OWNER-ONLY label: the owner accepts an open P0-P2 as a known issue for the 4.5 stable
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


def classify_issues(issues: list[dict]) -> tuple[list[tuple[int | None, str, str]], list[tuple[int | None, str, str]]]:
    """THE one open-P0-P2 rule, shared by `jarvis release status` and core/tools/release_gate.py (#535).

    Returns (blockers, accepted), each a list of (number, severity, title) for open issues (not PRs) labelled P0-P2.
    Label `known-issue-4.5` (owner-only, exact) makes an issue accepted: printed, not a blocker, even when labelled bug.
    Planned work (enhancement, v5-v9, 4.5.1) is skipped ONLY when the issue is not also labelled bug."""
    blockers: list[tuple[int | None, str, str]] = []
    accepted: list[tuple[int | None, str, str]] = []
    for i in issues:
        if "pull_request" in i or str(i.get("state", "open")) != "open":
            continue
        labels = _label_names(i)
        sev = next((l.upper() for l in labels if re.fullmatch(r"(?i)P[0-2]", l)), None)
        if not sev:
            continue
        row = (i.get("number"), sev, str(i.get("title", ""))[:70])
        if any(l.lower() == ACCEPTED_LABEL for l in labels):
            accepted.append(row)
        elif any(l.lower() == "bug" for l in labels) or not any(_LATER.match(l) for l in labels):
            blockers.append(row)
    return blockers, accepted


def check_issues(issues: list[dict]) -> Check:
    bad, accepted = classify_issues(issues)
    note = ""
    if accepted:
        note = f"; accepted known issues ({ACCEPTED_LABEL}): " + ", ".join(f"#{n} {s}" for n, s, _ in accepted)
    detail = ("none open" if not bad else "open: " + ", ".join(f"#{n} {s}" for n, s, _ in bad)) + note
    return Check("zero open P0-P2", not bad, detail)


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
                     ("zero open P0-P2", lambda: check_issues(fetch("issues")))):
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
