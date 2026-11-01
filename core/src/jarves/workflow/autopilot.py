"""Workflow autopilot, part 1: owner policy, version order, bug intake and the work list.

Deterministic ledger code only. It never starts a worker, merges, tags or edits settings; launching is a later
step that must run under an owner grant. A broken policy file fails closed (nothing is imported or authorised).
"""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

POLICY_NAME = "autopilot.json"
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
LABEL_VERSION = {"4.5.1": "V4.5.1", "5": "V5", "5.0": "V5", "6": "V6", "6.0": "V6", "7": "V7", "7.0": "V7"}
PRIORITY_LABEL = {"p0": 0, "showstopper": 0, "p1": 1, "p2": 2, "p3": 3}

DEFAULTS = {
    "enabled": False, "kill": False, "project": "jarves",
    "versions": ["V4.5.1", "V5", "V6", "V7"], "authorized_versions": ["V4.5.1"],
    "lanes": {"bugs": {"workers": 1, "model": "claude-sonnet-5-5"},
              "features": {"workers": 1, "model": "claude-sonnet-5-5"},
              "steward": {"on": True, "model": "claude-haiku-5-5"},
              "review": {"model": "claude-opus-5-5"}},
    "max_concurrent": 3, "max_starts_per_hour": 6,
    "bug_labels": ["bug", "P0", "P1", "P2", "P3", "showstopper"],
    "watchdog": {"idle_s": 300, "stale_s": 120, "stuck_s": 1800, "max_restarts_per_task": 1},
}


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer {low}..{high}")


def _strings(value, name, limit=20):
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(x, str) and 0 < len(x) <= 40 for x in value):
        raise ValueError(f"{name} must be a short list of short strings")


OPTIONAL_KEYS = ("worker_allowed_tools",)       # absent = the built-in narrow list (orchestrator.claude.WORKER_ALLOWED_TOOLS)
_TOOL_RULE = re.compile(r"^(Bash\([^()\n]{1,60}\)|Read|Grep|Glob)$")
_HARD_STOP = re.compile(r"(?i)git\s+(push|tag)|gh\s+release|rm|hotfix|powershell")
_BYPASS = ("dangerously", "bypass", "skip-permissions")


def _worker_tools(value) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 40 or not all(isinstance(x, str) and _TOOL_RULE.match(x) for x in value):
        raise ValueError("worker_allowed_tools must be a short list (1..40) of short rules like Bash(ls:*)")
    if any(_HARD_STOP.search(x) for x in value):
        raise ValueError("worker_allowed_tools: hard stops (push, tag, release, rm, hotfix, powershell) cannot be allowed")
    if len(set(value)) != len(value) or any(w in x.lower() for x in value for w in _BYPASS):
        raise ValueError("worker_allowed_tools: no duplicates and no bypass flags")


def validate(policy: dict) -> dict:
    """Return the policy if valid, else raise ValueError. Unknown keys are refused."""
    if not isinstance(policy, dict) or not set(DEFAULTS) <= set(policy) <= set(DEFAULTS) | set(OPTIONAL_KEYS):
        raise ValueError("policy keys must be exactly: " + ", ".join(DEFAULTS))
    for key in ("enabled", "kill"):
        if type(policy[key]) is not bool:
            raise ValueError(f"{key} must be true or false")
    if not isinstance(policy["project"], str) or not MODEL_RE.match(policy["project"]):
        raise ValueError("invalid project")
    _strings(policy["versions"], "versions")
    _strings(policy["authorized_versions"], "authorized_versions")
    if len(set(policy["versions"])) != len(policy["versions"]) or not set(policy["authorized_versions"]) <= set(policy["versions"]):
        raise ValueError("authorized_versions must be a subset of versions, without duplicates")
    _strings(policy["bug_labels"], "bug_labels")
    _int(policy["max_concurrent"], "max_concurrent", 0, 8)
    _int(policy["max_starts_per_hour"], "max_starts_per_hour", 0, 60)
    lanes = policy["lanes"]
    if not isinstance(lanes, dict) or set(lanes) != set(DEFAULTS["lanes"]):
        raise ValueError("lanes must be bugs, features, steward, review")
    for name, lane in lanes.items():
        if not isinstance(lane, dict) or set(lane) != set(DEFAULTS["lanes"][name]):
            raise ValueError(f"lane {name} has invalid keys")
        if not isinstance(lane["model"], str) or not MODEL_RE.match(lane["model"]):
            raise ValueError(f"lane {name}: invalid model")
        if "workers" in lane:
            _int(lane["workers"], f"lane {name} workers", 0, 5)
        if "on" in lane and type(lane["on"]) is not bool:
            raise ValueError(f"lane {name}: on must be true or false")
    if "worker_allowed_tools" in policy:
        _worker_tools(policy["worker_allowed_tools"])
    watchdog = policy["watchdog"]
    if not isinstance(watchdog, dict) or set(watchdog) != set(DEFAULTS["watchdog"]):
        raise ValueError("watchdog has invalid keys")
    for key, value in watchdog.items():
        _int(value, f"watchdog {key}", 0 if key == "max_restarts_per_task" else 1, 86400)
    return policy


def policy_path(home: Path) -> Path:
    return Path(home) / POLICY_NAME


def load(home: Path) -> tuple[dict, str]:
    """Re-read on every call. Missing file means defaults (OFF); a bad file means defaults plus an error (fail closed)."""
    path = policy_path(home)
    if not path.exists():
        return copy.deepcopy(DEFAULTS), ""
    try:
        return validate(json.loads(path.read_text(encoding="utf-8"))), ""
    except (OSError, ValueError) as exc:
        return {**copy.deepcopy(DEFAULTS), "enabled": False}, f"policy file invalid, autopilot stays off: {exc}"


def save(home: Path, policy: dict) -> dict:
    validate(policy)
    path = policy_path(home)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return policy


def set_key(home: Path, key: str, raw: str) -> dict:
    """`set lanes.bugs.workers 2`; the value is JSON when it parses, else a plain string."""
    policy, error = load(home)
    if error:
        raise ValueError(error)
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    parts = key.split(".")
    node = policy
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"unknown policy key: {key}")
        node = node[part]
    if not isinstance(node, dict) or (parts[-1] not in node and key not in OPTIONAL_KEYS):
        raise ValueError(f"unknown policy key: {key}")
    node[parts[-1]] = value
    return save(home, policy)


def version_rank(policy: dict, version: str) -> int:
    versions = policy["versions"]
    return versions.index(version) if version in versions else len(versions)


def issue_version(labels: list[str]) -> str:
    for label in labels:
        key = label.strip().lower().lstrip("v")
        if key in LABEL_VERSION:
            return LABEL_VERSION[key]
    return ""


def issue_priority(labels: list[str]) -> int:
    ranks = [PRIORITY_LABEL[x.lower()] for x in labels if x.lower() in PRIORITY_LABEL]
    return min(ranks) if ranks else 2


def is_bug(labels: list[str], bug_labels: list[str]) -> bool:
    wanted = {x.lower() for x in bug_labels}
    return any(x.lower() in wanted for x in labels)


def next_task(snapshot: dict, policy: dict, lane: str) -> str | None:
    """Next ready task id for a lane. Bugs go by priority; other lanes stay inside the lowest version with open required work."""
    tasks = [t for t in snapshot["tasks"] if t["lane"] == lane]
    if lane == "bugs":
        ready = [t for t in tasks if t.get("ready")]
        ready.sort(key=lambda t: (t["priority"], version_rank(policy, t.get("version", "")), t["updated_at"], t["id"]))
        return ready[0]["id"] if ready else None
    open_required = [t for t in tasks if t.get("scope", "required") == "required" and t["state"] not in {"verified", "done"}]
    if not open_required:
        return None
    lowest = min(version_rank(policy, t.get("version", "")) for t in open_required)
    ready = [t for t in open_required if t.get("ready") and version_rank(policy, t.get("version", "")) == lowest]
    ready.sort(key=lambda t: (t["priority"], t["updated_at"], t["id"]))
    return ready[0]["id"] if ready else None


def step(store, home: Path) -> dict:
    """Import filed defects and version-labelled issues as tasks; authorise only owner-authorised versions."""
    policy, error = load(home)
    if error or policy["kill"]:
        return {"skipped": error or "kill switch is on"}
    snapshot = store.snapshot()
    known = {t["id"] for t in snapshot["tasks"]}
    mapped = {n for t in snapshot["tasks"] for n in t.get("issue_numbers", [])}
    created = []
    for issue in snapshot["source"].get("open_issues", []):
        number, labels = issue["number"], issue.get("labels", [])
        ident = f"gh-{number}"
        if ident in known or number in mapped:
            continue
        bug = is_bug(labels, policy["bug_labels"])
        version = issue_version(labels)
        if not bug and not version:
            continue
        version = version or policy["versions"][0]
        task = store.create_task({
            "id": ident, "title": f"#{number} {issue.get('title', '')}"[:300], "version": version,
            "lane": "bugs" if bug else "features", "priority": issue_priority(labels) if bug else 2,
            "required_validation": "app", "scope": "required", "issue_numbers": [number],
            "source_url": issue.get("url", ""),
            "acceptance": [{"id": "C1", "label": f"Fixed or delivered, with evidence (PR, test or run) linked to issue #{number}"}],
            "execution_allowed": False})
        created.append(task["id"])
    authorised = []
    if policy["enabled"]:
        for task in store.snapshot()["tasks"]:
            if (not task.get("execution_allowed", True) and task["state"] == "queued" and not task.get("owner_gate")
                    and task.get("scope", "required") == "required" and task.get("version") in policy["authorized_versions"]):
                store.update_metadata(task["id"], {"execution_allowed": True}, authorize_execution=True)
                authorised.append(task["id"])
    return {"created": created, "authorised": authorised}


def view(snapshot: dict, home: Path) -> dict:
    """What the console shows: policy plus one list of open bugs and PRs with their linked tasks."""
    policy, error = load(home)
    source = snapshot.get("source", {})
    by_issue = {}
    for task in snapshot["tasks"]:
        for number in task.get("issue_numbers", []):
            by_issue.setdefault(number, task)
    work = []
    for issue in source.get("open_issues", []):
        task = by_issue.get(issue["number"])
        work.append({"kind": "issue", "number": issue["number"], "title": issue.get("title", ""), "url": issue.get("url", ""),
                     "labels": issue.get("labels", []), "priority": issue_priority(issue.get("labels", [])),
                     "task_id": task["id"] if task else "", "task_state": task["state"] if task else "", "ci": ""})
    for pr in source.get("open_prs", []):
        linked = sorted({by_issue[n]["id"] for n in pr.get("linked_issues", []) if n in by_issue})
        work.append({"kind": "pr", "number": pr["number"], "title": pr.get("title", ""), "url": pr.get("url", ""),
                     "labels": pr.get("labels", []), "priority": 9, "task_id": ", ".join(linked), "task_state": "",
                     "ci": "unknown"})
    work.sort(key=lambda w: (w["kind"] != "issue", w["priority"], w["number"]))
    return {"policy": policy, "error": error, "work": work, "synced_at": source.get("synced_at", 0),
            "sync_error": source.get("error", "")}


def command(store, home: Path, op: str, args: list[str]) -> dict:
    """CLI: status | set KEY VALUE | stop | start | once. Every change is a ledger event."""
    if op == "status":
        return view(store.snapshot(), home)
    if op == "set":
        if len(args) != 2:
            raise ValueError("usage: autopilot set KEY VALUE")
        policy = set_key(home, args[0], args[1])
        store.record_event("autopilot_policy", f"Policy changed: {args[0]}")
        return {"policy": policy}
    if op in {"stop", "start"}:
        policy = set_key(home, "kill", "true" if op == "stop" else "false")
        store.record_event("autopilot_policy", "Kill switch " + ("ON: no new work is imported or authorised" if op == "stop" else "OFF"))
        return {"policy": policy}
    if op == "once":
        return step(store, home)
    raise ValueError("usage: autopilot status|set|stop|start|once")
