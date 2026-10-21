"""Regression detection between versions: every metric / code row carries app_version and commit. After an
update, the first 24 h of the new version are compared with the previous version's last 7 days: error-rate deltas
per code, new codes, tool failure rates, voice latencies and the performance metrics (M budgets: CPU, RAM).
Output: a version report (Health page) and one "version report" finding ("since 4.0.1, LIVE_RECONNECT is 3x more
frequent").
"""
from __future__ import annotations

import time

from . import db

FIRST_S = 24 * 3600
PREV_S = 7 * 86400
REPORT_DAYS = 7                    # the report finding stays while the new version is younger than this
RATE_STEP = 2.0                    # a code at least this many times more frequent per hour is a regression
LATENCY_STEP = 1.25                # median latency / perf metric 25% worse
LATENCY_METRICS = ("turn_wait_s", "wake_to_first_audio_s", "tool_ms", "cpu_pct", "ram_mb", "loop_lag_ms")


def versions(store) -> list[dict]:
    """Versions seen in the hourly data, oldest first: {version, commit, first, last}."""
    rs = db.rows(store, "SELECT app_version v, MAX(git_commit) c, MIN(ts) f, MAX(ts) l FROM ("
                        "SELECT app_version, git_commit, ts FROM diag_codes WHERE res='h' UNION ALL "
                        "SELECT app_version, git_commit, ts FROM diag_metrics WHERE res='h') "
                        "WHERE app_version != '' GROUP BY app_version ORDER BY f")
    return [{"version": r["v"], "commit": r["c"], "first": r["f"], "last": r["l"]} for r in rs]


def _rates(store, v: str, a: float, b: float) -> dict[str, float]:
    hours = max(1.0, (b - a) / 3600)
    return {r["code"]: r["n"] / hours for r in db.rows(
        store, "SELECT code, SUM(n) n FROM diag_codes WHERE res='h' AND app_version=? AND ts >= ? AND ts < ? "
               "AND level IN ('ERROR','WARNING') GROUP BY code", (v, int(a), int(b)))}


def _median(store, v: str, name: str, a: float, b: float) -> float | None:
    vals = sorted(r["value"] for r in db.rows(
        store, "SELECT value FROM diag_metrics WHERE res='h' AND app_version=? AND name=? AND ts >= ? AND ts < ?",
        (v, name, int(a), int(b))))
    return vals[len(vals) // 2] if vals else None


def _tool_fail_rate(store, v: str, a: float, b: float) -> float:
    n = db.rows(store, "SELECT COUNT(*) n FROM diag_events WHERE kind='tool_fail' AND app_version=? AND ts >= ? "
                       "AND ts < ?", (v, a, b))[0]["n"]
    return n / max(1.0, (b - a) / 3600)


def version_report(store, *, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    vs = versions(store)
    if len(vs) < 2:
        return None
    cur, prev = vs[-1], vs[-2]
    t1 = cur["first"]
    if now - t1 > REPORT_DAYS * 86400:
        return None
    a, b = t1, min(t1 + FIRST_S, now + 1)
    pa, pb = max(prev["first"], t1 - PREV_S), t1
    if b - a < 3600 or pb - pa < 3600:
        return None
    rc, rp = _rates(store, cur["version"], a, b), _rates(store, prev["version"], pa, pb)
    items = []
    for code, r in sorted(rc.items(), key=lambda kv: -kv[1]):
        base = rp.get(code, 0.0)
        if base == 0.0 and r * (b - a) / 3600 >= 3:
            items.append({"kind": "new_code", "code": code, "rate_per_h": round(r, 2), "previous_per_h": 0.0})
        elif base > 0 and r / base >= RATE_STEP and r * (b - a) / 3600 >= 5:
            items.append({"kind": "rate_step", "code": code, "ratio": round(r / base, 1), "rate_per_h": round(r, 2),
                          "previous_per_h": round(base, 2)})
    for name in LATENCY_METRICS:
        mc, mp = _median(store, cur["version"], name, a, b), _median(store, prev["version"], name, pa, pb)
        if mc is not None and mp is not None and mp > 0 and mc / mp >= LATENCY_STEP:
            items.append({"kind": "metric", "metric": name, "ratio": round(mc / mp, 2), "now": round(mc, 2),
                          "previous": round(mp, 2)})
    tc, tp = _tool_fail_rate(store, cur["version"], a, b), _tool_fail_rate(store, prev["version"], pa, pb)
    if tc * (b - a) / 3600 >= 3 and tc >= RATE_STEP * max(tp, 1e-9):
        items.append({"kind": "tool_failures", "rate_per_h": round(tc, 2), "previous_per_h": round(tp, 2)})
    return {"version": cur["version"], "commit": cur["commit"], "previous": prev["version"],
            "previous_commit": prev["commit"], "since": t1, "hours_compared": round((b - a) / 3600, 1),
            "items": items, "regressed": bool(items)}


def _line(it: dict) -> str:
    if it["kind"] == "new_code":
        return f"new code {it['code']} ({it['rate_per_h']}/h)"
    if it["kind"] == "rate_step":
        return f"{it['code']} is {it['ratio']}x more frequent ({it['previous_per_h']}/h -> {it['rate_per_h']}/h)"
    if it["kind"] == "metric":
        return f"{it['metric']} {it['ratio']}x worse ({it['previous']} -> {it['now']})"
    return f"tool failures {it['previous_per_h']}/h -> {it['rate_per_h']}/h"


def version_findings(store, *, now: float | None = None) -> list:
    from .anomaly import DiagFinding
    rep = version_report(store, now=now)
    if not rep or not rep["regressed"]:
        return []
    lines = [_line(i) for i in rep["items"]]
    codes = [i["code"] for i in rep["items"] if i.get("code")]
    return [DiagFinding(
        f"version:{rep['version']}", "regression", "high" if len(rep["items"]) >= 3 else "medium",
        f"Since {rep['version']}: {lines[0]}" + (f" (+{len(lines) - 1} more)" if len(lines) > 1 else ""),
        f"מאז גרסה {rep['version']}: {lines[0]}" + (f" (ועוד {len(lines) - 1})" if len(lines) > 1 else ""),
        detail=f"First {rep['hours_compared']} h of {rep['version']} ({rep['commit'] or 'no commit'}) against the last "
               f"7 days of {rep['previous']} ({rep['previous_commit'] or 'no commit'}).",
        detail_he=f"השוואה של השעות הראשונות של {rep['version']} מול 7 הימים האחרונים של {rep['previous']}.",
        evidence=lines[:5], cause="a regression introduced by the update", cause_he="נסיגה שנכנסה עם העדכון",
        fix="Open the version report on the Health page; if it is serious, roll back the update (approval).",
        fix_he="פתחו את דוח הגרסה בעמוד התקינות; אם זה חמור, החזירו את העדכון לאחור (באישור).", effort="hour",
        action={"type": "remedy", "remedy": "rollback_update"}, count=len(rep["items"]), codes=codes,
        source="monitor")]
