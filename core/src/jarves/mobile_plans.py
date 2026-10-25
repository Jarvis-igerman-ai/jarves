"""Phone view of Goal plans (V5 gap G4, issue #184): read-only steps and status, plus the two stopping controls.

A paired phone with the `tasks` scope (the scope that already covers goal actions) sees each plan's title, state,
progress and the steps (tool name, the plan's own one-line reason, status, a short error). It can PAUSE a plan that is
ready or running and CANCEL an open plan: both only stop work. Approving, resuming, retrying, skipping and deleting
(everything that starts or repeats a step) stay on the computer, where the approval popup shows the steps, and the
phone says so. Tool arguments, step results and evidence are never sent to the phone.
Same store as the dashboard Goal plans page (`goal_plans.PlanStore`), so both clients always show one state.
The audit log holds the device, the action and the plan number only.
"""
from __future__ import annotations

from . import logs

SCOPE = "tasks"
PHONE_OPS = ("pause", "cancel")
PC_ONLY_OPS = ("approve", "resume", "retry", "skip", "delete")
SHOWN = 30                                       # newest plans listed
SHORT = 160


def store(mob):
    p = mob.bridge.plans() if mob.bridge is not None and getattr(mob.bridge, "plans", None) else None
    if p is None:
        p = getattr(mob, "_plans", None)
        if p is None:
            from .goal_plans import PlanStore
            from .userdata import open_db
            p = mob._plans = PlanStore(open_db(mob.home))
    return p


def feature_on(mob) -> bool:
    fn = getattr(mob.bridge, "plans_on", None) if mob.bridge is not None else None
    try:
        return bool(fn()) if fn else True
    except Exception:
        return True


def _short(s: object) -> str:
    return " ".join(str(s or "").split())[:SHORT]


def _can(state: str) -> list[str]:
    out = []
    if state in ("ready", "running"):
        out.append("pause")
    if state not in ("done", "cancelled"):
        out.append("cancel")
    return out


def brief(p: dict) -> dict:
    steps = [{"n": int(s["idx"]) + 1, "tool": str(s["tool"])[:64], "why": _short(s.get("why")), "status": s["status"],
              "consequential": bool(s.get("consequential")), "error": _short(s.get("error"))} for s in p.get("steps", [])]
    return {"id": p["id"], "title": _short(p["title"]), "state": p["state"], "progress": p.get("progress", 0),
            "goal": p.get("goal_id") or None, "note": _short(p.get("note")), "steps": steps, "can": _can(p["state"]),
            "needs_pc": p["state"] in ("draft", "paused", "blocked", "failed")}


def view(mob) -> dict:
    plans = store(mob).list()[:SHOWN]
    return {"enabled": feature_on(mob), "plans": [brief(p) for p in plans], "pc_only": list(PC_ONLY_OPS)}


def action(mob, d: dict, device: str) -> tuple[int, dict]:
    """One idempotent phone command (run through Continuity.execute by the route)."""
    op = d.get("op")
    if op in PC_ONLY_OPS:
        return 403, {"error": "starting or repeating a step is done on the computer", "code": "PLAN_PC_ONLY"}
    if op not in PHONE_OPS:
        return 400, {"error": "op must be " + " or ".join(PHONE_OPS)}
    try:
        pid = int(d.get("plan", 0))
    except (TypeError, ValueError):
        return 400, {"error": "plan must be a number"}
    st = store(mob)
    try:
        p = st.pause(pid, by="phone") if op == "pause" else st.cancel(pid, by="phone")
    except KeyError:
        return 404, {"error": "no such plan"}
    except ValueError as e:
        return 409, {"error": str(e), "code": "PLAN_STATE"}
    logs.info("mobile", "MOBILE_PLAN_" + op.upper(), "a plan was stopped from a phone", plan=pid, device=device)
    return 200, {"ok": True, "plan": pid, "state": p["state"]}
