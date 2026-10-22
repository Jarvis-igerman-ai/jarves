"""Seed, verify and record data for the upgrade / rollback smoke test (SW-V4 L). Python stdlib only (it runs on the CI
runner before and after the installer, outside any JARVES python).

    python -I packaging/seed_legacy.py seed     --home HOME      write an old-layout data folder (a conversation, a memory,
                                                                 a job, settings) the way the 0.1.0.x series left it
    python -I packaging/seed_legacy.py verify   --home HOME [--legacy]
                                                                 every seeded row is still there; without --legacy the data
                                                                 is also at the newest schema (schema.json) with a
                                                                 pre-migration backup; with --legacy it is the old layout again
                                                                 (no schema.json, no later columns) = a successful rollback
    python -I packaging/seed_legacy.py previous --home HOME --installer EXE --old-version V --new-version V
                                                                 write the "previous version" record the in-app updater leaves
                                                                 (installer copy + newest pre-migration backup), so
                                                                 `jarvis update --rollback` can be exercised without a release

The SQL below is a FROZEN copy of the old layout: it must not follow the current code (the migrations bring it forward).
Exit code 0 = ok, 1 = a check failed (listed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

STORE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, core_session TEXT, kind TEXT NOT NULL DEFAULT 'live', started REAL NOT NULL, ended REAL,
    end_reason TEXT, model TEXT, backend_model TEXT, tier TEXT, voice_seconds REAL, stats TEXT, last_event REAL,
    diagnosed REAL);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, ts REAL NOT NULL, role TEXT NOT NULL,
    text TEXT, chars INTEGER NOT NULL DEFAULT 0, wait_s REAL);
CREATE TABLE IF NOT EXISTS delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL NOT NULL, kind TEXT NOT NULL DEFAULT 'voice',
    model TEXT, tier TEXT, status TEXT, duration_s REAL, input_tokens INTEGER, cached_tokens INTEGER,
    output_tokens INTEGER, web_searches INTEGER, calls INTEGER NOT NULL DEFAULT 1, billed INTEGER NOT NULL DEFAULT 1);
"""
MEMORY_SQL = """
CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
                                     created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL DEFAULT 'user', name TEXT NOT NULL, status TEXT NOT NULL,
    kind TEXT NOT NULL, spec TEXT NOT NULL, tz TEXT NOT NULL, human TEXT NOT NULL DEFAULT '', action TEXT NOT NULL,
    task TEXT NOT NULL, tool TEXT, tool_args TEXT,
    skills TEXT NOT NULL DEFAULT '[]', max_requests INTEGER NOT NULL DEFAULT 2, retries INTEGER NOT NULL DEFAULT 1,
    backoff_s REAL NOT NULL DEFAULT 60, max_age_s REAL NOT NULL DEFAULT 21600, allow_overlap INTEGER NOT NULL DEFAULT 0,
    keep_reports INTEGER NOT NULL DEFAULT 30, delivery TEXT NOT NULL DEFAULT '["report"]',
    next_run REAL, last_run REAL, last_outcome TEXT, last_error TEXT, run_requested REAL,
    created REAL NOT NULL, updated REAL NOT NULL, approved_at REAL, approved_by TEXT);
"""
CONFIG = '[voice]\nmode = "off"\nwake_threshold = 0.95\n\n[music]\nprovider = "none"\n\n[link]\nport = 18765\n'
FEATURES = {"chimes": {"enabled": False}, "autostart": {"enabled": True}}
IDENTITY = {"display_name": "SeedMax"}
SETTINGS_FILES = {"config.toml": CONFIG, "features.json": json.dumps(FEATURES), "identity.json": json.dumps(IDENTITY)}
PRESERVED_FILES = {
    "models/owner-wake.onnx": b"fake owner model, never executed",
    "secrets.env": b"FAKE_INSTALLER_TEST_CREDENTIAL=not-a-real-key\n",
    "google/token.json": b'{"fake": "offline-test-only"}',
    "profiles/owner/profile.json": b'{"id":"owner","name":"Owner"}',
    "profiles/owner/config.toml": b'[voice]\nmode = "off"\nwake_threshold = 0.95\n',
}
LATEST_SCHEMA = 3                    # core/src/jarves/migrations.py LATEST (test_installer_clean.py keeps them equal)


def seed(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "store.db")
    db.executescript(STORE_SQL)
    now = time.time() - 10  # realistic old data; avoid Core retention pruning a 1970 fixture
    db.execute("INSERT INTO sessions (id, kind, started) VALUES ('s1', 'typed', ?)", (now,))
    db.execute("INSERT INTO turns (session_id, ts, role, text, chars) VALUES ('s1', ?, 'user', 'hello legacy', 12)", (now + 1,))
    db.execute("INSERT INTO delegations (session_id, ts, kind, status) VALUES ('s1', ?, 'voice', 'done')", (now + 2,))
    db.commit()
    db.close()
    db = sqlite3.connect(home / "memory.db")
    db.executescript(MEMORY_SQL)
    db.execute("INSERT INTO memories (text, created, updated) VALUES ('likes tea', '2026-01-01', '2026-01-01')")
    db.execute("INSERT INTO settings (key, value) VALUES ('override:voice.voice', 'marin')")
    db.execute("INSERT INTO jobs (id, name, status, kind, spec, tz, action, task, created, updated) VALUES "
               "('job1', 'morning', 'active', 'daily', '08:00', 'Asia/Jerusalem', 'reminder', 'stretch', 1.0, 1.0)")
    db.commit()
    db.close()
    for name, text in SETTINGS_FILES.items():
        (home / name).write_text(text, encoding="utf-8")
    for name, data in PRESERVED_FILES.items():
        target = home / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (home / "schema.json").unlink(missing_ok=True)


def _cols(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}


def verify(home: Path, legacy: bool) -> list[str]:
    bad = []
    try:
        s = sqlite3.connect(f"file:{(home / 'store.db').as_posix()}?mode=ro", uri=True)
        m = sqlite3.connect(f"file:{(home / 'memory.db').as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return [f"cannot open the databases: {e}"]
    want = [("a conversation turn", s, "SELECT COUNT(*) FROM turns WHERE text='hello legacy'"),
            ("the session", s, "SELECT COUNT(*) FROM sessions WHERE id='s1'"),
            ("the delegation row", s, "SELECT COUNT(*) FROM delegations WHERE session_id='s1'"),
            ("the memory", m, "SELECT COUNT(*) FROM memories WHERE text='likes tea'"),
            ("the voice setting", m, "SELECT COUNT(*) FROM settings WHERE key='override:voice.voice' AND value='marin'"),
            ("the job", m, "SELECT COUNT(*) FROM jobs WHERE id='job1' AND task='stretch'")]
    for what, db, q in want:
        if db.execute(q).fetchone()[0] != 1:
            bad.append(f"{what} is missing")
    marker = home / "schema.json"
    if legacy:
        if marker.exists():
            bad.append("schema.json is still there: the data was not rolled back to the old layout")
        if "response_id" in _cols(s, "delegations") or "source" in _cols(m, "memories"):
            bad.append("the databases still have the newer columns: not the old layout")
    else:
        try:
            schema = json.loads(marker.read_text(encoding="utf-8"))["schema"]
        except (OSError, ValueError, KeyError):
            schema = None
        if schema != LATEST_SCHEMA:
            bad.append(f"schema.json says {schema}, expected {LATEST_SCHEMA}: the data was not migrated")
        if "response_id" not in _cols(s, "delegations") or "source" not in _cols(m, "memories"):
            bad.append("the additive columns are missing after the upgrade")
        if not list((home / "backups").glob("jarves-backup-*-pre-migration*.zip")):
            bad.append("no pre-migration backup was taken before the data migration")
    s.close()
    m.close()
    for name, text in SETTINGS_FILES.items():
        if not (home / name).is_file():
            bad.append(f"{name} is missing")
        elif name != "config.toml" and (home / name).read_text(encoding="utf-8") != text:
            bad.append(f"{name} was changed")
    for name, data in PRESERVED_FILES.items():
        target = home / name
        if not target.is_file() or target.read_bytes() != data:
            bad.append(f"preserved file changed: {name}")
    return bad


def previous(home: Path, installer: Path, old_version: str, new_version: str) -> dict:
    """What prepare_install() leaves behind (jarves.updates.keep_previous), minus the download."""
    pre = sorted((home / "backups").glob("jarves-backup-*-pre-migration*.zip"), key=lambda p: p.stat().st_mtime)
    if not pre:
        raise SystemExit("no pre-migration backup to roll back to (start the new version once first)")
    pdir = home / "updates" / "previous"
    pdir.mkdir(parents=True, exist_ok=True)
    name = f"JARVES-Setup-{old_version}.exe"
    data = Path(installer).read_bytes()
    (pdir / name).write_bytes(data)
    # the oldest pre-migration backup was taken at the first start of the new version = the data as the old version left it
    with zipfile.ZipFile(pre[0]) as z:
        note = json.loads(z.read("manifest.json")).get("note", "")
    rec = {"version": old_version, "to": new_version, "backup": str(pre[0]), "installer": name,
           "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "source": None, "signed": True,
           "schema": 1, "created_ts": time.time(), "note": note}
    (pdir / "previous.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    (home / "updates" / "pending.json").write_text(json.dumps(
        {"from": old_version, "to": new_version, "state": "ok", "attempts": 0, "ts": time.time()}), encoding="utf-8")
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="seed_legacy.py")
    ap.add_argument("cmd", choices=("seed", "verify", "previous"))
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--installer", type=Path)
    ap.add_argument("--old-version")
    ap.add_argument("--new-version")
    a = ap.parse_args(argv)
    if a.cmd == "seed":
        seed(a.home)
        print(f"SEED: old-layout data written to {a.home}")
        return 0
    if a.cmd == "previous":
        rec = previous(a.home, a.installer, a.old_version, a.new_version)
        print(f"PREVIOUS: recorded {rec['version']} (installer {rec['size']} bytes, backup {Path(rec['backup']).name})")
        return 0
    bad = verify(a.home, a.legacy)
    for b in bad:
        print("SEED VERIFY:", b)
    print("SEED VERIFY (" + ("old layout" if a.legacy else "upgraded") + "): " + ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
