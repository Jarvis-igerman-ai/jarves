"""Autopilot usage throttle: read the weekly Claude usage and decide ok / slow / stop (issue #776).

Source 1 (real): the Claude Code statusLine stdin JSON carries ``rate_limits.seven_day.used_percentage``. The status
line script (C:\\work\\claude-j-status.py) writes the newest snapshot to ``%LOCALAPPDATA%\\jarves\\workflow\\usage.json``;
this module only reads it. Source 2 (ESTIMATE, always labelled): tokens of autopilot worker sessions recorded in the
ledger over 7 days against the policy ``weekly_token_budget``; with no budget the estimate is off. No source =
"unknown", and unknown never blocks. Deterministic, stdlib only, never raises into the autopilot loop.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SLOW_PCT, STOP_PCT = 80, 95
MAX_AGE_S = 12 * 3600          # an older snapshot is treated as unknown (the status line only runs while a session is open)
WEEK_S = 7 * 86400
TOKEN_KEY = "autopilot_token_log"   # meta row: [[ts, tokens], ...]
OVERRIDE_KEY = "autopilot_usage_override"


def snapshot_path() -> Path:
    env = os.environ.get("JARVES_USAGE_FILE")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "jarves" / "workflow" / "usage.json"


def extract(stdin_json: dict) -> dict | None:
    """Pull the usage snapshot out of the statusLine stdin JSON (None when the fields are absent)."""
    limits = stdin_json.get("rate_limits") if isinstance(stdin_json, dict) else None
    if not isinstance(limits, dict):
        return None
    out = {}
    for name, key in (("weekly_pct", "seven_day"), ("five_hour_pct", "five_hour")):
        item = limits.get(key)
        pct = item.get("used_percentage") if isinstance(item, dict) else None
        if isinstance(pct, (int, float)) and not isinstance(pct, bool):
            out[name] = float(pct)
    return out if "weekly_pct" in out else None


def read_snapshot(path: Path | None = None, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    try:
        data = json.loads(Path(path or snapshot_path()).read_text(encoding="utf-8"))
        pct, ts = float(data["weekly_pct"]), float(data["ts"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not 0 <= pct <= 1000 or now - ts > MAX_AGE_S:
        return None
    return {"pct": pct, "ts": ts, "five_hour_pct": data.get("five_hour_pct")}


def _meta(store, key, default):
    with store._tx() as db:
        row = db.execute("SELECT value FROM workflow_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _put(store, key, value) -> None:
    with store._tx() as db:
        store._set_meta(db, key, value)


def record_tokens(store, tokens: int) -> None:
    """Producer hook for the estimate: add the tokens of a finished autopilot worker session."""
    log = _meta(store, TOKEN_KEY, [])
    log.append([store.clock(), int(tokens)])
    _put(store, TOKEN_KEY, log[-2000:])


def _estimate(store, policy: dict) -> dict | None:
    budget = policy.get("weekly_token_budget")
    if not isinstance(budget, int) or budget <= 0:
        return None
    now = store.clock()
    total = sum(t for ts, t in _meta(store, TOKEN_KEY, []) if now - ts <= WEEK_S)
    return {"pct": round(100.0 * total / budget, 1), "tokens": total, "budget": budget}


def current(store, policy: dict, snapshot_file: Path | None = None) -> dict:
    """{pct|None, source: statusline|estimate|unknown, estimate: bool, text}. Never raises."""
    try:
        snap = read_snapshot(snapshot_file, store.clock())
        if snap:
            return {"pct": snap["pct"], "source": "statusline", "estimate": False, "text": f"{snap['pct']:.0f}% weekly"}
        est = _estimate(store, policy)
        if est:
            return {"pct": est["pct"], "source": "estimate", "estimate": True,
                    "text": f"{est['pct']:.0f}% weekly (ESTIMATE from {est['tokens']} tokens / budget {est['budget']})"}
    except Exception:  # noqa: BLE001
        pass
    return {"pct": None, "source": "unknown", "estimate": False, "text": "unknown", "notice": NOTICE}


# There is deliberately NO default weekly_token_budget: the Max 5x token allowance is not published as a number we can trust.
NOTICE = ("usage throttle DISABLED: Claude Code gave no weekly percentage (usage.json missing or stale) and no "
          "weekly_token_budget is set in autopilot.json (Max 5x budget unknown; any value the owner sets is an assumption)")


def thresholds(policy: dict) -> tuple[float, float]:
    return float(policy.get("usage_slow_pct", SLOW_PCT)), float(policy.get("usage_stop_pct", STOP_PCT))


def override_active(store) -> bool:
    return bool(_meta(store, OVERRIDE_KEY, {}).get("on"))


def set_override(store, on: bool) -> None:
    _put(store, OVERRIDE_KEY, {"on": on, "at": store.clock()})


def level(store, policy: dict, usage: dict) -> str:
    """ok | slow | stop. Unknown usage never blocks. An owner clear (usage-resume) turns stop into slow until usage < slow."""
    pct = usage.get("pct")
    slow, stop = thresholds(policy)
    if pct is None or pct < slow:
        if override_active(store):
            set_override(store, False)      # dropped below the slow line: the clear has done its job
        return "ok"
    if pct >= stop and not override_active(store):
        return "stop"
    return "slow"
