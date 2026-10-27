"""Slice B of #336: a self-diagnosis report becomes a LOCAL ticket the owner can send later.

`jarvis diagnose <area> --code C --ticket` records the redacted report in the issue registry (the same store and the same
approval path as `jarvis ticket`): nothing is sent, nothing is posted. Sending stays `jarvis ticket send JI-xxxxxxxx`, which
shows the exact text and asks first. The ticket holds only key names, codes, booleans, versions and short identifiers
(issue_registry.clean_evidence drops anything else) plus the EN cause line; no conversation text. A final scan BLOCKS the
ticket if any secret-, e-mail-, phone- or profile-path-shaped text is still in what would be sent."""
from __future__ import annotations

import hashlib
import platform
import re
from pathlib import Path

from .. import issue_registry as ir
from .. import logs
from . import areas

_PATH = re.compile(r"(?i)[A-Za-z]:[\\/]|/(?:home|users)/")


class TicketBlocked(Exception):
    """The final scan found something private-looking; `reasons` are pattern NAMES only, never the text."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("TICKET_BLOCKED")
        self.reasons = reasons


def scan(text: str) -> list[str]:
    """Names of private-looking patterns still in `text` (empty = clean)."""
    out = []
    if logs.has_secret(text) or logs.redact(text) != text:
        out.append("secret")
    if logs.mask_emails(text) != text:
        out.append("email")
    if logs.redact_pii(text) != text and "email" not in out:
        out.append("phone")
    if _PATH.search(text):
        out.append("path")
    return out


def evidence_of(rep: areas.DiagReport) -> dict:
    """Structured, content-free evidence (registry-clean values only)."""
    ev: dict = {"diag_area": rep.area, "error_code": rep.code, "confidence": rep.confidence, "diag_os": platform.system()}
    if rep.guide_id:
        ev["guide"] = rep.guide_id
    for line in rep.evidence:
        k, _, v = line.partition(": ")
        if k == "jarves_version":
            ev["jarves_version"] = v.strip()
        elif k == "last_log_codes":
            ev["last_log_codes"] = [c.strip() for c in v.split(";") if c.strip()][:8]
    return ev


def make(home: Path, rep: areas.DiagReport) -> dict:
    """Record the local ticket and return {id, title, body}. Raises TicketBlocked when the final scan finds private text."""
    title = f"[diag] {rep.area}: {rep.code}"
    ev = evidence_of(rep)
    cause = " ".join(f"{rep.cause_en} {rep.fix_en}".split())[:300]
    probe = "\n".join([title, cause] + [f"{k}={v}" for k, v in ev.items()])
    bad = scan(probe)
    if bad:
        raise TicketBlocked(bad)
    code = "TICKET_DIAG_" + hashlib.sha256(f"{rep.area}|{rep.code}".encode()).hexdigest()[:8].upper()
    r = ir.Registry(home).record("ticket", code, "P3", ev, title=ir.clean_text(title, 100) or code, source="diagnose",
                                 meaning=cause)
    if r is None:
        raise TicketBlocked(["registry"])
    b = ir.bundle(r)
    body = b["body"]                            # exactly what `jarvis ticket send` would send
    bad = scan(b["title"] + "\n" + body)
    if bad:
        raise TicketBlocked(bad)
    return {"id": r["id"], "title": b["title"], "body": body}
