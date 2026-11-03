"""One-shot worker-count monitor and manager handover signal. Deterministic ledger code, no LLM, no process control.

`worker-watch` is run by an outside wrapper (claude-j) every few minutes. It is idempotent: dedupe and rate-limit
state live in workflow_meta, never in files. `handover` / `handover-state` let a wrapper wait for the manager to
write a checkpoint before it restarts or nudges it.
"""
from __future__ import annotations

import json
from pathlib import Path

from .store import NATIVE_GRACE_S, Store

WORKER_LANES = {"bugs", "features", "any"}
MIN_INTERVAL_S = 300
MAX_ALLOWED = 8
HANDOVER_GRACE_S = NATIVE_GRACE_S  # a permission prompt or long tool call emits no hooks; same grace as the store
SELF_ACTIVITY_S = 30  # the handover command's own tool hooks land just after it


def allowed_workers(policy: dict) -> int:
    lanes = policy.get("lanes") or {}
    total = 0
    for name in ("bugs", "features"):
        value = (lanes.get(name) or {}).get("workers", 0)
        total += value if type(value) is int and value > 0 else 0
    return min(total, MAX_ALLOWED)


def live_native_workers(agents, manager_id, now):
    """Native worker subagents that are running with a fresh heartbeat; the manager and merger are not workers."""
    return [a["id"] for a in agents
            if a.get("provider") == "claude-native" and a.get("parent_id") and a["id"] != manager_id
            and a.get("lane") in WORKER_LANES and a.get("status") == "running"
            and now - a.get("heartbeat_at", 0) < NATIVE_GRACE_S]


def evaluate(running, allowed, ready, state, now):
    """Pure decision. Returns (numbers, action, new_state); action is None, 'short', 'over' or 'recovered'."""
    shortfall = max(0, allowed - running) if ready else 0
    over = max(0, running - allowed)
    numbers = {"running": running, "allowed": allowed, "shortfall": shortfall, "over": over,
               "ready_ids": list(ready)[:10]}
    problem = "short" if shortfall else "over" if over else ""
    state = dict(state or {})
    if not problem:
        action = "recovered" if state.get("active") else None
        return numbers, action, {"active": "", "sent_at": state.get("sent_at", 0)}
    if now - state.get("sent_at", 0) < MIN_INTERVAL_S:
        return numbers, None, {**state, "active": problem}
    return numbers, problem, {"active": problem, "sent_at": now, "shortfall": shortfall, "over": over}


def watch(store: Store, policy: dict, send=None, dry: bool = False) -> dict:
    """Count, decide, and (when needed) tell the manager once. Never raises."""
    try:
        snap = store.snapshot()
        now = store.clock()
        manager = store.followup_manager_id()
        running = live_native_workers(snap["agents"], manager, now)
        ready = [t for t in snap["tasks"] if t.get("ready")]
        numbers, action, state = evaluate(len(running), allowed_workers(policy), [t["id"] for t in ready],
                                          store.get_meta("worker_watch", {}), now)
        numbers["action"] = action or "none"
        if dry:
            return numbers
        if action:
            lane = (ready[0].get("lane") if ready else "") or "any"
            if action == "short":
                subject = f"WORKERS_SHORT: running {numbers['running']} of {numbers['allowed']}"
                body = (f"Ready runnable tasks: {', '.join(numbers['ready_ids'])}. Next lane: {lane}. "
                        "Start another independent worker for the next task.")
            elif action == "over":
                subject = f"WORKERS_OVER: running {numbers['running']} of {numbers['allowed']}"
                body = "More native workers run than the policy lanes allow; start no more and check for stragglers."
            else:
                subject, body = "WORKERS_OK", f"Worker count back to normal: running {numbers['running']} of {numbers['allowed']}."
            store.record_event("worker_watch", subject)
            if action != "recovered":
                (send or store.post_manager_note)(subject, body)
        store.set_meta("worker_watch", state)
        return numbers
    except Exception as exc:  # noqa: BLE001  a monitor must never break its caller
        return {"error": str(exc)[:200], "running": 0, "allowed": 0, "shortfall": 0, "over": 0, "ready_ids": [], "action": "error"}


def write_handover(store: Store, note: str) -> dict:
    note = (note or "").strip()
    if not note:
        raise ValueError("handover needs a note")
    value = {"at": store.clock(), "note": note[:2000]}
    store.set_meta("manager_handover", value)
    store.record_event("manager_handover", "Manager wrote a handover checkpoint")
    return value


def handover_state(store: Store, grace_s: int = HANDOVER_GRACE_S) -> dict:
    """ready=True means a restart or nudge may go ahead; the reason is always stated."""
    now = store.clock()
    snap = store.snapshot()
    manager = store.followup_manager_id()
    agent = next((a for a in snap["agents"] if a["id"] == manager), None)
    handover = store.get_meta("manager_handover", {})
    if agent is None:
        return {"ready": True, "age_s": 0, "reason": "no registered manager session"}
    last = agent.get("heartbeat_at", 0)
    idle_s = round(now - last, 1)
    if handover and handover.get("at", 0) >= last - SELF_ACTIVITY_S:
        return {"ready": True, "age_s": round(now - handover["at"], 1), "reason": "handover checkpoint is newer than last activity"}
    if idle_s >= grace_s:
        return {"ready": True, "age_s": idle_s, "reason": f"no handover, but the manager was silent for {grace_s} s grace period"}
    return {"ready": False, "age_s": idle_s,
            "reason": "manager active since its last handover; wait for `handover --note` or the grace period"}
