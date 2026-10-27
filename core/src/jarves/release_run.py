"""`jarvis release run`: the stable-release steps that were done by hand, in one guarded command
(docs/plans/LEAN_PROCESS_V2_2026-10-27.md, item 6).

    jarvis release run --version 5.0.0                                   # evidence + what is missing, signs nothing
    jarvis release run --version 5.0.0 --approved-by-owner "<quote or time UTC>"   # records the sign-off, prints next steps
    jarvis release run --version 5.0.0 --dry-run [--approved-by-owner ...]  # same checks, writes and signs nothing

Steps: (1) the same evidence as `release status` (main green, zero open P0-P2 minus known-issue-* labels, the owner
sheet); (2) REFUSE when main is red, an open P0-P2 remains, or the sheet cannot be signed (rows open); (3) only with an
explicit --approved-by-owner, the same sign-off as `release signoff --finish` (the chat approval is recorded in the sheet's
owner field and in the evidence file); (4) print the exact next commands: docs PR, release_gate.py, tag. This module never
tags, never pushes and never starts git or gh: it starts no other program at all (a test checks that). GitHub data is
injected (`fetch`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import clilang, logs, release_flow as rf, release_signoff as so

MIN_APPROVAL = 5


@dataclass
class Result:
    code: int = 1                                  # 0 signed + next steps printed, 1 not done yet, 2 refused
    refusals: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    signed_now: bool = False
    evidence: Path | None = None

    def say(self, s: str = "") -> None:
        self.lines.append(s)


def clean_approval(text: str | None) -> str:
    """The owner's chat quote or time, one printable line (it is written into a signed file)."""
    t = " ".join(str(text or "").split())
    if t and (len(t) < MIN_APPROVAL or not t.isprintable()):
        raise so.SheetError(f"--approved-by-owner needs the owner's words or a UTC time (at least {MIN_APPROVAL} "
                            "printable characters)")
    return t[:200]


def refusals(checks: list[rf.Check], sheet: so.Sheet | None, sheet_error: str = "") -> list[str]:
    out = [f"{c.name}: {c.detail}" for c in checks if c.name in ("main green", "zero open P0-P2") and not c.ok]
    if sheet is None:
        out.append(f"owner sheet: {sheet_error or 'missing'}")
    elif not sheet.rows:
        out.append("owner sheet: no rows")
    elif sheet.open_rows():
        out.append("owner sheet not signable, rows open: " + "; ".join(sheet.open_rows()))
    return out


def next_commands(version: str, sheet_rel: str, evidence_rel: str) -> list[str]:
    """Exact commands for the owner/agent to run by hand. Nothing here is executed."""
    tag = f"v{version}"
    return [
        "1. docs PR (sheet + evidence), then wait for it to merge:",
        f"     git fetch jarves && git switch -c release-{version} jarves/main",
        f"     git add {sheet_rel} {evidence_rel}",
        f"     git commit -m \"docs: release {version} sign-off and evidence\"",
        f"     git push -u jarves release-{version}",
        f"     gh pr create --repo {rf.REPO} --base main --title \"docs: release {version} sign-off\" "
        f"--body \"Sign-off sheet and evidence for {version}.\"",
        "     gh pr merge <N> --auto",
        "2. gate check on the merged commit:",
        "     git fetch jarves && git rev-parse jarves/main",
        f"     python core/tools/release_gate.py --tag {tag} --sha <that sha> --candidate-dir dist",
        "3. tag the signed commit (never move, force or delete a tag), then watch release.yml and latest.json:",
        f"     git tag -a {tag} -m \"JARVES {version}\" <that sha>",
        f"     git push jarves {tag}",
        f"     gh run list --repo {rf.REPO} --workflow release.yml --limit 3",
    ]


def run(root: Path, version: str, *, approved_by: str | None = None, dry_run: bool = False,
        fetch: Callable[[str], list[dict]] | None = None, finish: Callable[[so.Sheet, str], None],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Result:
    """`finish(sheet, owner)` records beta, commit and owner on the sheet (release_cmd._finish); injected for tests."""
    r = Result()
    version = so.normalise_version(version)
    approval = clean_approval(approved_by)
    checks = rf.collect(root, version, fetch=fetch)
    for c in checks:
        r.say(f"  [{'PASS' if c.ok else 'TODO'}] {c.name}: {c.detail}")
    try:
        sheet: so.Sheet | None = so.load(root, version)
        err = ""
    except so.SheetError as e:
        sheet, err = None, str(e)
    ref = refusals(checks, sheet, err)
    sheet_rel = f"docs/reviews/MANUAL_TEST_{version}.md"
    evidence_rel = f"docs/release/EVIDENCE_{version}.md"
    if ref or sheet is None:
        r.code, r.refusals = 2, ref
        r.say("REFUSED, nothing was signed:")
        r.lines += [f"  - {x}" for x in ref]
        return r
    if not approval:
        r.say("Ready to sign. Needs the owner's approval: re-run with  --approved-by-owner \"<chat quote or time UTC>\"")
        return r
    if dry_run:
        r.say(f"DRY RUN: would sign {sheet_rel} (approved by owner: {approval}) and write {evidence_rel}; nothing changed.")
        r.say("Then the next commands would be:")
        r.lines += ["  " + x for x in next_commands(version, sheet_rel, evidence_rel)]
        r.code = 0
        return r
    if not sheet.signed:
        try:
            finish(sheet, f"owner (chat approval: {approval})")
        except so.SheetError as e:
            r.code, r.refusals = 2, [f"sign-off: {e}"]
            r.say(f"REFUSED, nothing was signed: {e}")
            return r
        so.save(root, sheet)
        r.signed_now = True
        r.say(f"Signed {sheet_rel}: beta {sheet.beta}, commit {sheet.commit[:12]}, owner {sheet.owner}.")
        logs.info("release", "RELEASE_SIGNED", "release run: sign-off recorded from chat approval", version=version)
    else:
        r.say(f"{sheet_rel} was already signed (beta {sheet.beta}); not signing again.")
    checks = rf.collect(root, version, fetch=fetch)
    ev = root / "docs" / "release" / f"EVIDENCE_{version}.md"
    ev.parent.mkdir(parents=True, exist_ok=True)
    when = now()
    ev.write_text(rf.report(version, checks, today=when.date().isoformat())
                  + f"\n## Owner approval\n\nApproved by owner in chat: {approval}\n"
                    f"Recorded {when.strftime('%Y-%m-%d %H:%M UTC')} by `jarvis release run`.\n",
                  encoding="utf-8", newline="\n")
    r.evidence = ev
    r.say(f"Evidence report: {ev}")
    if not all(c.ok for c in checks):
        r.say("Not ready to tag yet:")
        r.lines += [f"  - {t}" for t in rf.remaining(checks)]
        return r
    r.say(clilang.fmt(f"מוכן: אפשר לתייג v{version}, בפקודות הבאות. הפקודה הזו לא מתייגת ולא דוחפת.",
                      f"READY to tag v{version}. This command does not tag or push; run these yourself:"))
    r.lines += ["  " + x for x in next_commands(version, sheet_rel, evidence_rel)]
    r.code = 0
    return r
