"""Autopilot watchdog actions and steward task creation (T4).

Deterministic code, no LLM and no sleep loop: ``watchdog_step`` / ``steward_step``
are called by the autopilot ``step()`` right after ``Store.tick`` and take an
explicit ``now`` so tests drive a fake clock. Process effects are injected
(``start``, ``stop``, ``notify``); nothing here starts a worker by itself.

Hard limits: this module only creates ledger tasks and owner questions. It has
no code path that integrates pull requests or publishes releases; the steward
role stays with the manager and the owner.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from .store import Store

OWNED_PREFIX = "ap-"  # agents pre-registered by the autopilot: ap-<lane>-<task>
DEFAULTS = {"idle_s": 300, "stale_s": 120, "stuck_s": 1800, "max_restarts_per_task": 1, "permission_alert_s": 600}
STATE_KEY = "autopilot_watch"
ALERTS_KEY = "autopilot_alerts"


# ---- small persistent state (meta rows, so a supervisor restart loses nothing) -------------------------------

def _load(store: Store, key: str, default):
    with store._tx() as db:
        row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _save(store: Store, key: str, value) -> None:
    with store._tx() as db:
        store._set_meta(db, key, value)


def _watch_policy(policy: dict) -> dict:
    return {**DEFAULTS, **(policy.get("watchdog") or {})}


def raise_alert(store: Store, kind: str, summary: str, *, task_id=None, agent_id=None, notify=None, notify_text=""):
    """Record an autopilot alert once per (kind, task, agent) key; push to the owner once (deduplicated)."""
    key = f"{kind}|{task_id or ''}|{agent_id or ''}"
    alerts = _load(store, ALERTS_KEY, [])
    if any(a["key"] == key for a in alerts):
        return False
    alerts.append({"key": key, "kind": kind, "summary": summary, "task_id": task_id, "agent_id": agent_id,
                   "at": store.clock()})
    _save(store, ALERTS_KEY, alerts[-50:])
    if notify is not None:
        notify(kind, notify_text or summary)
    return True


def autopilot_alerts(store: Store) -> list:
    return _load(store, ALERTS_KEY, [])


# ---- lane selection ------------------------------------------------------------------------------------------

def _version_rank(policy: dict, version: str) -> int:
    order = list(policy.get("versions") or [])
    return order.index(version) if version in order else len(order)


def pick_ready(snapshot: dict, policy: dict, lane: str):
    """First ready task of a lane: priority, then version order, then age."""
    tasks = [t for t in snapshot["tasks"] if t["lane"] == lane and t.get("ready")]
    tasks.sort(key=lambda t: (t["priority"], _version_rank(policy, t.get("version", "")), t["updated_at"], t["id"]))
    return tasks[0] if tasks else None


def _lane_workers(snapshot: dict, lane: str) -> list:
    return [a for a in snapshot["agents"] if a["lane"] == lane and a["id"].startswith(OWNED_PREFIX)
            and a["status"] in {"running", "blocked"} and a.get("task_id")]


# ---- watchdog ------------------------------------------------------------------------------------------------

def watchdog_step(store: Store, policy: dict, *, start, stop, notify=None, now: float | None = None) -> list[str]:
    """Act on the tick result. Returns a list of action names (for tests and logs).

    start(lane, task) -> None   begins one worker for that task (the T3 launch path claims and starts).
    stop(agent) -> None         stops an autopilot-OWNED session (interrupt, then stop); never called for foreign ones.
    notify(kind, text) -> None  Core notify bridge; called once per new alert key.
    """
    cfg = _watch_policy(policy)
    now = store.clock() if now is None else now
    state = _load(store, STATE_KEY, {"idle_since": {}, "restarts": {}, "perm_since": {}})
    actions: list[str] = []
    if policy.get("kill") or not policy.get("enabled"):
        return actions
    snap = store.snapshot()
    agents = {a["id"]: a for a in snap["agents"]}
    tasks = {t["id"]: t for t in snap["tasks"]}

    # 1. permission waits: alert only; after permission_alert_s an away-alert through Core notify
    live_perm = set()
    for alert in snap["alerts"]:
        if alert["kind"] != "worker_waiting_permission":
            continue
        aid = alert["agent_id"]
        live_perm.add(aid)
        first = state["perm_since"].setdefault(aid, now)
        raise_alert(store, "permission_wait", alert["summary"], agent_id=aid)
        if now - first >= cfg["permission_alert_s"]:
            if raise_alert(store, "permission_wait_away", f"{aid}: waiting for approval over 10 minutes", agent_id=aid,
                           notify=notify, notify_text="A worker waits for your approval or denial in the popup."):
                actions.append(f"away_alert:{aid}")
    state["perm_since"] = {k: v for k, v in state["perm_since"].items() if k in live_perm}

    # 2. stalled / offline workers
    for alert in snap["alerts"]:
        if alert["kind"] not in {"worker_stalled", "worker_offline"} or not alert.get("task_id"):
            continue
        agent, task = agents.get(alert["agent_id"]), tasks.get(alert["task_id"])
        if not agent or not task:
            continue
        if not agent["id"].startswith(OWNED_PREFIX):
            # Foreign or native session: never resurrect, never stop; tell the owner only.
            if raise_alert(store, alert["kind"] + "_foreign", alert["summary"], task_id=task["id"],
                           agent_id=agent["id"], notify=notify):
                actions.append(f"alert_foreign:{agent['id']}")
            continue
        count = state["restarts"].get(task["id"], 0)
        if count >= cfg["max_restarts_per_task"]:
            if task["state"] == "blocked" and "autopilot: stalled twice" in task.get("blocker", ""):
                continue
            store.transition(task["id"], {"state": "blocked",
                                          "blocker": "autopilot: stalled twice, owner decision needed"})
            raise_alert(store, "task_blocked", f"{task['title']}: stalled twice, no further restart",
                        task_id=task["id"], agent_id=agent["id"], notify=notify)
            actions.append(f"blocked:{task['id']}")
            continue
        if agent["status"] != "blocked" and agent["status"] != "offline":
            continue
        if stop(agent) is False:       # None = a caller with no result to report; False = stop not confirmed
            raise_alert(store, "stop_unconfirmed", f"{task['title']}: could not stop the stalled session; not restarted",
                        task_id=task["id"], agent_id=agent["id"], notify=notify)
            actions.append(f"stop_unconfirmed:{task['id']}")
            continue
        store.transition(task["id"], {"state": "queued", "recovery_confirmed": True,
                                      "checkpoint": "autopilot: stalled, session stopped"})
        state["restarts"][task["id"]] = count + 1
        raise_alert(store, "worker_restarted", f"{task['title']}: restarted once", task_id=task["id"],
                    agent_id=agent["id"], notify=notify)
        actions.append(f"restart:{task['id']}")

    # 3. idle lane with queued work -> start after idle_s; a stopped lane moves on to the next ready task
    snap = store.snapshot()
    for lane, conf in (policy.get("lanes") or {}).items():
        wanted = conf.get("workers", 0) if isinstance(conf, dict) else 0
        if not wanted:
            continue
        task = pick_ready(snap, policy, lane)
        if task is None or len(_lane_workers(snap, lane)) >= wanted:
            state["idle_since"].pop(lane, None)
            continue
        since = state["idle_since"].setdefault(lane, now)
        if now - since >= cfg["idle_s"]:
            raise_alert(store, "lane_idle", f"lane {lane} idle with queued work: {task['title']}", task_id=task["id"])
            start(lane, task)
            state["idle_since"].pop(lane, None)
            actions.append(f"start:{lane}:{task['id']}")
    _save(store, STATE_KEY, state)
    return actions


# ---- steward: PR review tasks, daily beta check, manual tests ------------------------------------------------

def _risk_module():
    path = Path(__file__).resolve().parents[3] / "tools" / "risk_class.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("jarves_risk_class", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_tier_r(files, labels) -> bool:
    """Read-only. Unsure counts as Tier R: no risk tool, no file list, or any high-risk path."""
    module = _risk_module()
    if module is None or not files or set(labels) & set(module.BLOCKING_LABELS):
        return True
    # The autopilot's own code is never auto-integrated.
    return any(module.is_high_risk(f) or "workflow/autopilot" in f for f in files)


def _create_once(store: Store, data: dict) -> bool:
    if any(t["id"] == data["id"] for t in store.snapshot()["tasks"]):
        return False
    store.create_task(data)
    return True


def steward_step(store: Store, policy: dict, prs, files_for, *, notify=None) -> list[str]:
    """One review or steward task per (PR, head SHA). Tier R PRs get an Opus review task, never a steward task."""
    if policy.get("kill") or not policy.get("enabled") or not (policy.get("lanes") or {}).get("steward", {}).get("on", False):
        return []
    made = []
    lanes = policy.get("lanes") or {}
    for pr in prs:
        sha = str(pr.get("head_sha") or pr.get("sha") or "nosha")[:8]
        number = pr["number"]
        files = files_for(pr)
        if is_tier_r(files, set(pr.get("labels") or [])):
            model = (lanes.get("review") or {}).get("model", "claude-opus-5-5")
            data = {"id": f"review-pr-{number}-{sha}", "title": f"Independent Tier R review of PR #{number}",
                    "lane": "release", "version": "ops", "priority": 1, "required_validation": "automated",
                    "scope": {"model": model, "pr": number, "kind": "tier_r_review"},
                    "acceptance": [{"id": "C1", "label": f"Review verdict for PR #{number} recorded; owner decides"}]}
        else:
            model = (lanes.get("steward") or {}).get("model", "claude-haiku-5-5")
            data = {"id": f"steward-pr-{number}-{sha}", "title": f"Steward PR #{number}",
                    "lane": "release", "version": "ops", "priority": 2, "required_validation": "automated",
                    "scope": {"model": model, "pr": number, "kind": "steward"},
                    "acceptance": [{"id": "C1", "label": f"PR #{number} checked per the operating model steward rules"}]}
        if _create_once(store, data):
            made.append(data["id"])
    return made


BETA_STEPS = ("backup now", "update install --channel beta --yes --silent", "doctor", "selftest", "speedtest",
              "status", "features diff")


def beta_step(store: Store, policy: dict, main_sha: str, ci_green: bool, day: str, *, contains_p01_fix=False) -> list[str]:
    """One beta-verify task per day (plus one per beta that carries a P0/P1 fix), only for a new green main SHA."""
    if policy.get("kill") or not policy.get("enabled") or not ci_green or not main_sha:
        return []
    state = _load(store, "autopilot_beta", {"sha": "", "days": []})
    if state["sha"] == main_sha:
        return []
    state["sha"] = main_sha
    ident = None
    if contains_p01_fix:
        ident = f"beta-verify-{main_sha[:8]}"
    elif day not in state["days"]:
        ident = f"beta-verify-{day}"
        state["days"] = (state["days"] + [day])[-30:]
    _save(store, "autopilot_beta", state)
    if not ident:
        return []
    made = _create_once(store, {
        "id": ident, "title": f"Verify beta of main {main_sha[:8]} on the owner PC", "lane": "release",
        "version": "ops", "priority": 1, "resources": ["owner-pc"], "required_validation": "app",
        "scope": {"model": (policy.get("lanes") or {}).get("steward", {}).get("model", "claude-haiku-5-5"),
                  "kind": "beta_verify", "steps": list(BETA_STEPS), "main_sha": main_sha},
        "acceptance": [{"id": f"C{i + 1}", "label": f"jarvis {s} succeeded, silent, backup first"}
                       for i, s in enumerate(BETA_STEPS)]})
    return [ident] if made else []


def manual_test_step(store: Store) -> list[str]:
    """Owner item for tasks that need a device or owner-run check. Only that task waits."""
    asked = []
    snap = store.snapshot()
    pending = {q["task_id"] for q in snap["questions"] if q["status"] in {"pending", "deferred"}}
    for task in snap["tasks"]:
        scope = task.get("scope")  # plain string ("required") for imported scope, dict for autopilot-made tasks
        needs = task["required_validation"] == "device" or (isinstance(scope, dict) and scope.get("owner_test"))
        if not needs or task["state"] != "review" or task["id"] in pending:
            continue
        if any(q["id"] == f"manual-{task['id']}" for q in snap["questions"]):
            continue
        sheet = ", ".join(task.get("source_paths") or []) or "see task acceptance"
        store.ask({"id": f"manual-{task['id']}", "task_id": task["id"], "blocks": True,
                   "title": f"Manual test needed: {task['title']}",
                   "context": f"Test sheet: {sheet}. Record the result with check --criterion --evidence "
                              "(an answer text is not evidence).",
                   "checkpoint": "autopilot: ready for the owner's manual test"})
        asked.append(task["id"])
    return asked


GATE_CHOICES = [
    {"id": "later-today", "label": "Schedule it for later today (Recommended)"},
    {"id": "tell-me", "label": "Tell me when ready"},
    {"id": "skip-version", "label": "Skip for this version"},
]
GATE_CAP = 15


def _version_key(version) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", str(version or ""))) or (999,)


def gate_questions_step(store: Store) -> list[str]:
    """One non-blocking owner question per owner-gated queued task, so 'what needs me' is visible.

    Created once (any existing gate-<task> question, answered or not, stops a new one), never blocks the task
    or its lane, at most GATE_CAP new ones per call, ordered by version then priority.
    """
    snap = store.snapshot()
    known = {q["id"] for q in snap["questions"]}
    todo = [t for t in snap["tasks"]
            if t.get("owner_gate") and t["state"] == "queued" and t.get("execution_allowed", True) is False
            and f"gate-{t['id']}" not in known]
    todo.sort(key=lambda t: (_version_key(t.get("version")), t.get("priority", 9), t["id"]))
    asked = []
    for task in todo[:GATE_CAP]:
        link = task.get("source_url") or (
            f"https://github.com/igalgerman/jarves/issues/{task['issue_numbers'][0]}" if task.get("issue_numbers") else "")
        scope = task.get("scope")
        minutes = scope.get("owner_minutes") if isinstance(scope, dict) else None
        context = (f"Task: {task['id']} ({task['title']}). Version: {task.get('version') or '?'}. "
                   f"{'Link: ' + link + '. ' if link else ''}Time: {minutes or 'about 15'} min. "
                   "Safe default: nothing is done or approved without you.")
        try:
            store.ask({"id": f"gate-{task['id']}", "task_id": task["id"], "blocks": False,
                       "title": str(task["owner_gate"])[:500], "context": context, "choices": GATE_CHOICES})
        except ValueError:
            continue
        asked.append(task["id"])
    return asked
