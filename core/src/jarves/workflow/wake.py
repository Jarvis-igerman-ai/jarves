"""Quiet, coalesced wake of an idle manager (issue #762). Design: docs/workflow/WAKE_DESIGN.md.

Requests are stored per target in the ledger (workflow_meta), so many requests collapse into one pending
wake. Delivery injects ONE short line only when the channel reports a clean prompt; otherwise the outcome is
logged and the wake stays pending. There is no timer and no retry loop: the next attempt happens only on a
new event (a new request, or the manager reaching idle). No popup, sound or focus change is made here; the
real keystroke channel is an injected object and stays behind its own opt-in.
"""
from __future__ import annotations

import json

WAKE_LINE = "Workflow: authorised ready work is waiting. Run workflow status."
_KEY = "wake:"


def _read(db, target):
    row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (_KEY + target,)).fetchone()
    return json.loads(row[0]) if row else None


def request_wake(store, target: str, reason: str) -> dict:
    """Coalesce: one pending record per target; later requests only bump count and keep a short reason list."""
    reason = (reason or "")[:120]
    with store._tx() as db:
        rec = _read(db, target) or {"first_at": store.clock(), "count": 0, "reasons": []}
        rec["count"] += 1
        if reason and reason not in rec["reasons"] and len(rec["reasons"]) < 5:
            rec["reasons"].append(reason)
        store._set_meta(db, _KEY + target, rec)
        store._event(db, "wake_requested", f"{target}: pending x{rec['count']}")
    return rec


def pending(store, target: str):
    with store._tx() as db:
        return _read(db, target)


def _blocker(state) -> str:
    """First reason the prompt is not clean; empty string means clean. Unknown state is NOT clean."""
    if not isinstance(state, dict) or state.get("known") is not True:
        return "unknown_state"
    if state.get("permission_prompt"):
        return "permission_prompt"
    if state.get("mid_turn"):
        return "mid_turn"
    if state.get("draft"):
        return "draft_present"
    if not state.get("idle"):
        return "not_idle"
    return ""


def try_deliver(store, target: str, channel) -> str:
    """One attempt. channel.state() -> dict(known, idle, draft, permission_prompt, mid_turn); channel.inject(line).
    Returns 'none', 'delivered', 'deferred:<why>' or 'failed'. Never sleeps and never retries."""
    rec = pending(store, target)
    if rec is None:
        return "none"
    try:
        why = _blocker(channel.state())
    except Exception:
        why = "state_error"
    if why:
        with store._tx() as db:
            store._event(db, "wake_deferred", f"{target}: {why} (pending x{rec['count']})")
        return "deferred:" + why
    try:
        ok = channel.inject(WAKE_LINE) is not False
    except Exception:
        ok = False
    with store._tx() as db:
        if not ok:
            store._event(db, "wake_failed", f"{target}: inject failed; wake kept pending")
            return "failed"
        db.execute("DELETE FROM workflow_meta WHERE key=?", (_KEY + target,))
        store._event(db, "wake_delivered", f"{target}: one line for {rec['count']} request(s)")
    return "delivered"
