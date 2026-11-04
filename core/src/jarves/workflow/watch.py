"""One-shot worker-count monitor and manager handover signal. Deterministic ledger code, no LLM, no process control.

`worker-watch` is run by an outside wrapper (claude-j) every few minutes. It is idempotent: dedupe and rate-limit
state live in workflow_meta, never in files. `handover` / `handover-state` let a wrapper wait for the manager to
write a checkpoint before it restarts or nudges it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .store import NATIVE_GRACE_S, Store

WORKER_LANES = {"bugs", "features", "any"}
MIN_INTERVAL_S = 300
MAX_ALLOWED = 8
HANDOVER_GRACE_S = NATIVE_GRACE_S  # a permission prompt or long tool call emits no hooks; same grace as the store
SELF_ACTIVITY_S = 30  # the handover command's own tool hooks land just after it
IDLE_ALARM_S = 30 * 60  # free slots + open work + no worker start / task transition for this long -> owner notify
RUN_DEAD_S = 15 * 60  # worker-watch itself has not run for this long: the wrapper is dead
WORK_LABELS = {"agent-doable", "bug", "p0", "p1", "p2", "p3"}
OWNER_ONLY = {"owner-gated", "no-automerge"}
ACTIVITY_KINDS = ("agent_registered", "task_claimed", "task_transition", "task_flow", "task_created")


def open_work(snap: dict) -> dict:
    """Open work with no worker, from the already synced GitHub data (no network). Pure, deterministic."""
    source = snap.get("source") or {}
    covered = set()
    for task in snap.get("tasks", []):
        if task.get("state") in ("done", "merged", "closed"):
            continue
        for row in task.get("source_issues") or []:
            covered.add(int(row.get("number", 0)))
        found = re.match(r"gh-(\d+)", str(task.get("id", "")))
        if found:
            covered.add(int(found.group(1)))
    def gated(labels):
        low = {str(x).lower() for x in labels}
        return bool(low & OWNER_ONLY) or any(x.startswith("known-issue-") for x in low)

    issues = [i["number"] for i in source.get("open_issues") or []
              if i.get("state", "open") == "open" and i["number"] not in covered
              and {str(x).lower() for x in i.get("labels", [])} & WORK_LABELS and not gated(i.get("labels", []))]
    # Not idle work: drafts, PRs with auto-merge (queue data is not synced), and Tier R PRs (no-automerge) that
    # wait for review or the owner; those are reported separately.
    open_prs = [p for p in source.get("open_prs") or [] if not p.get("draft") and not p.get("auto_merge")]
    tier_r = [p["number"] for p in open_prs if "no-automerge" in {str(x).lower() for x in p.get("labels", [])}]
    prs = [p["number"] for p in open_prs if p["number"] not in tier_r]
    return {"issues": issues[:20], "issue_count": len(issues), "prs": prs[:20], "pr_count": len(prs),
            "tier_r_prs": tier_r[:20], "synced_at": source.get("synced_at", 0)}


def last_activity(store: Store) -> float:
    marks = ",".join("?" * len(ACTIVITY_KINDS))
    with store._tx() as db:
        row = db.execute(f"SELECT MAX(ts) FROM workflow_events WHERE kind IN ({marks})", ACTIVITY_KINDS).fetchone()
    return float(row[0] or 0)


def idle_decision(free: int, work: dict, ready: int, idle_s: float, idle_sent_at: float, now: float) -> bool:
    """True when the owner must be told: slots free, work waiting, nothing moved for IDLE_ALARM_S; rate limited."""
    waiting = work["issue_count"] + work["pr_count"] + ready
    if free <= 0 or not waiting or idle_s < IDLE_ALARM_S:
        return False
    return now - idle_sent_at >= IDLE_ALARM_S


def run_age(store: Store, now: float | None = None) -> dict:
    """Age of the last worker-watch run, for health / status. Never-run is reported, not alarmed."""
    now = store.clock() if now is None else now
    last = (store.get_meta("worker_watch_run", {}) or {}).get("at", 0)
    if not last:
        return {"last_run_at": 0, "age_s": None, "state": "never"}
    age = max(0.0, round(now - last, 1))  # a clock that went backwards reads as 0, never a crash or false alarm
    return {"last_run_at": last, "age_s": age, "state": "dead" if age > RUN_DEAD_S else "ok"}


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
        idle_sent_at = (store.get_meta("worker_watch", {}) or {}).get("idle_sent_at", 0)
        work = open_work(snap)
        idle_s = max(0.0, now - last_activity(store))
        free = max(0, allowed_workers(policy) - len(running))
        condition = idle_decision(free, work, len(ready), idle_s, 0, now)  # the state itself, without the note rate limit
        idle = condition and now - idle_sent_at >= IDLE_ALARM_S
        numbers["open_work"] = work
        numbers["idle_alarm"] = idle
        if dry:
            return numbers
        store.set_meta("worker_watch_run", {"at": now})
        if idle:
            subject = f"IDLE_WORK: no worker or task moved for {int(idle_s // 60)} min"
            body = (f"{free} free worker slots; open issues without a task: {work['issue_count']} "
                    f"({', '.join('#%d' % n for n in work['issues'][:8])}); open PRs waiting: {work['pr_count']}; "
                    f"Tier R PRs waiting for review: {len(work['tier_r_prs'])}; ready tasks: {len(ready)}. Start a worker or assign work.")
            store.record_event("worker_watch", subject)
            (send or store.post_manager_note)(subject, body)
            idle_sent_at = now
        # No owner push (Core notify toasts and speaks). The state is a dashboard incident (IDLE_WORK) instead;
        # the manager note above repeats at most every 30 minutes.
        if condition:
            episode = store.get_meta("worker_watch_idle", {}) or {}
            store.set_meta("worker_watch_idle", {"since": episode.get("since", now), "issues": work["issue_count"],
                                                 "prs": work["pr_count"], "free": free})
        elif store.get_meta("worker_watch_idle", {}):
            store.set_meta("worker_watch_idle", {})
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
        store.set_meta("worker_watch", {**state, "idle_sent_at": idle_sent_at})
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
