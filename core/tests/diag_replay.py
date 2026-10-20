"""SW-V4 O replay harness: synthetic hourly metrics, log-code counts and events in a REAL store.db (tmp dir), with a
fixed clock and a fixed seed, plus the 12 planted anomalies of tests/fixtures/diag_replay/planted.json.

Normal behaviour (the "boring" replay): CPU with a daily rhythm, flat RAM, slowly growing state folder, steady
handles/threads/loop lag, reply waits ~1.5 s (lognormal) in the daytime, occasional warning codes (Poisson), a few
wake-without-speech per day, a rare tool failure, nightly canaries all PASS, a green preflight, one config snapshot.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime
from pathlib import Path

from jarves.diag import db

PLANTED = json.loads((Path(__file__).parent / "fixtures" / "diag_replay" / "planted.json").read_text(encoding="utf-8"))
ALL = tuple(PLANTED["anomalies"])
NOW = datetime(2026, 10, 20, 12, 0).timestamp()
H = 3600
V0, C0, V1, C1 = "4.0.0", "aaa111", "4.0.1", "bbb222"
TOOLS = ("music_play", "notes_add", "timer_set", "web_search", "reminder_add", "list_add", "briefing", "weather")


def poisson(rng: random.Random, lam: float) -> int:
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def build(store, *, now: float = NOW, days: int = 16, plant: tuple[str, ...] = (), seed: int = 7) -> None:
    rng = random.Random(seed)
    db.ensure(store)
    start = int((now - days * 86400) // H * H)
    upd = now - 24 * H if "version_regression" in plant else None
    creep0 = now - 7 * 86400
    metrics, codes, events = [], [], []
    tool_fail_i = 0
    for t in range(start, int(now), H):
        ver, com = (V1, C1) if upd is not None and t >= upd else (V0, C0)
        hod = datetime.fromtimestamp(t).hour
        in_win = t >= now - 48 * H
        m = {
            "cpu_pct": 0.30 + 0.10 * math.sin(2 * math.pi * hod / 24) + rng.gauss(0, 0.03),
            "ram_mb": 117 + rng.gauss(0, 1.0),
            "disk_state_mb": 500 + (t - start) / 86400 * 1.0 + rng.gauss(0, 0.2),
            "handles": 400 + rng.gauss(0, 5),
            "threads": 11 + rng.gauss(0, 0.5),
            "loop_lag_ms": 2 + abs(rng.gauss(0, 0.5)),
            "tool_ms": 300 + rng.gauss(0, 30),
            "scheduler_late_s": 1 + abs(rng.gauss(0, 0.3)),
        }
        if "ram_creep" in plant and t >= creep0:
            m["ram_mb"] += 2.0 * (t - creep0) / H                         # 2 MB/h
        if "disk_growth" in plant and t >= creep0:
            m["disk_state_mb"] += 200.0 * (t - creep0) / 86400             # 200 MB/day
        if "version_regression" in plant and ver == V1:
            m["tool_ms"] *= 1.4                                            # regression below the anomaly floor
        # reply waits (daytime turns) -> events + the hourly mean
        waits = []
        if 8 <= hod <= 22:
            for _ in range(3):
                w = 1.5 * math.exp(rng.gauss(0, 0.3))
                waits.append(w)
                events.append((t + rng.randint(0, 3599), "turn", "reply_wait", w, "", f"c{t}", ver, com))
            if rng.random() < 0.8:
                w = 1.2 * math.exp(rng.gauss(0, 0.1))
                events.append((t + 100, "turn", "wake_to_first_audio", w, "", "", ver, com))
        if "slow_turns" in plant and now - 30 * H <= t < now - 25 * H:
            waits.append(15.0)
            events.append((t + 1800, "turn", "reply_wait", 15.0, "", f"slow{t}", ver, com))
        if waits:
            m["turn_wait_s"] = sum(waits) / len(waits)
        for name, v in m.items():
            metrics.append(("h", t, name, v, v, v, 60, ver, com))
        # log codes
        c = {("LIVE_RECONNECT", "WARNING"): poisson(rng, 0.2), ("MUSIC_TIMEOUT", "WARNING"): poisson(rng, 0.05),
             ("TOOL_BAD_ARGS", "WARNING"): poisson(rng, 0.1), ("WAKE_WORD", "INFO"): poisson(rng, 1.0),
             ("VOICE_NO_SPEECH", "INFO"): poisson(rng, 0.04)}
        if "rate_step_3x" in plant and in_win:
            c[("LIVE_RECONNECT", "WARNING")] = 0 if (t // H) % 3 == 0 else 1          # 0.67/h vs 0.2/h
        if "new_error_code" in plant and now - 20 * H <= t < now - 16 * H:
            c[("PLANTED_NEW_ERROR", "ERROR")] = 1
        if "false_action_claim" in plant and t in (int(now // H * H) - 10 * H, int(now // H * H) - 5 * H):
            c[("LIVE_FALSE_ACTION_CLAIM", "WARNING")] = 1
        if "wake_false_positive_burst" in plant and in_win and hod in (20, 21, 22):
            c[("VOICE_NO_SPEECH", "INFO")] = c[("VOICE_NO_SPEECH", "INFO")] + 4
        for (code, level), n in c.items():
            if n:
                codes.append(("h", t, code, level, "test", n, ver, com))
        # a rare tool failure, each time another tool and conversation (never a cluster)
        if rng.random() < 0.3 / 24:
            tool_fail_i += 1
            events.append((t + 600, "tool_fail", TOOLS[tool_fail_i % len(TOOLS)], 1.0, "", f"f{t}", ver, com))
        if "tool_fails_3_conversations" in plant and t in [int(now // H * H) - k * H for k in (30, 20, 12, 6)]:
            events.append((t + 300, "tool_fail", "calendar_list", 1.0, "", f"conv{t}", ver, com))
    db.many(store, "INSERT OR REPLACE INTO diag_metrics (res, ts, name, value, vmin, vmax, n, app_version, git_commit) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", metrics)
    db.many(store, "INSERT OR REPLACE INTO diag_codes (res, ts, code, level, component, n, app_version, git_commit) "
                   "VALUES (?,?,?,?,?,?,?,?)", codes)
    db.many(store, "INSERT INTO diag_events (ts, kind, name, value, detail, ref, app_version, git_commit) "
                   "VALUES (?,?,?,?,?,?,?,?)", events)
    # nightly canaries
    names = ("voice_stack", "music", "calendar_read", "dashboard_load", "installer_check", "bridges", "scheduler")
    nights = [int(now // 86400 * 86400) - d * 86400 + 3 * H for d in range(min(days, 14), -1, -1)]
    nights = [n for n in nights if n < now]
    for i, n in enumerate(nights):
        bad = "canary_fails_twice" in plant and i >= len(nights) - 2
        res = [{"name": x, "status": "FAIL" if bad and x == "dashboard_load" else
                ("BLOCKED" if x == "bridges" else "PASS"), "detail": "console error" if bad and x == "dashboard_load"
                else "", "ms": 100.0} for x in names]
        db.write(store, "INSERT INTO diag_canary (ts, profile, passed, failed, blocked, ms, results) VALUES (?,?,?,?,?,?,?)",
                 (n, "owner", sum(r["status"] == "PASS" for r in res), sum(r["status"] == "FAIL" for r in res),
                  sum(r["status"] == "BLOCKED" for r in res), 700.0, db.dumps(res)))
    # the latest preflight
    items = [{"id": i, "title": i, "title_he": i, "component": "core", "status": "green", "detail": "ok", "fix": "",
              "fix_he": "", "fatal": False, "ms": 1.0, "action": None}
             for i in ("config", "state_dir", "disk", "ports", "godot", "audio", "wake_model", "secrets",
                       "google_token", "features", "schema", "shutdown", "clock")]
    if "google_token_near_expiry" in plant:
        g = next(i for i in items if i["id"] == "google_token")
        g.update(status="amber", detail="the Google sign-in expires in 20 h", fix="Reconnect Google now",
                 component="google", action={"type": "remedy", "remedy": "reconnect_google"})
    db.write(store, "INSERT INTO diag_preflight (ts, ms, ok, fatal, items) VALUES (?,?,?,?,?)",
             (now - 2 * H, 300.0, 1, 0, db.dumps(items)))
    # configuration snapshots (non-secret keys)
    snap = {"voice.wake_threshold": 0.5, "ai.voice_service_tier": "priority", "voice.mode": "live"}
    db.write(store, "INSERT OR REPLACE INTO diag_config (ts, digest, snapshot) VALUES (?,?,?)", (start, "d0", db.dumps(snap)))
    if "config_drift" in plant:
        db.write(store, "INSERT INTO diag_config (ts, digest, snapshot) VALUES (?,?,?)",
                 (now - 10 * H, "d1", db.dumps({**snap, "voice.wake_threshold": 0.6})))


def expected_findings(plant) -> set[str]:
    return {PLANTED["anomalies"][p]["finding"] for p in plant}


def expected_slice(plant) -> dict[str, set[str]]:
    return {p: set(PLANTED["anomalies"][p]["slice"]) for p in plant}
