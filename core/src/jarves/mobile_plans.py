"""Phone view of Goal plans (V5 gap G4, issue #184): read-only steps and status, plus the two stopping controls.

A paired phone with the `tasks` scope (the scope that already covers goal actions) sees each plan's title, state,
progress and the steps (tool name, the plan's own one-line reason, status, a short error). It can PAUSE a plan that is
ready or running and CANCEL an open plan (both only stop work), RESUME a plan the owner paused and RETRY a failed one
(#340, owner decision 25 Oct). Those two only continue a plan the owner ALREADY approved on the computer, through the
same guards (every step that changes something still asks in the approval popup, which stays computer-only, #200).
They are refused for a plan the untrusted-content guard tainted, for blocked or unknown steps (the step may already
have run) and for retries past the attempt limit: those stay on the computer. Approving, skipping and deleting stay on
the computer. Tool arguments, step results and evidence are never sent to the phone.
Same store as the dashboard Goal plans page (`goal_plans.PlanStore`), so both clients always show one state.
The audit log holds the device, the action and the plan number only.
"""
from __future__ import annotations

import re

from . import logs
from .goal_plans import MAX_ATTEMPTS

SCOPE = "tasks"
PHONE_OPS = ("pause", "cancel", "resume", "retry")
PC_ONLY_OPS = ("approve", "skip", "delete")
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


_CODE_HEAD = re.compile(r"([A-Z][A-Z0-9_]{2,40}): .+", re.S)


def _phone_error(s: object) -> str:
    """F12 #206: a failed step's error is 'CODE: tool message'; the phone gets the code only, never the tool's own text."""
    m = _CODE_HEAD.fullmatch(" ".join(str(s or "").split()))
    if m:
        return m.group(1)
    return "STEP_FAILED" if str(s or "").strip() else ""      # never the tool's free text


def _clean(p: dict) -> bool:
    """Only an untainted plan with no blocked or unknown step may be continued from the phone."""
    return not p.get("tainted") and not any(s["status"] in ("blocked", "unknown", "running") for s in p.get("steps", []))


def _can(p: dict) -> list[str]:
    state, out = p["state"], []
    if state in ("ready", "running"):
        out.append("pause")
    if state == "paused" and _clean(p):
        out.append("resume")
    if state == "failed" and _clean(p) and any(s["status"] == "failed" and s.get("attempts", 0) < MAX_ATTEMPTS
                                                for s in p.get("steps", [])):
        out.append("retry")
    if state not in ("done", "cancelled"):
        out.append("cancel")
    return out


def brief(p: dict) -> dict:
    steps = [{"n": int(s["idx"]) + 1, "tool": str(s["tool"])[:64], "why": _short(s.get("why")), "status": s["status"],
              "consequential": bool(s.get("consequential")), "error": _phone_error(s.get("error"))} for s in p.get("steps", [])]
    can = _can(p)
    return {"id": p["id"], "title": _short(p["title"]), "state": p["state"], "progress": p.get("progress", 0),
            "goal": p.get("goal_id") or None, "note": _short(p.get("note")), "steps": steps, "can": can,
            "needs_pc": p["state"] in ("draft", "paused", "blocked", "failed") and not ({"resume", "retry"} & set(can))}


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
        if op in ("resume", "retry"):                  # continue an approved plan: re-check on the stored plan, not on the client
            cur = st.get(pid)
            if cur is None:
                raise KeyError(pid)
            if op not in _can(cur):
                return 403, {"error": "this plan needs the computer (blocked, unknown or too many attempts, or not in a state "
                                      "that can " + op + ")", "code": "PLAN_PC_ONLY"}
        p = {"pause": st.pause, "cancel": st.cancel, "resume": st.resume, "retry": st.retry}[op](pid, by="phone")
    except KeyError:
        return 404, {"error": "no such plan"}
    except ValueError as e:
        return 409, {"error": str(e), "code": "PLAN_STATE"}
    logs.info("mobile", "MOBILE_PLAN_" + op.upper(), "a plan was changed from a phone", plan=pid, device=device)
    return 200, {"ok": True, "plan": pid, "state": p["state"]}
