"""Ticket drafts from findings, filed ONLY after the owner's approval, ONLY on the private repo the owner set in health.ticket_repo (empty by default = drafts stay local, V4-RP-2).

A draft holds: title, severity, version, commit, code links (log-index hints), counts, redacted evidence and the
reproduction steps when known. Filing is an approvals-flow action: the full text is in the approval prompt (and on
the Health page), then `gh issue create` runs. Duplicates are detected by a fingerprint written into the body
(`<!-- jarves-fingerprint: ... -->`): an existing OPEN issue with the same fingerprint gets a comment instead.
The repo must report visibility PRIVATE (`gh repo view`), otherwise nothing is filed. Nothing is ever filed
automatically.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from typing import Callable

from .. import logs
from . import db

Runner = Callable[[list[str], str | None], tuple[int, str]]      # (argv, stdin) -> (returncode, stdout)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def gh_runner(argv: list[str], stdin: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(["gh", *argv], input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr if p.returncode else "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, type(e).__name__


def fingerprint(finding_fp: str) -> str:
    return hashlib.sha256(f"jarves-health:{finding_fp}".encode()).hexdigest()[:16]


def _clean(s: str) -> str:
    return _EMAIL.sub("<email>", logs.redact(str(s or "")))


def draft(f: dict, *, version: tuple[str, str] | None = None, prefix: str = "[health]",
          origin: str = "JARVES Self-Diagnostics 2.0") -> dict:
    """A Markdown issue draft of one finding (monitor finding or accepted AI-review item). The Development Manager
    reuses it for its local suggestions (prefix "[dev]", its own origin line)."""
    from . import db as _db
    ver, commit = version or _db.version_info()
    fp = fingerprint(f["fingerprint"])
    title = f"{prefix} {f.get('title', f['fingerprint'])}"[:120]
    lines = [f"**Severity:** {f.get('severity', '?')} · **Version:** {ver}" + (f" · **Commit:** `{commit}`" if commit else "")
             + f" · **Platform:** {sys.platform}", "",
             f"**Finding:** `{f['fingerprint']}` (count {f.get('count', 1)}, category {f.get('category', '?')})", ""]
    if f.get("detail"):
        lines += [_clean(f["detail"]), ""]
    hints = f.get("hints") or []
    if hints:
        lines.append("**Code:**")
        lines += [f"- `{h['code']}` at [{h['file']}:{h['line']}]({h['url']}) in `{h.get('function', '')}`"
                  + (f", last change `{h['commit']}`" if h.get("commit") else "") for h in hints[:5]]
        lines.append("")
    lines.append("**Evidence (redacted):**")
    lines += [f"- `{_clean(e)[:200]}`" for e in (f.get("evidence") or [])[:8]] or ["- (none)"]
    lines.append("")
    if f.get("cause"):
        lines += [f"**Likely cause:** {_clean(f['cause'])}", ""]
    if f.get("fix"):
        lines += [f"**Suggested fix:** {_clean(f['fix'])}", ""]
    if f.get("repro"):
        lines += ["**Reproduction:**"] + [f"{i}. {_clean(s)}" for i, s in enumerate(f["repro"], 1)] + [""]
    lines += [f"Drafted by {origin}; filed after the owner approved it. (#17)",
              f"<!-- jarves-fingerprint: {fp} -->"]
    return {"fingerprint": fp, "finding": f["fingerprint"], "title": title, "body": "\n".join(lines),
            "severity": f.get("severity", "")}


def save_draft(store, d: dict, *, now: float | None = None) -> int:
    old = db.rows(store, "SELECT id FROM diag_tickets WHERE fingerprint=? AND status='draft'", (d["fingerprint"],))
    if old:
        db.write(store, "UPDATE diag_tickets SET title=?, body=?, severity=? WHERE id=?",
                 (d["title"], d["body"], d["severity"], old[0]["id"]))
        return old[0]["id"]
    return db.write(store, "INSERT INTO diag_tickets (fingerprint, finding, created, title, body, severity) "
                           "VALUES (?,?,?,?,?,?)", (d["fingerprint"], d["finding"], now or time.time(), d["title"],
                                                    d["body"], d["severity"]))


def get(store, tid: int) -> dict | None:
    r = db.rows(store, "SELECT * FROM diag_tickets WHERE id=?", (tid,))
    return r[0] if r else None


def listing(store, limit: int = 30, *, dev: bool = False) -> list[dict]:
    """Health ticket drafts (default) or the Development Manager's drafts (dev=True, finding "dev:...")."""
    return db.rows(store, "SELECT * FROM diag_tickets WHERE finding " + ("" if dev else "NOT ") +
                   "LIKE 'dev:%' AND status<>'discarded' ORDER BY id DESC LIMIT ?", (limit,))


def _private(repo: str, run: Runner) -> tuple[bool, str]:
    rc, out = run(["repo", "view", repo, "--json", "visibility"], None)
    if rc != 0:
        return False, f"gh repo view failed ({out.strip()[:120]})"
    try:
        vis = str(json.loads(out).get("visibility", "")).upper()
    except ValueError:
        return False, "gh repo view: unreadable answer"
    return vis == "PRIVATE", vis.lower() or "unknown"


async def file(store, tid: int, *, ask, repo: str, by: str, run: Runner = gh_runner) -> dict:
    """Ask the owner (full text in the prompt), then file on the PRIVATE repo or comment on the duplicate."""
    t = get(store, tid)
    if t is None:
        raise KeyError(tid)
    if t["status"] != "draft":
        return {"id": tid, "status": t["status"], "issue_url": t["issue_url"]}
    if not (repo or "").strip():                      # V4-RP-2: no repo configured = the draft stays a local file
        return {"id": tid, "status": "draft", "reason": "no ticket repo is set (health.ticket_repo); the draft stays local, nothing filed"}
    ok, vis = _private(repo, run)
    if not ok:
        db.write(store, "UPDATE diag_tickets SET status='draft', by=? WHERE id=?", (by, tid))
        logs.warn("health", "TICKET_NOT_PRIVATE", "ticket not filed: the target repo is not private", visibility=vis)
        return {"id": tid, "status": "refused", "reason": f"{repo} is not a private repo ({vis}); nothing filed"}
    approved = False
    if ask is not None:
        try:
            approved = bool(await ask(f"File this issue on {repo}?\n\n# {t['title']}\n\n{t['body']}"[:2000],
                                      title=f"File a ticket on {repo}", why="Self-Diagnostics ticket draft",
                                      risk="medium", kind="message",
                                      details={"repo": repo, "ticket": tid, "fingerprint": t["fingerprint"]}))
        except Exception:
            approved = False
    if not approved:
        logs.info("health", "TICKET_DENIED", "ticket draft not approved: nothing filed", ticket=tid)
        return {"id": tid, "status": "draft", "reason": "not approved; nothing filed"}
    rc, out = run(["issue", "list", "--repo", repo, "--state", "open", "--search", t["fingerprint"], "--json",
                   "number,url"], None)
    dup = []
    if rc == 0:
        try:
            dup = json.loads(out or "[]")
        except ValueError:
            dup = []
    if dup:
        n = dup[0]["number"]
        rc, out = run(["issue", "comment", str(n), "--repo", repo, "--body-file", "-"],
                      f"Seen again ({time.strftime('%Y-%m-%d %H:%M')}):\n\n{t['body']}")
        status, url = ("commented", dup[0].get("url", "")) if rc == 0 else ("failed", "")
    else:
        rc, out = run(["issue", "create", "--repo", repo, "--title", t["title"], "--body-file", "-"], t["body"])
        url = (out.strip().splitlines() or [""])[-1] if rc == 0 else ""
        status = "filed" if rc == 0 else "failed"
    db.write(store, "UPDATE diag_tickets SET status=?, issue_url=?, filed_at=?, by=? WHERE id=?",
             ("draft" if status == "failed" else status, url, time.time(), by, tid))
    if status == "failed":
        logs.warn("health", "TICKET_FAILED", "ticket not filed (gh failed)", ticket=tid)
    else:
        logs.info("health", "TICKET_FILED", "ticket filed or commented", ticket=tid, decision=status)
    return {"id": tid, "status": status, "issue_url": url, "detail": "" if status != "failed" else out[:200]}
