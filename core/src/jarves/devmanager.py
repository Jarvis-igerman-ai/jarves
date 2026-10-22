"""Development Manager (V5 pillar, software slice): suggestions and tickets for JARVES's own development, kept as
LOCAL DRAFTS only.

It reuses the Self-Diagnostics ticket-draft code (`diag/tickets.py`: draft text, `diag_tickets` table in store.db).
A draft never leaves the PC by itself: there is no scheduler job, no automatic posting and no voice tool that posts.
The only way out is the existing, approval-gated filing (`tickets.file`): it asks on the approval popup with the full
text, and still needs a private repository the owner set in `health.ticket_repo` (empty = drafts stay local).
Sensitive text (passwords, long numbers, health and money words) is refused and redacted like every ticket.
"""
from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from . import calm, features as feat, logs
from .diag import db as diag_db, tickets
from .preferences import looks_sensitive

FEATURE = "dev_manager"
feat.register(FEATURE, "מנהל פיתוח (טיוטות מקומיות)", "Development manager (local drafts)", True, skills=["dev"],
              about="ideas and tickets for JARVES's own development are kept as local drafts; nothing is posted to "
                    "GitHub unless you approve it on the screen",
              targets=calm.TARGETS)
KINDS = ("suggestion", "ticket")
MAX_TITLE, MAX_BODY = 100, 1500
ORIGIN = "JARVES Development Manager (local draft)"


def suggest(store, title: str, body: str = "", kind: str = "suggestion", *, by: str = "user") -> dict:
    """Save a local draft. Raises ValueError (nothing stored) for an empty, sensitive or unknown-kind draft."""
    t, b = " ".join(str(title or "").split())[:MAX_TITLE], str(body or "").strip()[:MAX_BODY]
    if not t:
        raise ValueError("a draft needs a title")
    if kind not in KINDS:
        raise ValueError("kind must be one of: " + ", ".join(KINDS))
    if looks_sensitive(t) or looks_sensitive(b):
        logs.info("devmgr", "DEV_DRAFT_REFUSED", "sensitive draft not stored", by=by)
        raise ValueError("this looks sensitive (secret, number, health or money detail): not stored")
    fp = "dev:" + hashlib.sha256(t.casefold().encode()).hexdigest()[:12]
    d = tickets.draft({"fingerprint": fp, "title": t, "severity": kind, "category": "development", "detail": b,
                       "count": 1}, prefix="[dev]", origin=ORIGIN)
    tid = tickets.save_draft(store, d)
    logs.info("devmgr", "DEV_DRAFT_SAVED", "development draft saved locally", id=tid, by=by)
    return view(tickets.get(store, tid))


def view(r: dict | None) -> dict | None:
    return None if r is None else {k: r[k] for k in ("id", "title", "body", "severity", "status", "issue_url", "created")}


def listing(store) -> list[dict]:
    return [view(r) for r in tickets.listing(store, 50, dev=True)]


def get(store, tid: int) -> dict | None:
    r = tickets.get(store, tid)
    return view(r) if r and str(r["finding"]).startswith("dev:") and r["status"] != "discarded" else None


def discard(store, tid: int, *, by: str = "user") -> bool:
    if get(store, tid) is None:
        return False
    diag_db.write(store, "UPDATE diag_tickets SET status='discarded', by=? WHERE id=? AND status='draft'", (by, tid))
    logs.info("devmgr", "DEV_DRAFT_DISCARDED", "development draft discarded", id=tid, by=by)
    return True


# ---------------------------------------------------------------------- voice / agent tools
class SuggestArgs(BaseModel):
    title: str = Field(..., min_length=3, max_length=MAX_TITLE)
    body: str = Field("", max_length=MAX_BODY)
    kind: str = Field("suggestion", max_length=10, description="suggestion or ticket")


class IdArgs(BaseModel):
    id: int = Field(..., ge=1)


class NoArgs(BaseModel):
    pass


def make_skill(open_store):
    """open_store() -> a Store (store.db with the diag tables); closed after each call."""
    from .skills import Skill, Tool, ToolResult

    def with_store(fn):
        store = open_store()
        try:
            return fn(store)
        finally:
            store.close()

    class DevSkill(Skill):
        name = "dev"
        description = "development ideas and tickets as local drafts"

        def tools(self) -> list:
            async def add(a: SuggestArgs):
                try:
                    d = with_store(lambda s: suggest(s, a.title, a.body, a.kind, by="voice"))
                except ValueError as e:
                    return ToolResult(False, str(e), code="DEV_REFUSED")
                return ToolResult(True, "saved as a local draft, nothing was posted", {"draft": d})

            async def show(_a: NoArgs):
                rows = with_store(listing)
                return ToolResult(True, "; ".join(r["title"] for r in rows) or "no drafts", {"drafts": rows})

            async def drop(a: IdArgs):
                return (ToolResult(True, "discarded") if with_store(lambda s: discard(s, a.id, by="voice"))
                        else ToolResult(False, "no such draft", code="DEV_NOT_FOUND"))
            return [Tool("dev_suggest", "Save a development idea or ticket as a LOCAL draft (never posted).", add,
                         SuggestArgs, permission="action"),
                    Tool("dev_drafts", "List the local development drafts.", show, NoArgs, permission="read"),
                    Tool("dev_discard", "Discard a local development draft.", drop, IdArgs, permission="action")]
    return DevSkill()


ALLOWED_ACTIONS = {"dev_suggest", "dev_discard"}


# ---------------------------------------------------------------------- dashboard: the Development drafts page
def api_drafts(ctx, m, body, q=None):
    diag_db.ensure(ctx.store)
    return 200, {"drafts": listing(ctx.store), "kinds": list(KINDS),
                 "repo_set": bool(str(_db(ctx).setting("health.ticket_repo") or "").strip())}


def _db(ctx):
    from .userdata import ctx_db
    return ctx_db(ctx)


def api_draft_add(ctx, m, body):
    b = body or {}
    try:
        return 200, {"draft": suggest(ctx.store, str(b.get("title", "")), str(b.get("body", "")),
                                      str(b.get("kind", "suggestion")), by="dashboard")}
    except ValueError as e:
        return 400, {"error": str(e)}


def api_draft_op(ctx, m, body):
    b = body or {}
    try:
        tid = int(b.get("id", 0) or 0)
    except (TypeError, ValueError):
        return 400, {"error": "id must be a number"}
    if b.get("op") == "discard":
        return (200, {"ok": True}) if discard(ctx.store, tid, by="dashboard") else (404, {"error": "no such draft"})
    return 400, {"error": "op must be discard (posting is the approval-gated /api/health/ticket/file)"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/dev/drafts", api_drafts)
    register("POST", r"/api/dev/drafts", api_draft_add)
    register("POST", r"/api/dev/op", api_draft_op)
