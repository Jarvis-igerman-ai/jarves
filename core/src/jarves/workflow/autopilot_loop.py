"""Autopilot launch and next-task loop (plan task T3): claim -> start -> link; finish -> stop -> next.

Deterministic ledger code, not an LLM scheduler. It never merges, tags, approves tools, or falls back
to another model/provider. Session start/stop go through injected callables (the Orchestrator bridge
in production, a fake in tests). One step at a time per profile: an in-process lock plus a kernel
file lock make two concurrent step() calls (threads or processes) safe; claim() is atomic in SQLite.

STUBS for the integrator (replace when T1/T2 land):
  * default_policy()/load_policy()  -> T1's policy reader (same JSON keys as the plan section 2).
  * grant: start passes approved=policy["_grant_active"] (default False); T2 supplies the real grant.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from .control import assignment, exclusive, task_context
from .store import Store

AGENT_PREFIX = "ap-"
START_GRACE_S = 300          # a claim with no linked session after this long is an unconfirmed start
PAUSE_S = 600
CLEAN_REFUSALS = {"NOT_ALLOWED_PROJECT", "BUDGET_REACHED", "PROVIDER_MISSING", "AUTH_REQUIRED", "NO_CORE",
                  "MODEL_NOT_ALLOWED", "SECOND_WRITER", "error"}
_LOCK = threading.Lock()
_LAST_SIG: dict[str, tuple] = {}


# ---- STUB (T1 replaces): policy reader -------------------------------------------------------------
def default_policy() -> dict:
    return {"enabled": False, "kill": False, "project": "jarves",
            "versions": ["V4.5.1", "V5", "V6", "V7"], "authorized_versions": ["V4.5.1"],
            "lanes": {"bugs": {"workers": 1, "model": "claude-sonnet-5-5"},
                      "features": {"workers": 1, "model": "claude-sonnet-5-5"}},
            "max_concurrent": 3, "max_starts_per_hour": 6}


def load_policy(home: Path) -> dict:
    policy = default_policy()
    try:
        policy.update(json.loads((Path(home) / "autopilot.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return policy
# ---- end STUB --------------------------------------------------------------------------------------


# ---- production bridge adapters --------------------------------------------------------------------
def bridge_start(profile_home: Path, **req) -> dict | None:
    """Clean refusal -> dict; ambiguous (sent but no answer) -> None."""
    from ..orchestrator.bridge import BridgeClient, BridgeError
    try:
        client = BridgeClient.connect(profile_home)
    except BridgeError:
        return {"ok": False, "code": "NO_CORE", "text": "JARVES Core is not running"}
    try:
        return client.request("call", timeout=60, name="start", args={**req, "by": "autopilot", "lang": "en"})
    except BridgeError:
        return None
    finally:
        client.close()


def bridge_stop(profile_home: Path, session_id: str) -> dict | None:
    from ..orchestrator.bridge import try_request
    return try_request(profile_home, "call", timeout=30, name="stop",
                       args={"ref": session_id, "by": "autopilot", "approved": False, "lang": "en"})


# ---- small persisted state (pause + start rate) ----------------------------------------------------
def _state_path(store: Store) -> Path:
    return store.path.parent / "autopilot_state.json"


def _load(store: Store) -> dict:
    try:
        data = json.loads(_state_path(store).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(store: Store, state: dict) -> None:
    path = _state_path(store)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(path)


def _rank(policy: dict, version: str) -> int:
    versions = policy.get("versions", [])
    return versions.index(version) if version in versions else len(versions)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "_.-" else "-" for c in text)[:60]


def _pick(snapshot: dict, policy: dict, lane: str) -> dict | None:
    tasks = [t for t in snapshot["tasks"] if t["lane"] == lane]
    ready = [t for t in tasks if t.get("ready") and t.get("execution_allowed", True)]
    if lane == "features":
        # Imported/planned scope runs only for versions the owner authorised in the policy.
        authorized = set(policy.get("authorized_versions", []))
        ready = [t for t in ready if t["version"] in authorized]
        # Stay inside the lowest authorised version that still has startable work.
        if ready:
            low = min(_rank(policy, t["version"]) for t in ready)
            ready = [t for t in ready if _rank(policy, t["version"]) == low]
    ready.sort(key=lambda t: (_rank(policy, t["version"]) if lane == "features" else 0,
                              t["priority"], t["updated_at"], t["id"]))
    return ready[0] if ready else None


def step(store: Store, profile_home: Path, policy: dict | None = None, *, start=bridge_start, stop=bridge_stop,
         force: bool = False) -> list[dict]:
    """One idempotent pass. Returns the actions taken (empty when nothing changed)."""
    policy = policy if policy is not None else load_policy(store.path.parent)
    if not policy.get("enabled"):
        return []
    if not _LOCK.acquire(blocking=False):
        return []
    try:
        try:
            with exclusive(store.path.parent / "autopilot.lock"):
                return _step(store, Path(profile_home), policy, start, stop, force)
        except OSError:
            return []          # another process is stepping
    finally:
        _LOCK.release()


def step_safe(store: Store, profile_home: Path, **kw) -> list[dict]:
    """Supervisor entry point: an autopilot failure must never stop local monitoring."""
    try:
        return step(store, profile_home, **kw)
    except Exception as exc:  # noqa: BLE001
        try:
            store.record_event("autopilot_error", f"autopilot step failed: {type(exc).__name__}")
        except Exception:  # noqa: BLE001
            pass
        return []


def _step(store, profile_home, policy, start, stop, force) -> list[dict]:
    actions: list[dict] = []
    state = _load(store)
    now = store.clock()
    snap = store.snapshot()
    sig = (snap["revision"], hashlib.sha256(json.dumps(policy, sort_keys=True, default=str).encode()).hexdigest(),
           now >= state.get("paused_until", 0), int(now // PAUSE_S))
    key = str(store.path)
    if not force and _LAST_SIG.get(key) == sig:
        return actions

    owned = [a for a in snap["agents"] if a.get("autopilot")]

    # 1. Unconfirmed starts (crash between claim and start, e.g. supervisor restart): quarantine, never retry.
    for agent in owned:
        tid = agent.get("task_id")
        if tid and not agent.get("session_id") and now - agent.get("progress_at", now) > START_GRACE_S:
            task = next(t for t in snap["tasks"] if t["id"] == tid)
            if task["state"] == "running":
                store.quarantine(tid, "Autopilot start unconfirmed (no session linked); inspect before retrying")
                actions.append({"task_id": tid, "action": "start_unconfirmed"})

    # 2. Finished work: the worker moved its task away (review / waiting_owner / done / blocked). Close the
    #    session the autopilot started, only ever its own.
    for agent in owned:
        if agent.get("session_id") and not agent.get("task_id"):
            result = stop(profile_home, agent["session_id"])
            if result and result.get("ok"):
                store.link_session(agent["id"], "")
                actions.append({"agent": agent["id"], "action": "stopped"})
            else:
                store.record_event("autopilot_stop", f"{agent['id']}: stop not confirmed; will retry")
                actions.append({"agent": agent["id"], "action": "stop_unconfirmed"})

    # 3. Start the next task per lane.
    if policy.get("kill"):
        _LAST_SIG[key] = sig
        return actions
    snap = store.snapshot()
    state = _load(store)
    now = store.clock()
    starts = [t for t in state.get("starts", []) if now - t < 3600]
    running = [a for a in snap["agents"] if a.get("autopilot") and a.get("task_id")]
    for lane, conf in policy.get("lanes", {}).items():
        if not isinstance(conf, dict) or "workers" not in conf:
            continue
        if now < state.get("paused_until", 0):
            break
        while (sum(1 for a in running if a["lane"] == lane) < int(conf["workers"])
               and len(running) < int(policy.get("max_concurrent", 1))
               and len(starts) < int(policy.get("max_starts_per_hour", 1))):
            task = _pick(snap, policy, lane)
            if not task:
                break
            agent_id = AGENT_PREFIX + _slug(f"{lane}-{task['id']}")
            store.register_agent({"id": agent_id, "label": f"Autopilot {lane}: {task['title'][:80]}", "lane": lane,
                                  "provider": "claude", "project": policy.get("project", ""),
                                  "requested_model": conf.get("model", ""), "auto_dispatch": False})
            store.link_session(agent_id, "", autopilot=True)
            claimed = store.claim(agent_id, task_id=task["id"])
            if not claimed:           # lost the race or no longer ready: no start
                snap = store.snapshot()
                break
            ctx = task_context(store, claimed)
            req = dict(provider="claude", project=policy.get("project", ""), model=conf.get("model", ""),
                       task=assignment(ctx, agent_id, store.path.parent), approved=bool(policy.get("_grant_active")),
                       env={"JARVES_WORKFLOW_AGENT": agent_id})
            try:
                result = start(profile_home, **req)
            except Exception:  # noqa: BLE001
                result = None
            starts.append(now)
            state["starts"] = starts
            _save(store, state)
            if result is None:
                store.quarantine(task["id"], "Autopilot start unconfirmed; check the original session before retrying")
                actions.append({"task_id": task["id"], "action": "start_unconfirmed"})
            elif not result.get("ok"):
                code = str(result.get("code", "error"))
                store.transition(task["id"], {"state": "queued", "checkpoint": f"autopilot: start refused {code}",
                                              "blocker": f"Autopilot start refused: {code}"})
                state["paused_until"] = now + PAUSE_S
                state["pause_reason"] = code
                _save(store, state)
                store.record_event("autopilot_paused", f"Autopilot start refused ({code}); paused for {PAUSE_S // 60} min")
                actions.append({"task_id": task["id"], "action": "refused", "code": code})
                break
            else:
                session = (result.get("session") or {}).get("id") or result.get("session_id") or ""
                if session:
                    store.link_session(agent_id, str(session), provider="claude")
                actions.append({"task_id": task["id"], "agent": agent_id, "action": "started", "session": session})
            snap = store.snapshot()
            running = [a for a in snap["agents"] if a.get("autopilot") and a.get("task_id")]
    _LAST_SIG[key] = (store.snapshot()["revision"],) + sig[1:]
    return actions
