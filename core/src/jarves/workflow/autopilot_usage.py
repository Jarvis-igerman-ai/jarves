"""Autopilot usage-aware throttle (#776). Deterministic: below slow_pct run normally; from slow_pct start one task
at a time and only when nothing else runs; from stop_pct start nothing (running workers finish), alert the owner once,
and resume when the owner runs `autopilot resume` or the week resets (usage falls below stop_pct).

Usage source: `<home>/usage.json` = {"pct": 83.5, "at": epoch, "source": "...", "estimated": true}. Anything may write
it (a status-line hook, a token-count estimator). Whether Claude Code exposes the weekly percentage is UNVERIFIED, so
no collector ships here; a missing, stale (over 6 h) or malformed file means "unknown" and the loop runs normally.
"""
from __future__ import annotations

import json
from pathlib import Path

USAGE_NAME = "usage.json"
STALE_S = 6 * 3600
SLOW_PCT, STOP_PCT = 80, 95


def thresholds(policy: dict) -> tuple[float, float]:
    return float(policy.get("usage_slow_pct", SLOW_PCT)), float(policy.get("usage_stop_pct", STOP_PCT))


def read_usage(home: Path, now: float) -> dict | None:
    """The fresh usage record, or None when unknown."""
    try:
        data = json.loads((Path(home) / USAGE_NAME).read_text(encoding="utf-8"))
        pct = data["pct"]
        if type(pct) not in (int, float) or not 0 <= pct <= 1000:
            return None
        if now - float(data.get("at", 0)) > STALE_S:
            return None
        return {"pct": float(pct), "source": str(data.get("source", ""))[:80], "estimated": bool(data.get("estimated", True))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def mode(policy: dict, usage: dict | None, resumed: bool = False) -> str:
    """normal | slow | stop."""
    if usage is None:
        return "normal"
    slow, stop = thresholds(policy)
    if usage["pct"] >= stop:
        return "slow" if resumed else "stop"
    return "slow" if usage["pct"] >= slow else "normal"


def view(policy: dict, home: Path, now: float, resumed: bool = False) -> dict:
    usage = read_usage(home, now)
    slow, stop = thresholds(policy)
    return {"pct": usage["pct"] if usage else None, "estimated": usage["estimated"] if usage else None,
            "source": usage["source"] if usage else "", "mode": mode(policy, usage, resumed),
            "slow_pct": slow, "stop_pct": stop}
