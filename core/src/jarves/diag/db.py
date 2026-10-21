"""The diag_* tables in store.db and the health.* settings (memory.db agent_settings, dashboard-editable).

Everything here is local. Rows never hold message bodies, transcripts, contact data or secret values: metrics are
numbers, log codes are identifiers, evidence lines are built by logindex.context_lines (template text + numeric
fields only).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from ..userdata import DEFAULTS

SCHEMA = """
CREATE TABLE IF NOT EXISTS diag_metrics (
    res TEXT NOT NULL, ts INTEGER NOT NULL, name TEXT NOT NULL, value REAL NOT NULL, vmin REAL, vmax REAL,
    n INTEGER NOT NULL DEFAULT 1, app_version TEXT NOT NULL DEFAULT '', git_commit TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (res, name, ts));
CREATE TABLE IF NOT EXISTS diag_codes (
    res TEXT NOT NULL, ts INTEGER NOT NULL, code TEXT NOT NULL, level TEXT NOT NULL, component TEXT NOT NULL,
    n INTEGER NOT NULL, app_version TEXT NOT NULL DEFAULT '', git_commit TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (res, ts, code, level));
CREATE TABLE IF NOT EXISTS diag_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,
    value REAL, detail TEXT, ref TEXT, app_version TEXT NOT NULL DEFAULT '', git_commit TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS diag_events_k ON diag_events (kind, ts);
CREATE TABLE IF NOT EXISTS diag_findings (
    fingerprint TEXT PRIMARY KEY, source TEXT NOT NULL, category TEXT NOT NULL, severity TEXT NOT NULL,
    title TEXT NOT NULL, title_he TEXT NOT NULL, detail TEXT, detail_he TEXT, evidence TEXT, hints TEXT,
    cause TEXT, cause_he TEXT, fix TEXT, fix_he TEXT, effort TEXT, action TEXT, count INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'open', until REAL, decided_at REAL, decided_by TEXT);
CREATE TABLE IF NOT EXISTS diag_preflight (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, ms REAL NOT NULL, ok INTEGER NOT NULL,
    fatal INTEGER NOT NULL DEFAULT 0, items TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diag_canary (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, profile TEXT NOT NULL, passed INTEGER NOT NULL,
    failed INTEGER NOT NULL, blocked INTEGER NOT NULL, ms REAL, results TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diag_review_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL NOT NULL, finished REAL, trigger TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT, model TEXT, items INTEGER NOT NULL DEFAULT 0, selected TEXT,
    score REAL NOT NULL DEFAULT 0, est_usd REAL NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,
    request_bytes INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, excerpts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS diag_review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, rank INTEGER NOT NULL, fingerprint TEXT NOT NULL,
    severity TEXT NOT NULL, title TEXT NOT NULL, evidence TEXT, hypothesis TEXT, fix TEXT, effort TEXT,
    action TEXT, refs TEXT, evidence_n INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'open', until REAL, decided_at REAL, decided_by TEXT, proposal_id INTEGER);
CREATE INDEX IF NOT EXISTS diag_ri_run ON diag_review_items (run_id, rank);
CREATE TABLE IF NOT EXISTS diag_remediations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, remedy TEXT NOT NULL, finding TEXT,
    status TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0, automatic INTEGER NOT NULL DEFAULT 0,
    precheck TEXT, result TEXT, postcheck TEXT, by TEXT, args TEXT);
CREATE TABLE IF NOT EXISTS diag_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL, finding TEXT, created REAL NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, severity TEXT, status TEXT NOT NULL DEFAULT 'draft',
    issue_url TEXT, filed_at REAL, by TEXT);
CREATE INDEX IF NOT EXISTS diag_tickets_fp ON diag_tickets (fingerprint);
CREATE TABLE IF NOT EXISTS diag_config (
    ts REAL NOT NULL, digest TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY (ts));
"""

STATES = ("open", "accepted", "dismissed", "snoozed")

# health.* settings (memory.db agent_settings). Owner decisions OD-33..OD-37 are still open: these are the
# recommended answers of the V4 plan (section 11.1), each changeable on the Health page or with `jarvis health`.
SETTINGS: dict[str, object] = {
    "health.monitor_interval_s": 60,               # the sampler period (the event-driven part is continuous)
    "health.ai_review_enabled": True,              # OD-34 (recommended: ON, redacted metadata only)
    "health.ai_review_every_days": 2,              # OD-33 (default every 2 days)
    "health.ai_review_time": "11:30",              # outside the default quiet hours 22:00-07:00
    "health.ai_review_run_cap_usd": 0.10,          # OD-34 per-run cap
    "health.ai_review_month_cap_usd": 1.00,        # OD-34 monthly cap
    "health.ai_review_model": "gpt-6.1-sol",       # OpenAI (not Claude: independent family); checked 2026-10-20
    "health.ai_review_min_score": 3.0,             # slice below this score = "skipped: nothing new" ($0)
    "health.ai_review_max_items": 12,              # at most this many slice items per run (ranked, trimmed)
    "health.ai_review_job_id": "",
    "health.ai_review_last": 0.0,                  # epoch of the last review that ran or was skipped as not new
    "health.canary_enabled": True,
    "health.canary_time": "03:30",                 # nightly (plays nothing, so quiet hours are no obstacle)
    "health.canary_paid_voice": False,             # OD-36 (recommended: no; free local path only)
    "health.canary_job_id": "",
    "health.auto_face_link_restart": False,        # OD-37: the ONLY remediation the owner may grant for automatic use
    "health.spoken_alert_last": 0.0,               # at most one spoken health alert per day
    "health.ticket_repo": "",     # empty: drafts stay local files; the owner sets a repo to allow filing (V4-RP-2)
}
for _k, _v in SETTINGS.items():
    DEFAULTS.setdefault(_k, _v)


def ensure(store) -> None:
    """Create the diag tables once per Store object (idempotent)."""
    if getattr(store, "_diag_schema", False):
        return
    with store._lock:
        store._db.executescript(SCHEMA)
        store._db.commit()
    store._diag_schema = True


def write(store, sql: str, args: tuple = ()) -> int:
    ensure(store)
    with store._lock:
        cur = store._db.execute(sql, args)
        store._db.commit()
        return cur.lastrowid or 0


def many(store, sql: str, rows: list[tuple]) -> None:
    ensure(store)
    if not rows:
        return
    with store._lock:
        store._db.executemany(sql, rows)
        store._db.commit()


def rows(store, sql: str, args: tuple = ()) -> list[dict]:
    ensure(store)
    return store.rows(sql, args)


def j(v, default=None):
    if v in (None, ""):
        return default
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str, sort_keys=True)


def month_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    return datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp()


def version_info() -> tuple[str, str]:
    """(app_version, commit) of this install: build-info.json when installed, else the package version + git."""
    from .. import __version__
    commit = ""
    try:
        from ..updates import build_info
        bi = build_info() or {}
        commit = str(bi.get("commit") or bi.get("git_commit") or "")[:12]
        ver = str(bi.get("version") or __version__)
    except Exception:
        ver = __version__
    return ver, commit


def now() -> float:
    return time.time()
