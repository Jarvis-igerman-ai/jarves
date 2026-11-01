"""Autopilot launch and next-task loop (plan task T3): claim -> start -> link; finish -> stop -> next.

Deterministic ledger code, not an LLM scheduler. It never merges, tags, approves tools, or falls back
to another model/provider. Session start/stop go through injected callables (the Orchestrator bridge
in production, a fake in tests). One step at a time per profile: an in-process lock plus a kernel
file lock make two concurrent step() calls (threads or processes) safe; claim() is atomic in SQLite.

Policy comes from autopilot.load (T1). The owner grant (T2) lives in the Orchestrator: with by="autopilot" the service ignores any
approved flag and starts without a popup only on an exact grant match, otherwise it asks/refuses as usual. The T4 watchdog/steward/manual-test passes run inside _step.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

from .control import assignment, exclusive, task_context
from .store import Store

AGENT_PREFIX = "ap-"
START_GRACE_S = 300          # a claim with no linked session after this long is an unconfirmed start
PAUSE_S = 600
START_TIMEOUT_S = 240
CLEAN_REFUSALS = {"NOT_ALLOWED_PROJECT", "BUDGET_REACHED", "PROVIDER_MISSING", "AUTH_REQUIRED", "NO_CORE",
                  "MODEL_NOT_ALLOWED", "SECOND_WRITER", "error"}
log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_LAST_SIG: dict[str, tuple] = {}


# ---- policy: T1's reader (re-read every step; a bad file fails closed) ----------------------------
_CLAIM_FAILED: set = set()


def load_policy(home: Path) -> dict:
    from . import autopilot
    policy, error = autopilot.load(home)
    if error:
        policy["enabled"] = False
    return policy


def bridge_notify(profile_home: Path, kind: str, text: str) -> None:
    """Best effort owner push through the running Core; the console alert list is the reliable channel."""
    try:
        from ..orchestrator.bridge import try_request
        r = try_request(profile_home, "call", timeout=10, name="notify",
                        args={"kind": f"autopilot_{kind}", "text": text[:500], "by": "autopilot", "lang": "en"})
        if not r or not r.get("ok"):
            log.warning("autopilot owner notify not delivered (%s): %s", kind, (r or {}).get("code", "no Core"))
    except Exception:  # noqa: BLE001
        log.exception("autopilot owner notify failed (%s)", kind)


# ---- production bridge adapters --------------------------------------------------------------------
def bridge_start(profile_home: Path, **req) -> dict | None:
    """Clean refusal -> dict; ambiguous (sent but no answer) -> None."""
    from ..orchestrator.bridge import BridgeClient, BridgeError
    try:
        client = BridgeClient.connect(profile_home)
    except BridgeError:
        return {"ok": False, "code": "NO_CORE", "text": "JARVES Core is not running"}
    try:
        # Longer than the worst case inside start (git fetch 60 s + rev-parse 20 s + worktree add 120 s).
        return client.request("call", timeout=START_TIMEOUT_S, name="start", args={**req, "by": "autopilot", "lang": "en"})
    except BridgeError:
        return None
    finally:
        client.close()


def bridge_find(profile_home: Path, agent_id: str) -> str:
    """The live session the Core started for this workflow agent ("" when none or no Core)."""
    from ..orchestrator.bridge import try_request
    r = try_request(profile_home, "call", timeout=15, name="find_worker", args={"ref": agent_id, "by": "autopilot"})
    return str((r or {}).get("session_id") or "") if r and r.get("ok") else ""


def stop_confirmed(result: dict | None) -> bool:
    """True when the session is stopped: the Core said ok, or it has no LIVE session by that id any more (already
    stopped or dead). A real failed stop of a live session (or no answer at all) is NOT confirmed."""
    return bool(result and (result.get("ok") or result.get("code") == "NO_SUCH_SESSION"))


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


def _pick(snapshot: dict, policy: dict, lane: str, skip=()) -> dict | None:
    tasks = [t for t in snapshot["tasks"] if t["lane"] == lane and t["id"] not in skip]
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
         notify=bridge_notify, find=bridge_find, force: bool = False, usage=None) -> list[dict]:
    """One idempotent pass. Returns the actions taken (empty when nothing changed)."""
    policy = policy if policy is not None else load_policy(store.path.parent)
    from .observability import record_decision
    if not policy.get("enabled"):
        record_decision(store, policy, [])
        return []
    if not _LOCK.acquire(blocking=False):
        return []
    try:
        try:
            with exclusive(store.path.parent / "autopilot.lock"):
                done = _step(store, Path(profile_home), policy, start, stop, notify, force, find, usage)
                record_decision(store, policy, done, usage)   # why nothing started (deduplicated)
                return done
        except OSError:
            return []          # another process is stepping
    finally:
        _LOCK.release()


def step_safe(store: Store, profile_home: Path, **kw) -> list[dict]:
    """Supervisor entry point: an autopilot failure must never stop local monitoring."""
    try:
        return step(store, profile_home, **kw)
    except Exception as exc:  # noqa: BLE001
        log.exception("autopilot step failed")
        try:
            store.record_event("autopilot_error", f"autopilot step failed: {type(exc).__name__}: {str(exc)[:200]}")
        except Exception:  # noqa: BLE001
            pass
        return []


def _unconfirmed(store, profile_home, agent_id, task_id, find, actions) -> None:
    """A start with no answer may still be running. Adopt that session so nothing runs untracked; quarantine
    (never retry) only when the Core has no live session for this worker."""
    try:
        sid = find(profile_home, agent_id) if find else ""
    except Exception:  # noqa: BLE001
        log.exception("autopilot could not look up the unconfirmed worker %s", agent_id)
        sid = ""
    if sid:
        store.link_session(agent_id, sid, provider="claude")
        actions.append({"task_id": task_id, "agent": agent_id, "action": "started", "session": sid, "adopted": True})
        return
    store.quarantine(task_id, "Autopilot start unconfirmed; check the original session before retrying")
    actions.append({"task_id": task_id, "action": "start_unconfirmed"})


def _step(store, profile_home, policy, start, stop, notify, force, find=None, usage=None) -> list[dict]:
    actions: list[dict] = []
    state = _load(store)
    now = store.clock()
    snap = store.snapshot()
    sig = (snap["revision"], hashlib.sha256(json.dumps(policy, sort_keys=True, default=str).encode()).hexdigest(),
           now >= state.get("paused_until", 0), int(now // 30))
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
                _unconfirmed(store, profile_home, agent["id"], tid, find, actions)

    # 2. Finished work: the worker moved its task away (review / waiting_owner / done / blocked). Close the
    #    session the autopilot started, only ever its own.
    for agent in owned:
        if agent.get("session_id") and not agent.get("task_id"):
            result = stop(profile_home, agent["session_id"])
            if stop_confirmed(result):
                store.link_session(agent["id"], "")
                actions.append({"agent": agent["id"], "action": "stopped"})
            else:
                store.record_event("autopilot_stop", f"{agent['id']}: stop not confirmed; will retry")
                actions.append({"agent": agent["id"], "action": "stop_unconfirmed"})

    # 2b. Watchdog (stall/offline/permission/idle), steward + review tasks, manual-test owner items. All of it
    #     only touches the ledger; launching stays in stage 3, so a stuck lane never blocks the others.
    actions += _watch(store, profile_home, policy, stop, notify)

    # 3. Start the next task per lane.
    if policy.get("kill"):
        _LAST_SIG[key] = sig
        return actions
    snap = store.snapshot()
    state = _load(store)
    now = store.clock()
    starts = [t for t in state.get("starts", []) if now - t < 3600]
    running = [a for a in snap["agents"] if a.get("autopilot") and a.get("task_id")]
    throttle = _throttle(store, policy, usage, notify, profile_home)
    for lane, conf in policy.get("lanes", {}).items():
        if not isinstance(conf, dict) or "workers" not in conf:
            continue
        if now < state.get("paused_until", 0) or throttle == "stop":
            break
        skipped: set[str] = set()
        while (sum(1 for a in running if a["lane"] == lane) < int(conf["workers"])
               and len(running) < int(policy.get("max_concurrent", 1))
               and (throttle != "slow" or not running)        # 80%+: one task at a time (#776)
               and len(starts) < int(policy.get("max_starts_per_hour", 1))):
            task = _pick(snap, policy, lane, skipped)
            if not task:
                break
            agent_id = AGENT_PREFIX + _slug(f"{lane}-{task['id']}")
            old = next((a for a in snap["agents"] if a["id"] == agent_id), None)
            if old and old.get("session_id"):
                break                 # its previous session is not confirmed stopped: never wipe the link or run two workers
            store.register_agent({"id": agent_id, "label": f"Autopilot {lane}: {task['title'][:80]}", "lane": lane,
                                  "provider": "claude", "project": policy.get("project", ""),
                                  "requested_model": conf.get("model", ""), "auto_dispatch": False})
            store.link_session(agent_id, "", autopilot=True)
            if old and old.get("status") != "idle":
                store.reset_idle_agent(agent_id)   # leftover offline agent from a failed try must not block the lane forever
            claimed = store.claim(agent_id, task_id=task["id"])
            if not claimed:           # lost the race or no longer ready: record it and try the next candidate
                if (agent_id, task["id"]) not in _CLAIM_FAILED:      # once per task: an unclaimable task must not flood the ledger
                    _CLAIM_FAILED.add((agent_id, task["id"]))
                    store.record_event("autopilot_error", f"Autopilot could not claim {task['id']} for {agent_id}; trying the next candidate")
                snap = store.snapshot()
                skipped.add(task["id"])
                actions.append({"task_id": task["id"], "action": "claim_failed"})
                continue
            ctx = task_context(store, claimed)
            req = dict(provider="claude", project=policy.get("project", ""), model=conf.get("model", ""),
                       task=assignment(ctx, agent_id, store.path.parent), approved=False,
                       env={"JARVES_WORKFLOW_AGENT": agent_id})
            try:
                result = start(profile_home, **req)
            except Exception:  # noqa: BLE001
                result = None
            if result is None or result.get("ok"):
                # Only a start the bridge accepted (or an unknown outcome) uses the hourly budget (#763).
                starts.append(now)
                state["starts"] = starts
                _save(store, state)
            if result is None:
                _unconfirmed(store, profile_home, agent_id, task["id"], find, actions)
            elif not result.get("ok"):
                code = str(result.get("code", "error"))
                store.transition(task["id"], {"state": "queued", "checkpoint": f"autopilot: start refused {code}",
                                              "blocker": f"Autopilot start refused: {code}"})
                state["paused_until"] = now + PAUSE_S
                state["pause_reason"] = code
                _save(store, state)
                store.record_event("autopilot_idle", f"start refused ({code}); not counted against max_starts_per_hour")
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


def _throttle(store, policy, usage, notify, profile_home) -> str:
    """ok | slow | stop from the weekly usage (#776). Unknown usage never blocks; a failure here never stops the loop."""
    from . import usage as use
    from .autopilot_watch import ALERTS_KEY, _load, _save, raise_alert
    try:
        info = (usage or use.current)(store, policy)
        lvl = use.level(store, policy, info)
        if lvl == "stop":
            raise_alert(store, "usage_stop", f"Weekly usage {info['text']} is at the stop line: no new tasks until it drops or the owner "
                        "clears it (workflow_console.py autopilot usage-resume)", notify=lambda k, t: notify(profile_home, k, t))
        else:
            alerts = _load(store, ALERTS_KEY, [])
            if any(a["kind"] == "usage_stop" for a in alerts):
                _save(store, ALERTS_KEY, [a for a in alerts if a["kind"] != "usage_stop"])   # re-arm the alert
        return lvl
    except Exception:  # noqa: BLE001
        log.exception("usage throttle failed; treated as unknown")
        return "ok"


def _watch(store, profile_home, policy, stop, notify) -> list[dict]:
    from . import autopilot_watch as watch
    out: list[dict] = []

    def _notify(kind, text):
        notify(profile_home, kind, text)

    stopped_ids: list[str] = []

    def _stop(agent):
        """False = the stop was not confirmed (the watchdog then keeps the task with its worker)."""
        if not agent.get("session_id"):
            return True
        result = stop(profile_home, agent["session_id"])
        if not stop_confirmed(result):
            return False
        stopped_ids.append(agent["id"])           # unlinked below, once the watchdog has released the task
        return True

    try:
        for name in watch.watchdog_step(store, policy, start=lambda lane, task: None, stop=_stop, notify=_notify):
            out.append({"action": "watchdog", "detail": name})
        # Confirmed-stopped workers whose task went back to the queue: drop the dead link so it can be restarted.
        # Never touches an agent that still owns a task (link_session refuses) or whose stop was not confirmed.
        for agent in store.snapshot()["agents"]:
            if agent["id"] in stopped_ids and agent.get("session_id") and not agent.get("task_id"):
                store.link_session(agent["id"], "")
        source = store.snapshot().get("source", {})
        # Sync supplies each open PR changed files (read-only); an empty list means unknown = Tier R (Opus review task, never steward).
        for name in watch.steward_step(store, policy, source.get("open_prs", []),
                                       lambda pr: list(pr.get("files") or []), notify=_notify):
            out.append({"action": "review_task", "task_id": name})
        from .release_health import watch_release_health
        for kind in watch_release_health(store, source, notify=_notify):
            out.append({"action": "release_health", "detail": kind})
        for name in watch.manual_test_step(store):
            out.append({"action": "manual_test_item", "task_id": name})
        for name in watch.gate_questions_step(store):
            out.append({"action": "gate_question", "task_id": name})
        # ci_green comes from sync (main check-runs, read-only).
        if source.get("ci_green") and source.get("sha"):
            import datetime
            day = datetime.datetime.fromtimestamp(store.clock(), datetime.timezone.utc).strftime("%Y-%m-%d")
            for name in watch.beta_step(store, policy, source["sha"], True, day,
                                        contains_p01_fix=bool(source.get("contains_p01_fix"))):
                out.append({"action": "beta_verify_task", "task_id": name})
    except Exception as exc:  # noqa: BLE001
        log.exception("autopilot watch failed")
        store.record_event("autopilot_error", f"autopilot watch failed: {type(exc).__name__}: {str(exc)[:200]}")
    return out
