"""Dynamic prioritisation ("focus") for the factory manager and the autopilot.

Simple, deterministic, explainable, no LLM. Inputs are counts (open PRs by state, open issues by label, main CI,
queued tasks, owner questions); the output is one focus word, the reasons, a lane plan (workers per lane) and one
line of advice. Read-only: it never merges, starts or stops anything; the autopilot loop only takes worker counts
from the plan, the manager reads ``workflow_console.py focus``.

Focus words (highest first):
  main-fix  main CI is red: only the fix merges, everything else pauses
  fix       showstopper/P0 issues >= focus_showstopper_high: the bugs lane borrows workers from features
  merge     open PRs >= focus_pr_high (or green-waiting >= focus_green_high): steward + review first, features drop to 1
  balanced  the static lane workers from autopilot.json

A change of focus needs the condition to hold for 2 evaluations in a row (no flapping) and records ONE ledger
event ``focus_changed``. The steward is never 0. Worker counts never exceed ``max_concurrent`` and the usage
throttle (stop = 0, slow = 1) always wins. GitHub is read through ``gh`` (read-only, fail soft) and cached 5 minutes.
"""
from __future__ import annotations

import json
import os
import subprocess

FOCUS_DEFAULTS = {"focus_enabled": True, "focus_pr_high": 6, "focus_green_high": 3, "focus_showstopper_high": 2}
FOCUS_KEYS = tuple(FOCUS_DEFAULTS)
CACHE_KEY, STATE_KEY = "focus_gh_cache", "focus_state"
CACHE_TTL_S = 300
CONFIRM = 2                      # evaluations in a row before a focus change
GH_TIMEOUT_S = 20
FOCUSES = ("main-fix", "fix", "merge", "balanced")
HE_NAME = {"main-fix": "תיקון main", "fix": "תיקון", "merge": "מיזוג", "balanced": "מאוזן"}
SENTENCE = "Run workflow_console.py focus each cycle and follow it."


# ---- policy ---------------------------------------------------------------------------------------------------
def validate_policy(policy: dict) -> None:
    if "focus_enabled" in policy and type(policy["focus_enabled"]) is not bool:
        raise ValueError("focus_enabled must be true or false")
    for key, high in (("focus_pr_high", 200), ("focus_green_high", 100), ("focus_showstopper_high", 100)):
        if key in policy and (type(policy[key]) is not int or not 1 <= policy[key] <= high):
            raise ValueError(f"{key} must be an integer 1..{high}")


def setting(policy: dict, key: str):
    return policy.get(key, FOCUS_DEFAULTS[key])


# ---- inputs ---------------------------------------------------------------------------------------------------
def _gh_json(args: list[str]):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_S, check=True, shell=False)
    return json.loads(out.stdout or "[]")


def default_gh(kind: str):
    """Read-only list through the gh CLI. Raises on any failure (the caller fails soft)."""
    if os.environ.get("JARVES_FOCUS_GH", "").lower() == "off" or os.environ.get("JARVES_TEST_OFFLINE"):
        raise RuntimeError("gh disabled")
    from .sync import REPO
    if kind == "prs":
        return _gh_json(["pr", "list", "--repo", REPO, "--state", "open", "--limit", "100", "--json",
                         "number,isDraft,labels,mergeable,statusCheckRollup"])
    return _gh_json(["issue", "list", "--repo", REPO, "--state", "open", "--limit", "200", "--json", "number,labels"])


def _labels(item: dict) -> set[str]:
    return {(x.get("name", "") if isinstance(x, dict) else str(x)).lower() for x in item.get("labels") or []}


def _check_state(pr: dict) -> str:
    """green | red | pending | unknown from statusCheckRollup (or a plain ci field from the sync snapshot)."""
    rows = pr.get("statusCheckRollup")
    if not isinstance(rows, list):
        return str(pr.get("ci") or "unknown")
    if not rows:
        return "unknown"
    verdicts = set()
    for row in rows:
        concl = str(row.get("conclusion") or row.get("state") or "").upper()
        if concl in {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED"}:
            verdicts.add("red")
        elif concl in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            verdicts.add("green")
        else:
            verdicts.add("pending")
    return "red" if "red" in verdicts else "pending" if "pending" in verdicts else "green"


def classify_prs(prs: list[dict]) -> dict:
    out = {"total": len(prs), "green": 0, "red": 0, "conflicted": 0, "tier_r_waiting": 0}
    for pr in prs:
        labels = _labels(pr)
        if pr.get("isDraft") or pr.get("draft"):
            continue
        if str(pr.get("mergeable") or "").upper() == "CONFLICTING":
            out["conflicted"] += 1
        elif _check_state(pr) == "red":
            out["red"] += 1
        elif "no-automerge" in labels:
            out["tier_r_waiting"] += 1
        elif _check_state(pr) == "green":
            out["green"] += 1
    return out


def classify_issues(issues: list[dict]) -> dict:
    out = {"showstopper": 0, "p0": 0, "p1": 0, "p2": 0, "main_fix": 0, "pressure": 0}
    for issue in issues:
        labels = _labels(issue)
        for key, label in (("showstopper", "showstopper"), ("p0", "p0"), ("p1", "p1"), ("p2", "p2"), ("main_fix", "main-fix")):
            out[key] += label in labels
        out["pressure"] += bool(labels & {"showstopper", "p0"})
    return out


def _meta(store, key, default):
    with store._tx() as db:
        row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _put(store, key, value) -> None:
    with store._tx() as db:
        store._set_meta(db, key, value)


def _github(store, gh, fetch: bool) -> tuple[list | None, list | None, str]:
    """(prs, issues, source). Cached 5 min in the ledger meta; a failed read keeps the last good data."""
    now = store.clock()
    cache = _meta(store, CACHE_KEY, {})
    fresh = bool(cache) and now - cache.get("ts", 0) < CACHE_TTL_S
    if fresh or not fetch:
        return (cache.get("prs"), cache.get("issues"), "cache") if cache.get("prs") is not None else (None, None, "none")
    try:
        prs, issues = (gh or default_gh)("prs"), (gh or default_gh)("issues")
        if not isinstance(prs, list) or not isinstance(issues, list):
            raise ValueError("unexpected gh output")
        _put(store, CACHE_KEY, {"ts": now, "prs": prs, "issues": issues})
        return prs, issues, "gh"
    except Exception:  # noqa: BLE001  fail soft: never raise into the loop
        _put(store, CACHE_KEY, {**cache, "ts": now})        # retry in 5 min, keep the last good lists
        return (cache.get("prs"), cache.get("issues"), "stale-cache") if cache.get("prs") is not None else (None, None, "none")


def gather_inputs(store, gh=None, *, fetch: bool = True, usage_level: str = "ok") -> dict:
    """Counts only. ``gh(kind)`` returns the JSON list for 'prs' or 'issues' (fake in tests)."""
    snap = store.snapshot()
    source = snap.get("source", {})
    prs, issues, origin = _github(store, gh, fetch)
    if prs is None:                                           # no gh data at all: fall back to the sync snapshot
        prs, issues, origin = source.get("open_prs", []), source.get("open_issues", []), "snapshot"
    from .release_health import release_health
    try:
        main_red = any(f["kind"] == "main_not_green" for f in release_health(source, store.clock()))
    except Exception:  # noqa: BLE001
        main_red = False
    queued = {"bugs": 0, "features": 0}
    blocking = {q.get("task_id") for q in snap.get("questions", [])
                if q.get("status") in {"pending", "deferred", "open"} and q.get("blocks", True) and q.get("task_id")}
    lane_of = {t["id"]: t["lane"] for t in snap["tasks"]}
    blocked_lanes = sorted({lane_of[i] for i in blocking if lane_of.get(i) in queued})
    for task in snap["tasks"]:
        if task["lane"] in queued and task.get("ready"):
            queued[task["lane"]] += 1
    waiting = sum(1 for q in snap.get("questions", []) if q.get("status") in {"pending", "deferred", "open"})
    return {"prs": classify_prs(prs), "issues": classify_issues(issues), "main_red": main_red, "queued": queued,
            "questions_waiting": waiting, "blocked_lanes": blocked_lanes, "usage_level": usage_level, "source": origin}


# ---- decision -------------------------------------------------------------------------------------------------
def raw_focus(inputs: dict, policy: dict) -> tuple[str, list[str]]:
    prs, issues = inputs["prs"], inputs["issues"]
    pr_high, green_high = setting(policy, "focus_pr_high"), setting(policy, "focus_green_high")
    ss_high = setting(policy, "focus_showstopper_high")
    reasons = []
    pressure_fix = issues["pressure"] >= ss_high
    pressure_merge = prs["total"] >= pr_high or prs["green"] >= green_high
    if inputs.get("main_red"):
        reasons.append("main CI is red: only the fix merges, everything else pauses")
    if pressure_fix:
        reasons.append(f"{issues['pressure']} showstopper/P0 issues open (limit {ss_high})")
    if pressure_merge:
        reasons.append(f"{prs['total']} open PRs, {prs['green']} green and waiting (limits {pr_high}/{green_high})")
    focus = "main-fix" if inputs.get("main_red") else "fix" if pressure_fix else "merge" if pressure_merge else "balanced"
    if focus == "balanced":
        reasons.append("no pressure: static lane plan")
    return focus, reasons


def _plan(focus: str, inputs: dict, policy: dict) -> dict:
    lanes = policy.get("lanes", {})
    b0, f0 = int(lanes.get("bugs", {}).get("workers", 1)), int(lanes.get("features", {}).get("workers", 1))
    cap = int(policy.get("max_concurrent", 1))
    tier_r = inputs["prs"]["tier_r_waiting"]
    blocked = set(inputs.get("blocked_lanes", []))
    steward = 1                                              # never 0
    bugs, features, review = b0, f0, min(tier_r, 1)
    if focus == "main-fix":
        bugs, features, review = min(max(b0, 1), cap), 0, 0
    elif focus == "fix":
        want = max(b0, min(5, inputs["queued"]["bugs"]))
        bugs = b0 if "bugs" in blocked else max(b0, min(want, cap - steward))   # borrow, steward keeps 1
        features = min(f0, max(0, cap - steward - bugs))
        review = min(tier_r, 1)
    elif focus == "merge":
        features = min(f0, 1)
        review = min(tier_r, 2)
    # bounded by max_concurrent: features give way first, then bugs
    over = bugs + features - cap
    if over > 0:
        cut = min(features, over)
        features, over = features - cut, over - cut
        bugs -= max(0, over)
    level = inputs.get("usage_level", "ok")
    if level == "stop":
        bugs = features = review = 0                         # the usage stop wins: no new autopilot work
    elif level == "slow" and bugs + features > 1:
        bugs, features = (1, 0) if bugs else (0, 1)
    return {"bugs": bugs, "features": features, "steward": steward, "review": review}


def _advice(focus: str, inputs: dict) -> str:
    prs = inputs["prs"]
    if inputs.get("usage_level") == "stop":
        return "Weekly usage is at the stop line: no new workers; the manager only merges green PRs."
    return {
        "main-fix": "Merge only the main fix; hold every other PR and pause feature work.",
        "fix": "Put the extra workers on showstopper/P0 bugs; the steward keeps merging green PRs.",
        "merge": (f"Merge first: {prs['green']} green PR(s) waiting, {prs['tier_r_waiting']} Tier R review(s), "
                  f"{prs['red']} red and {prs['conflicted']} conflicted PR(s) need a fixer; plan integration for 3+ independent green PRs."),
        "balanced": "Normal order: bugs first, then features, steward merges green PRs.",
    }[focus]


def compute_focus(inputs: dict, policy: dict, state: dict | None = None, advance: bool = True) -> dict:
    """Pure. ``state`` ({current, candidate, streak}) switches on hysteresis; None adopts the raw focus at once."""
    raw, raw_reasons = raw_focus(inputs, policy)
    new = dict(state or {})
    current = new.get("current") or "balanced"
    pending = None
    changed = False
    if state is None:
        focus = raw
    elif not advance:                      # read-only view: show the held focus and how close a change is
        focus = current
        if raw != current:
            seen = new.get("streak", 0) if new.get("candidate") == raw else 0
            pending = {"focus": raw, "seen": seen, "needed": CONFIRM}
    else:
        if raw == current:
            new["candidate"], new["streak"] = None, 0
        else:
            new["streak"] = new.get("streak", 0) + 1 if new.get("candidate") == raw else 1
            new["candidate"] = raw
            if new["streak"] >= CONFIRM:
                current, changed = raw, True
                new["candidate"], new["streak"] = None, 0
            else:
                pending = {"focus": raw, "seen": new["streak"], "needed": CONFIRM}
        new["current"] = current
        focus = current
    reasons = raw_reasons if focus == raw else [f"holding {focus} until {raw} is seen {CONFIRM} times in a row"] + raw_reasons
    if inputs.get("usage_level") in {"slow", "stop"}:
        reasons.append(f"usage throttle {inputs['usage_level']} limits workers")
    if inputs.get("blocked_lanes"):
        reasons.append("owner question blocks lane: " + ", ".join(inputs["blocked_lanes"]) + " (no borrowed workers)")
    if inputs.get("source") in {"snapshot", "none"}:
        reasons.append("GitHub data is the last sync snapshot (gh unavailable)")
    return {"focus": focus, "raw": raw, "reasons": reasons, "lanes": _plan(focus, inputs, policy),
            "advice": _advice(focus, inputs), "pending": pending, "changed": changed, "state": new,
            "inputs": inputs}


def evaluate(store, policy: dict, gh=None, *, commit: bool = True, fetch: bool = True, usage_level: str = "ok") -> dict:
    """Gather + compute. ``commit`` advances the hysteresis state and writes the one ``focus_changed`` event."""
    inputs = gather_inputs(store, gh, fetch=fetch, usage_level=usage_level)
    state = _meta(store, STATE_KEY, {"current": "balanced", "candidate": None, "streak": 0})
    result = compute_focus(inputs, policy, state, advance=commit)
    if commit:
        _put(store, STATE_KEY, result["state"])
        if result["changed"]:
            store.record_event("focus_changed", f"Focus {state.get('current', 'balanced')} -> {result['focus']}: "
                               + "; ".join(r for r in result["reasons"] if not r.startswith("holding"))[:800])
    result["enabled"] = bool(setting(policy, "focus_enabled"))
    return result


def line_he(result: dict) -> str:
    """One short Hebrew line for the Overview, e.g. 'מיקוד: מיזוג — 9 PR פתוחים'."""
    focus, inputs = result["focus"], result["inputs"]
    detail = {"main-fix": "main אדום", "fix": f"{inputs['issues']['pressure']} באגים קריטיים",
              "merge": f"{inputs['prs']['total']} PR פתוחים", "balanced": "אין עומס"}[focus]
    return f"מיקוד: {HE_NAME[focus]} — {detail}"


def line_en(result: dict) -> str:
    focus, inputs = result["focus"], result["inputs"]
    detail = {"main-fix": "main is red", "fix": f"{inputs['issues']['pressure']} critical bugs",
              "merge": f"{inputs['prs']['total']} open PRs", "balanced": "no pressure"}[focus]
    return f"Focus: {focus} - {detail}"


def summary(result: dict) -> dict:
    """The small dict shown in status and /api/health."""
    return {"focus": result["focus"], "reasons": result["reasons"], "lanes": result["lanes"], "advice": result["advice"],
            "pending": result["pending"], "enabled": result["enabled"], "he": line_he(result), "en": line_en(result),
            "prs": result["inputs"]["prs"], "issues": result["inputs"]["issues"], "source": result["inputs"]["source"]}


def current(store, policy: dict, usage_level: str = "ok") -> dict:
    """Read-only, cache-only view for health/status: never runs gh, never advances the hysteresis."""
    try:
        return summary(evaluate(store, policy, commit=False, fetch=False, usage_level=usage_level))
    except Exception as exc:  # noqa: BLE001
        return {"focus": "balanced", "reasons": [f"focus unavailable: {type(exc).__name__}"], "lanes": {}, "advice": "",
                "pending": None, "enabled": False, "he": "מיקוד: לא זמין", "en": "Focus: unavailable"}


def format_text(result: dict) -> str:
    lanes = result["lanes"]
    lines = [line_en(result) + ("" if result["enabled"] else "  (focus_enabled is off: the static policy is used)"),
             "Reasons: " + "; ".join(result["reasons"]),
             "Lanes: " + ", ".join(f"{k}={v}" for k, v in lanes.items()),
             "Advice: " + result["advice"]]
    if result.get("pending"):
        lines.append(f"Pending: {result['pending']['focus']} seen {result['pending']['seen']}/{result['pending']['needed']}")
    return "\n".join(lines)
