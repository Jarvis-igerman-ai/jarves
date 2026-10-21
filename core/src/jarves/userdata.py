"""User data for the proactive agent (SW-V2 WP-M3): jobs, job runs, reports, notes, alerts, folder grants,
diagnostic proposals, the audit log and their settings. Same SQLite file as the memory (<state_dir>/memory.db, WAL):
one user database to back up and restore (spec 12), no new database per feature. The Core, the dashboard process
and the CLI each open their own connection; every write commits at once, so the other processes see it.

Times are UTC epoch seconds (REAL); local wall-clock meaning (timezone, DST) lives only in scheduler.py.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL DEFAULT 'user', name TEXT NOT NULL,
    status TEXT NOT NULL,                       -- draft | active | paused | done
    kind TEXT NOT NULL, spec TEXT NOT NULL, tz TEXT NOT NULL, human TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,                       -- reminder | research | tool
    task TEXT NOT NULL, tool TEXT, tool_args TEXT,
    skills TEXT NOT NULL DEFAULT '[]', max_requests INTEGER NOT NULL DEFAULT 2, retries INTEGER NOT NULL DEFAULT 1,
    backoff_s REAL NOT NULL DEFAULT 60, max_age_s REAL NOT NULL DEFAULT 21600, allow_overlap INTEGER NOT NULL DEFAULT 0,
    keep_reports INTEGER NOT NULL DEFAULT 30, delivery TEXT NOT NULL DEFAULT '["report"]',
    next_run REAL, last_run REAL, last_outcome TEXT, last_error TEXT, run_requested REAL,
    created REAL NOT NULL, updated REAL NOT NULL, approved_at REAL, approved_by TEXT);
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, idem TEXT UNIQUE NOT NULL, scheduled_for REAL NOT NULL,
    trigger TEXT NOT NULL,                      -- schedule | catch_up | manual | resume
    state TEXT NOT NULL,                        -- running | done | partial | failed | skipped | interrupted
    late INTEGER NOT NULL DEFAULT 0, missed INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0, started REAL, finished REAL, error TEXT, report_id INTEGER, pid INTEGER);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, run_id INTEGER, title TEXT NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL,                       -- ok | partial | failed
    sources TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, scheduled_for REAL, late INTEGER NOT NULL DEFAULT 0,
    read_at REAL);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, text TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, list_name TEXT NOT NULL, text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, done_at REAL);
CREATE TABLE IF NOT EXISTS timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,       -- timer | alarm
    label TEXT NOT NULL DEFAULT '', due REAL NOT NULL, created REAL NOT NULL,
    status TEXT NOT NULL,                       -- pending | fired | missed | cancelled
    fired_at REAL);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',      -- active | paused | done | dropped
    target_date TEXT NOT NULL DEFAULT '', percent INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL, updated REAL NOT NULL, done_at REAL, last_checkin REAL);
CREATE TABLE IF NOT EXISTS goal_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id INTEGER NOT NULL, text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0, outward INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, done_at REAL);
CREATE INDEX IF NOT EXISTS goal_steps_g ON goal_steps (goal_id);
CREATE TABLE IF NOT EXISTS learned_prefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, norm TEXT NOT NULL, topic TEXT NOT NULL DEFAULT 'other',
    state TEXT NOT NULL,                        -- proposed | confirmed | declined
    evidence TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'voice',
    created REAL NOT NULL, updated REAL NOT NULL, confirmed_at REAL);
CREATE INDEX IF NOT EXISTS learned_prefs_s ON learned_prefs (state);
CREATE INDEX IF NOT EXISTS list_i ON list_items (list_name, done);
CREATE INDEX IF NOT EXISTS timers_s ON timers (status, due);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'info',
    priority TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', dedupe_key TEXT, channel TEXT,
    ref TEXT, user_requested INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,                       -- pending | delivered | suppressed | expired | dismissed
    reason TEXT, delivered_via TEXT, delivered_at REAL, attempts INTEGER NOT NULL DEFAULT 0, expires REAL);
CREATE TABLE IF NOT EXISTS grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, mode TEXT NOT NULL,
    status TEXT NOT NULL,                       -- pending | active | revoked | denied
    reason TEXT, requested_by TEXT, created REAL NOT NULL, decided_at REAL, decided_by TEXT);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE NOT NULL, title TEXT NOT NULL, detail TEXT,
    evidence TEXT, applier TEXT, params TEXT, risk TEXT NOT NULL,
    status TEXT NOT NULL,                       -- proposed | observed | applied | rejected | reverted | failed
    created REAL NOT NULL, decided_at REAL, decided_by TEXT, result TEXT, revert TEXT);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT,
    detail TEXT);
CREATE TABLE IF NOT EXISTS agent_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS runs_job ON job_runs (job_id, scheduled_for);
CREATE INDEX IF NOT EXISTS reports_t ON reports (created);
CREATE INDEX IF NOT EXISTS alerts_s ON alerts (status, ts);
CREATE INDEX IF NOT EXISTS alerts_k ON alerts (dedupe_key, ts);
"""

# Dashboard-editable settings (agent_settings, JSON values) and their defaults.
DEFAULTS: dict[str, Any] = {
    "scheduler.timezone": "Asia/Jerusalem",     # the owner's zone; DST handled by zoneinfo (tzdata)
    "scheduler.max_age_s": 6 * 3600,            # default for new jobs: a missed run older than this is skipped
    "scheduler.workweek": "0-4",                # "ימי חול": cron weekdays, Sunday-Thursday (or "1-5")
    "alerts.quiet_start": "22:00",
    "alerts.quiet_end": "07:00",
    "alerts.quiet_enabled": True,
    "alerts.urgent_in_quiet": True,             # urgent alerts may still speak during quiet hours
    "alerts.paused_until": 0.0,                 # "pause proactive alerts" (epoch; 0 = not paused)
    "alerts.max_per_hour": 6,
    "alerts.dedupe_s": 6 * 3600,
    "alerts.pending_max_age_s": 24 * 3600,      # a pending alert older than this is dropped (expired)
    "alerts.delivery": {"low": "popup", "normal": "popup", "high": "speech", "urgent": "speech"},
    "autonomy.level": "suggest_and_ask",
    "autonomy.grants": [],                      # appliers the user allowed for approved_low_risk
    "briefing.city": "Tel Aviv",                # morning briefing weather place (Open-Meteo, no key)
    "briefing.lat": 32.0853,
    "briefing.lon": 34.7818,
    # self-check (selfcheck.py, owner request 20 Oct 2026)
    "self_check.on_start": "crash",             # crash = after an unclean stop | always = every start | never
    "self_check.error_burst": 10,               # this many ERROR records in one run of the Core start a check (0 = off)
    "self_check.min_interval_s": 3600,          # automatic checks at most this often
    "self_check.days": 7,                       # log window of one check
    "self_check.ai_pass": True,                 # also run the existing AI review (diagnosis_llm_pass rules apply)
    "self_check.ai_sessions": 2,                # at most this many conversations reviewed per check
    "self_check.job_id": "",                    # the weekly scheduler job (created once)
    "self_check.last_auto": 0.0,
    "self_check.last_run": 0.0,
    "selftest.job_id": "",                      # selfspeed.py: the daily self-test job (created once)
    "speedtest.job_id": "",                     # selfspeed.py: the weekly speed-test job (created once)
    # guided account setup (connect.py): the once-a-day "Google is not connected" reminder
    "connect.nudge": True,
    "connect.last_nudge": 0.0,
    "connect.skipped": [],                      # items the owner chose "never ask again" for (jarvis connect)
    "files.quota_mb": 200,
    "files.max_file_mb": 10,
}


class UserDB:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        """Execute and commit (one statement = one transaction; other processes see it at once)."""
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args)]

    def row(self, sql: str, args: tuple = ()) -> dict | None:
        r = self.rows(sql, args)
        return r[0] if r else None

    # --- settings
    def setting(self, key: str) -> Any:
        r = self.row("SELECT value FROM agent_settings WHERE key = ?", (key,))
        if r is None:
            return DEFAULTS.get(key)
        try:
            return json.loads(r["value"])
        except ValueError:
            return DEFAULTS.get(key)

    def set_setting(self, key: str, value: Any, *, actor: str = "user") -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        self.x("INSERT INTO agent_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
               "value = excluded.value", (key, json.dumps(value, ensure_ascii=False)))
        self.audit(actor, "setting", key, value)

    def settings(self, prefix: str = "") -> dict:
        return {k: self.setting(k) for k in DEFAULTS if k.startswith(prefix)}

    # --- audit (configuration changes, approvals, autonomy-granted actions: spec 12)
    def audit(self, actor: str, action: str, target: str = "", detail: Any = None) -> None:
        self.x("INSERT INTO audit (ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
               (time.time(), actor, action, str(target),
                None if detail is None else json.dumps(detail, ensure_ascii=False, default=str)[:2000]))

    def audit_log(self, limit: int = 200) -> list[dict]:
        return self.rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))


def open_db(home: Path) -> UserDB:
    home.mkdir(parents=True, exist_ok=True)
    return UserDB(home / "memory.db")


def ctx_db(ctx) -> UserDB:
    """The dashboard request context's connection (one per dashboard process)."""
    db = getattr(ctx, "_userdata", None)
    if db is None:
        db = open_db(ctx.home)
        ctx._userdata = db
    return db
