"""Proactive Intelligence (V5 pillar, software slice): by default JARVES answers only when asked.

- Mode `on_request` (default): items are only LISTED, on the dashboard (Proactive page), `jarvis proactive list` or
  when the owner asks by voice (`proactive_show`). Nothing is said, shown or sent on its own.
- Mode `in_conversation` (the owner switches it on): while the owner is ALREADY talking to JARVES, one pending item
  may be offered once, at a natural pause, as a short question in the same (voice) mode. Never spoken outside a
  conversation, never in quiet hours (22:00-07:00), at most one offer per conversation, no scheduler job, no alert.
- Items are derived from the owner's own goals (no movement for a week, past its date); an item the owner
  dismisses is never re-created for the same situation. Local only, in memory.db `proactive_items`.
"""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from . import calm, features as feat, logs
from .goals import STALE_DAYS, Goals
from .userdata import UserDB

FEATURE = "proactive_intel"
feat.register(FEATURE, "הצעות יזומות", "Proactive offers", True, applies="next_session", skills=["proactive"],
              about="by default JARVES answers only when asked; you can let it offer one item inside a conversation "
                    "you already started, never outside one and never between 22:00 and 07:00",
              targets=calm.TARGETS)
MODES = ("on_request", "in_conversation")
OFFER_NOTE = ("Optional offer (the user is already talking to you): at a natural pause, once, ask it as a short "
              "question in the same mode. Never while they speak. If they decline or ignore it, call "
              "proactive_dismiss with the id. Offer ")


class Proactive:
    def __init__(self, db: UserDB, goals: Goals, *, clock=time.time) -> None:
        self.db, self.goals, self.clock = db, goals, clock

    # ------------------------------------------------------------------ settings
    def mode(self) -> str:
        m = self.db.setting("proactive.mode")
        return m if m in MODES else "on_request"

    def set_mode(self, mode: str, *, by: str = "user") -> str:
        if mode not in MODES:
            raise ValueError("mode must be one of: " + ", ".join(MODES))
        self.db.set_setting("proactive.mode", mode, actor=by)
        logs.info("proactive", "PROACTIVE_MODE", "proactive mode changed", mode=mode, by=by)
        return mode

    # ------------------------------------------------------------------ items
    def refresh(self) -> list[dict]:
        """Recompute the items from the goals (only when asked or when a conversation starts; never in the background)."""
        now, want = self.clock(), {}
        for g in self.goals.list("active"):
            idle = (now - g["updated"]) / 86400
            if idle >= STALE_DAYS:
                want[("goal_stalled", f"{g['id']}:{int(g['updated'])}")] = f"{g['title']}: no movement for {int(idle)} days"
            if g["days_left"] is not None and g["days_left"] < 0:
                want[("goal_overdue", f"{g['id']}:{g['target_date']}")] = f"{g['title']}: past its date {g['target_date']}"
        for (kind, ref), text in want.items():
            self.db.x("INSERT OR IGNORE INTO proactive_items (kind, ref, text, created) VALUES (?,?,?,?)",
                      (kind, ref, text, now))
        for r in self.db.rows("SELECT id, kind, ref FROM proactive_items WHERE state<>'dismissed'"):
            if (r["kind"], r["ref"]) not in want:                 # the situation is over: the item goes away
                self.db.x("DELETE FROM proactive_items WHERE id=?", (r["id"],))
        return self.items()

    def items(self) -> list[dict]:
        return self.db.rows("SELECT id, kind, text, state, created, offered_at FROM proactive_items "
                            "WHERE state<>'dismissed' ORDER BY id")

    def dismiss(self, pid: int, *, by: str = "user") -> bool:
        n = self.db.x("UPDATE proactive_items SET state='dismissed' WHERE id=?", (pid,)).rowcount
        logs.info("proactive", "PROACTIVE_DISMISSED", "proactive item dismissed", id=pid, by=by)
        return bool(n)

    # ------------------------------------------------------------------ the in-conversation offer
    def offer(self) -> dict | None:
        """The ONE item to offer in a conversation that just started, or None. Marks it offered (never offered twice)."""
        if self.mode() != "in_conversation" or calm.quiet_hours_now(self.db, self.clock()):
            return None
        self.refresh()
        row = self.db.row("SELECT id, kind, text FROM proactive_items WHERE state='new' ORDER BY id LIMIT 1")
        if row is None:
            return None
        self.db.x("UPDATE proactive_items SET state='offered', offered_at=? WHERE id=?", (self.clock(), row["id"]))
        logs.info("proactive", "PROACTIVE_OFFERED", "proactive item offered in a conversation", id=row["id"])
        return row


def attach_live(live, proactive: Proactive, feats=None) -> None:
    """Voice: at most one offer per conversation, chosen when the conversation starts (a reconnect reuses it)."""
    orig = getattr(live, "session_config", None)
    if orig is None:
        return
    picked: list = []

    def session_config() -> dict:
        cfg = orig()
        if not picked:
            try:
                picked.append(proactive.offer() if feats is None or feats.active(FEATURE) else None)
            except Exception:
                picked.append(None)
                logs.error("proactive", "PROACTIVE_OFFER_FAILED", "proactive offer not prepared", exc=True)
        if picked[0] is not None:
            cfg["instructions"] = f"{cfg.get('instructions', '')}\n\n{OFFER_NOTE}{picked[0]['id']}: {picked[0]['text']}"
        return cfg
    live.session_config = session_config


# ---------------------------------------------------------------------- voice / agent tools
class IdArgs(BaseModel):
    id: int = Field(..., ge=1)


class NoArgs(BaseModel):
    pass


def make_skill(p: Proactive):
    from .skills import Skill, Tool, ToolResult

    class ProactiveSkill(Skill):
        name = "proactive"
        description = "proactive items: list on request, dismiss"

        def tools(self) -> list:
            async def show(_a: NoArgs):
                rows = p.refresh()
                return ToolResult(True, "; ".join(r["text"] for r in rows) or "nothing to mention", {"items": rows})

            async def dismiss(a: IdArgs):
                return (ToolResult(True, "dismissed") if p.dismiss(a.id, by="voice")
                        else ToolResult(False, "no such item", code="PROACTIVE_NOT_FOUND"))
            return [Tool("proactive_show", "List what JARVES could mention (only when asked).", show, NoArgs,
                         permission="read"),
                    Tool("proactive_dismiss", "Dismiss a proactive item.", dismiss, IdArgs, permission="action")]
    return ProactiveSkill()


ALLOWED_ACTIONS = {"proactive_dismiss"}


# ---------------------------------------------------------------------- dashboard: the Proactive page
def _ctx(ctx) -> Proactive:
    from .goals import _ctx as goals_ctx
    from .userdata import ctx_db
    p = getattr(ctx, "_proactive", None)
    if p is None:
        p = Proactive(ctx_db(ctx), goals_ctx(ctx))
        ctx._proactive = p
    return p


def api_proactive(ctx, m, body, q=None):
    p = _ctx(ctx)
    return 200, {"mode": p.mode(), "modes": list(MODES), "items": p.refresh(),
                 "quiet_now": calm.quiet_hours_now(p.db, p.clock())}


def api_proactive_op(ctx, m, body):
    p, b = _ctx(ctx), body or {}
    try:
        if b.get("op") == "mode":
            return 200, {"mode": p.set_mode(str(b.get("mode", "")), by="dashboard")}
        if b.get("op") == "dismiss":
            return 200, {"ok": p.dismiss(int(b.get("id", 0) or 0), by="dashboard")}
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    return 400, {"error": "op must be mode or dismiss"}


def dashboard_routes(register) -> None:
    register("GET", r"/api/proactive", api_proactive)
    register("POST", r"/api/proactive/op", api_proactive_op)
