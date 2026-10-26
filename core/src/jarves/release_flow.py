"""One simple release flow (docs/plans/SIMPLE_RELEASES_2026-10-26.md): `jarvis release status` and `jarvis release sheet`.

`status` runs every automatable gate (main green, nightly fresh and green, open P0-P2 issues from live labels, the owner
sheet, the SIGNED file) and says exactly what remains for the owner. `sheet` writes the reduced owner sheet: a tiny smoke
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import release_signoff as so

REPO = "Jarvis-igerman-ai/jarves"
NIGHT_MAX_AGE = timedelta(days=2)
_LATER = re.compile(r"(?i)^(enhancement|v[5-9])$")
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
            "uninstall smoke) and the latest nightly")


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


def check_nightly(runs: list[dict], now: datetime | None = None) -> Check:
    now = now or datetime.now(timezone.utc)
    done = [r for r in runs if r.get("status", "completed") == "completed"]
    if not done:
        return Check("nightly green", False, "no completed nightly run; run the nightly workflow on main")
    last = max(done, key=lambda r: _when(r["created_at"]))
    day = _when(last["created_at"]).date()
    if last.get("conclusion") != "success":
        return Check("nightly green", False, f"{day} {last.get('conclusion')}")
    if now - _when(last["created_at"]) > NIGHT_MAX_AGE:
        return Check("nightly green", False, f"latest green nightly is from {day}; run a fresh one")
    return Check("nightly green", True, f"{day} success")


def check_issues(issues: list[dict]) -> Check:
    bad = []
    for i in issues:
        if "pull_request" in i or str(i.get("state", "open")) != "open":
            continue
        labels = [str(l.get("name", "")) if isinstance(l, dict) else str(l) for l in i.get("labels", [])]
        sev = next((l.upper() for l in labels if re.fullmatch(r"(?i)P[0-2]", l)), None)
        if sev and not any(_LATER.match(l) for l in labels):
            bad.append(f"#{i.get('number')} {sev}")
    return Check("zero open P0-P2", not bad, "none open" if not bad else "open: " + ", ".join(bad))


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


def check_signed(root: Path, version: str) -> Check:
    p = Path(root) / "docs" / "release" / f"SIGNED_{version}"
    return Check("SIGNED file", p.is_file(), "present" if p.is_file() else f"missing docs/release/SIGNED_{version}")


def collect(root: Path, version: str, fetch=None, head: str | None = None, now: datetime | None = None) -> list[Check]:
    """`fetch(kind)` returns API-shaped lists for 'checks', 'nightly', 'issues' (default: GitHub)."""
    fetch = fetch or github_fetch
    out: list[Check] = []
    for name, fn in (("main green", lambda: check_main(fetch("checks"), head)),
                     ("nightly green", lambda: check_nightly(fetch("nightly"), now)),
                     ("zero open P0-P2", lambda: check_issues(fetch("issues")))):
        try:
            out.append(fn())
        except Exception as e:                                    # network or API trouble is a TODO, never a pass
            out.append(Check(name, False, f"could not read GitHub ({type(e).__name__})"))
    out += [check_sheet(root, version), check_signed(root, version)]
    return out


def remaining(checks: list[Check]) -> list[str]:
    """What is left, in plain words, for the owner (or for CI, when the check is a machine one)."""
    todo = []
    for c in checks:
        if c.ok:
            continue
        who = "you" if c.name in ("owner sheet", "SIGNED file") else "CI / agents"
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
                           "dashboard pages, installer, approvals logic)", "Proven by CI and the nightly",
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
    if kind == "nightly":
        return get(f"{base}/actions/workflows/nightly.yml/runs?per_page=10&branch=main&status=completed")["workflow_runs"]
    out: list[dict] = []
    for label in ("P0", "P1", "P2"):
        out += get(f"{base}/issues?state=open&labels={label}&per_page=100")
    return out
