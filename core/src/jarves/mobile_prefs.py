"""Phone page for Learned preferences (V5 gap G3, issue #183): list, confirm, edit and forget, from a paired phone.

Same store and same rules as the dashboard page (`preferences.Preferences`): a sensitive text is refused and never
stored, nothing is inferred (the phone cannot add or "suggest" a preference), conflicts are shown, every change is
logged by id only. The phone needs the `prefs` scope (off until the owner grants it) and the phone token like every
other `/api/m/` route; there is no new unauthenticated route. Clearing everything stays on the computer.

Privacy: the owner's own paired phone shows the text (like the dashboard and the chat), passed through the same output
cleaner (secrets and e-mail addresses redacted). Privacy mode governs what is KEPT: the audit log holds the device, the
action and the preference number, never the text, and the answers of commands (stored in the resend ledger) hold ids
and counts only.
"""
from __future__ import annotations

import time

from . import logs

SCOPE = "prefs"
PHONE_OPS = ("confirm", "decline", "reaffirm", "edit", "remove")      # no add and no clear-all from a phone


def store(mob):
    """The Core's own Preferences when it is connected, else the same memory.db opened here (standalone dashboard)."""
    p = mob.bridge.prefs() if mob.bridge is not None and getattr(mob.bridge, "prefs", None) else None
    if p is None:
        p = getattr(mob, "_prefs", None)
        if p is None:
            from .preferences import Preferences
            from .userdata import open_db
            p = mob._prefs = Preferences(open_db(mob.home))
    return p


def feature_on(mob) -> bool:
    fn = getattr(mob.bridge, "prefs_in_prompt", None) if mob.bridge is not None else None
    try:
        return bool(fn()) if fn else True
    except Exception:
        return True


def view(mob) -> dict:
    from .preferences import TOPICS
    p = store(mob)
    rows = p.items()
    return {"in_prompt": feature_on(mob), "privacy": bool(logs.privacy()), "topics": list(TOPICS),
            "items": [r for r in rows if r["state"] == "confirmed"],
            "proposed": [r for r in rows if r["state"] == "proposed"],
            "declined": [r for r in rows if r["state"] == "declined"]}


def action(mob, d: dict, device: str) -> tuple[int, dict]:
    """One idempotent phone command (run through Continuity.execute by the route)."""
    op = d.get("op")
    if op not in PHONE_OPS:
        return 400, {"error": "op must be " + ", ".join(PHONE_OPS)}
    try:
        pid = int(d.get("pref", 0))
    except (TypeError, ValueError):
        return 400, {"error": "pref must be a number"}
    p = store(mob)
    if not p.get(pid):
        return 404, {"error": "no such preference"}
    by = "phone"
    try:
        # answers hold ids and counts only: the command ledger stores them, and text must not rest there (privacy)
        if op == "confirm":
            r = p.confirm(pid, by=by)
            return 200, {"ok": True, "pref": pid, "state": "confirmed", "conflicts": len(r["conflicts"])}
        if op == "decline":
            return 200, {"ok": p.decline(pid, by=by), "pref": pid}
        if op == "reaffirm":
            return 200, {"ok": p.reaffirm(pid, by=by), "pref": pid}
        if op == "edit":
            p.edit(pid, str(d.get("text", ""))[:400], by=by)
            return 200, {"ok": True, "pref": pid}
        return 200, {"ok": p.remove(pid, by=by), "pref": pid}
    except ValueError as e:                                   # sensitive / empty / duplicate text: nothing stored
        return 400, {"error": str(e)}
    except KeyError:
        return 404, {"error": "no such preference"}
