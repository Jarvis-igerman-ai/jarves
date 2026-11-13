"""Merge steward policy (#777): choose integration PR or one-by-one from recorded outcomes.

Pure functions, no network. A cycle record is a dict:
  mode ("integration"|"individual"), eligible, skipped, conflicts, ci_minutes,
  minutes_to_merge (green -> merged, per PR average), reverts.
Cycles are stored as workflow ledger events of kind "merge_cycle" (JSON summary).
Tier R / no-automerge PRs are never counted as eligible; that rule is unchanged.
"""
from __future__ import annotations

import json

BASE_THRESHOLD = 3
# OPERATING_MODEL: individual when fewer than 3 eligible PRs, so the floor stays at 3.
MIN_THRESHOLD, MAX_THRESHOLD = 3, 6
WINDOW = 10            # most recent cycles per mode
MIN_SAMPLES = 2        # cycles of a mode needed before history can move the threshold
SKIP_RATE_LIMIT = 0.30
SERIAL_WAIT_LIMIT = 40  # minutes
RARE_CONFLICT = 0.10
EVENT_KIND = "merge_cycle"


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _avg(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else None


def stats(history: list) -> dict:
    out = {}
    for mode in ("integration", "individual"):
        rows = [c for c in history if isinstance(c, dict) and c.get("mode") == mode][-WINDOW:]  # history is oldest first
        elig = sum(max(_int(c.get("eligible")), 0) for c in rows)
        bad = sum(_int(c.get("skipped")) + _int(c.get("conflicts")) for c in rows)
        out[mode] = {
            "cycles": len(rows),
            "conflict_rate": (bad / elig) if elig else 0.0,
            "avg_minutes_to_merge": _avg([c.get("minutes_to_merge") for c in rows]),
            "avg_ci_minutes": _avg([c.get("ci_minutes") for c in rows]),
            "reverts": sum(_int(c.get("reverts")) for c in rows),
        }
    return out


def threshold(history: list) -> tuple[int, str]:
    """Adjusted eligible-PR threshold for choosing integration, with the reason."""
    s = stats(history)
    i, o = s["integration"], s["individual"]
    t, why = BASE_THRESHOLD, "default"
    if i["cycles"] >= MIN_SAMPLES:
        slower = (o["cycles"] >= MIN_SAMPLES
                  and i["avg_minutes_to_merge"] is not None
                  and o["avg_minutes_to_merge"] is not None
                  and i["avg_minutes_to_merge"] > o["avg_minutes_to_merge"])
        if i["conflict_rate"] > SKIP_RATE_LIMIT or i["reverts"] or slower:
            return min(t + 1, MAX_THRESHOLD), "integration skip/revert/time worse than serial: raised"
    if (o["cycles"] >= MIN_SAMPLES and (o["avg_minutes_to_merge"] or 0) > SERIAL_WAIT_LIMIT
            and o["conflict_rate"] <= RARE_CONFLICT and not i["reverts"]):
        return max(t - 1, MIN_THRESHOLD), "serial wait long, conflicts rare: lowered"
    return t, why


def decide(eligible: int, history: list, *, overlap: bool = False, lone_tier_r: bool = False) -> dict:
    """Return {"mode", "threshold", "reason", "stats"}. eligible counts non-Tier-R green PRs."""
    t, why = threshold(history)
    if lone_tier_r:
        mode, reason = "individual", "lone Tier R: never merged by the steward"
    elif overlap:
        mode, reason = "individual", "file overlap between PRs"
    elif eligible >= t:
        mode, reason = "integration", f"{eligible} eligible >= threshold {t} ({why})"
    else:
        mode, reason = "individual", f"{eligible} eligible < threshold {t} ({why})"
    return {"mode": mode, "threshold": t, "reason": reason, "stats": stats(history)}


def cycle_summary(cycle: dict) -> str:
    keys = ("mode", "eligible", "skipped", "conflicts", "ci_minutes", "minutes_to_merge", "reverts")
    return json.dumps({k: cycle.get(k, 0) for k in keys}, separators=(",", ":"))


def record_cycle(store, cycle: dict) -> dict:
    """Write one merge cycle to the workflow ledger (shown on Timeline as a merge_cycle event)."""
    if cycle.get("mode") not in ("integration", "individual"):
        raise ValueError("mode must be integration or individual")
    return store.record_event(EVENT_KIND, cycle_summary(cycle))


def history_from_events(events: list) -> list:
    """Cycle dicts, oldest first. Accepts events in any order (sorted by id/ts); skips non-dict rows."""
    ordered = sorted((e for e in events if isinstance(e, dict) and e.get("kind") == EVENT_KIND),
                     key=lambda e: (_int(e.get("id")), e.get("ts") or 0))
    rows = []
    for e in ordered:
        try:
            row = json.loads(e.get("summary", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def history_from_store(store) -> list:
    """Read merge_cycle rows with a kind-filtered query, so other events cannot push them out."""
    return history_from_events(store.events(kind=EVENT_KIND, limit=WINDOW * 4))


def simulate(scenario: list, history: list | None = None) -> list:
    """Replay (eligible, outcome-dict) steps; each outcome is appended to history. For docs/tests."""
    hist, log = list(history or []), []
    for eligible, outcome in scenario:
        d = decide(eligible, hist)
        log.append(d["mode"])
        hist.append({"mode": d["mode"], "eligible": eligible, **outcome})
    return log


def view(history: list) -> dict:
    """Chosen threshold, reason and per-mode numbers, for the Health tab (#777)."""
    t, why = threshold(history)
    return {"threshold": t, "reason": why, "cycles": len(history), "stats": stats(history)}


if __name__ == "__main__":  # merge_policy.py N [history.json] -> decision JSON
    import sys
    n = int(sys.argv[1])
    hist = []
    if len(sys.argv) > 2:
        with open(sys.argv[2], encoding="utf-8") as fh:
            hist = json.load(fh)
    print(json.dumps(decide(n, hist), indent=2))
