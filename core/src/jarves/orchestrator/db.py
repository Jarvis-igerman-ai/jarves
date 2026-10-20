"""agents.db: the orchestrator's registry, durable inbox/outbox, timeline, approvals map, audit and day counters.

One SQLite file per profile home (`<home>/agents.db`, WAL, busy timeout): the Core, the CLI, the dashboard and the
plugin's hook processes may all open it. Kept apart from store.db so that "delete all conversations" or history
off never wipe the agent registry. Bounded: timeline rows per session, delivered messages and the audit (90 days)
are pruned on open.

Delivery (the inbox/outbox of #49): at-least-once with idempotency. `enqueue` is idempotent on message_id;
`claim` leases due messages for one consumer (lease = CLAIM_S, retried with backoff after a failed lease),
`ack` marks them delivered exactly once, `expire` drops what passed its TTL, `cancel` withdraws a queued one.
A consumer that dies mid-lease simply lets the lease run out: the message is offered again, and the receiver
dedups on message_id (`seen`).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .envelope import Envelope

CLAIM_S = 30.0
BACKOFF_S = (2.0, 5.0, 15.0, 60.0, 300.0)
MAX_ATTEMPTS = 8
TIMELINE_KEEP = 500
AUDIT_KEEP_DAYS = 90
DELIVERED_KEEP_DAYS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_session_id TEXT NOT NULL, project TEXT, project_root TEXT,
    cwd TEXT, branch TEXT, worktree TEXT, name TEXT, origin TEXT NOT NULL DEFAULT 'manual', kind TEXT,
    state TEXT NOT NULL DEFAULT 'UNKNOWN', waiting_for TEXT, status_source TEXT, capabilities TEXT, auth TEXT,
    channel INTEGER NOT NULL DEFAULT 0, plugin INTEGER NOT NULL DEFAULT 0, pid INTEGER, started_at REAL,
    last_seen_at REAL, last_progress_at REAL, last_summary TEXT, last_activity TEXT, files TEXT, blocker TEXT,
    next_step TEXT, cost_usd REAL NOT NULL DEFAULT 0, tokens_total INTEGER NOT NULL DEFAULT 0,
    turns INTEGER NOT NULL DEFAULT 0, ended_at REAL, attachable INTEGER NOT NULL DEFAULT 0, meta TEXT);
CREATE INDEX IF NOT EXISTS sessions_psid ON sessions (provider, provider_session_id);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, direction TEXT NOT NULL,
    session_id TEXT, kind TEXT NOT NULL, correlation_id TEXT, request_id TEXT, origin TEXT, hops INTEGER DEFAULT 0,
    payload TEXT, created REAL NOT NULL, expires_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, claimed_until REAL DEFAULT 0,
    claimed_by TEXT, delivered_at REAL, delivery TEXT);
CREATE INDEX IF NOT EXISTS messages_q ON messages (direction, session_id, state, next_attempt_at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
    summary TEXT, payload TEXT, message_id TEXT);
CREATE INDEX IF NOT EXISTS events_s ON events (session_id, id);
CREATE TABLE IF NOT EXISTS approvals (
    request_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, provider TEXT, approval_id TEXT, tool TEXT,
    command TEXT, cwd TEXT, risk TEXT, state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
    decided_at REAL, decision TEXT, channel TEXT);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, who TEXT NOT NULL, what TEXT NOT NULL,
    session_id TEXT, request_id TEXT, decision TEXT, result TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS audit_t ON audit (ts);
CREATE TABLE IF NOT EXISTS counters (day TEXT NOT NULL, key TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

SESSION_COLS = ("provider", "provider_session_id", "project", "project_root", "cwd", "branch", "worktree", "name",
                "origin", "kind", "state", "waiting_for", "status_source", "capabilities", "auth", "channel", "plugin",
                "pid", "started_at", "last_seen_at", "last_progress_at", "last_summary", "last_activity", "files",
                "blocker", "next_step", "cost_usd", "tokens_total", "turns", "ended_at", "attachable", "meta")
JSON_COLS = ("capabilities", "files", "meta")


def session_key(provider: str, provider_session_id: str) -> str:
    return f"{provider}:{provider_session_id}"


def today(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))


class AgentDB:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self.prune()

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ helpers
    def _rows(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, tuple(args))]

    def _x(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, tuple(args))

    def _tx(self):
        db, lock = self._db, self._lock

        class _T:
            def __enter__(self):
                lock.acquire()
                db.execute("BEGIN IMMEDIATE")
                return db

            def __exit__(self, et, ev, tb):
                try:
                    db.execute("ROLLBACK" if et else "COMMIT")
                finally:
                    lock.release()
                return False
        return _T()

    @staticmethod
    def _decode(r: dict | None) -> dict | None:
        if r is None:
            return None
        for k in JSON_COLS:
            if k in r and isinstance(r[k], str):
                try:
                    r[k] = json.loads(r[k])
                except ValueError:
                    r[k] = None
        return r

    def prune(self) -> None:
        now = time.time()
        with self._tx() as db:
            db.execute("DELETE FROM audit WHERE ts < ?", (now - AUDIT_KEEP_DAYS * 86400,))
            db.execute("DELETE FROM messages WHERE state IN ('acked','delivered','expired','cancelled','failed') "
                       "AND created < ?", (now - DELIVERED_KEEP_DAYS * 86400,))
            db.execute("DELETE FROM counters WHERE day < ?", (today(now - 40 * 86400),))

    # ------------------------------------------------------------------ sessions
    def upsert_session(self, sid: str, **fields) -> dict:
        fields = {k: v for k, v in fields.items() if k in SESSION_COLS}
        for k in JSON_COLS:
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        with self._tx() as db:
            cur = db.execute("SELECT id FROM sessions WHERE id = ?", (sid,)).fetchone()
            if cur is None:
                prov, _, psid = sid.partition(":")
                base = {"provider": prov, "provider_session_id": psid, **fields}
                cols = ",".join(["id", *base])
                db.execute(f"INSERT INTO sessions ({cols}) VALUES ({','.join('?' * (len(base) + 1))})",
                           (sid, *base.values()))
            elif fields:
                db.execute(f"UPDATE sessions SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                           (*fields.values(), sid))
        return self.session(sid) or {}

    def session(self, sid: str) -> dict | None:
        r = self._rows("SELECT * FROM sessions WHERE id = ?", (sid,))
        return self._decode(r[0]) if r else None

    def sessions(self, *, live_only: bool = False) -> list[dict]:
        rows = self._rows("SELECT * FROM sessions ORDER BY COALESCE(last_seen_at, started_at, 0) DESC")
        out = [self._decode(r) for r in rows]
        if live_only:
            out = [r for r in out if r["state"] not in ("STOPPED",)]
        return out

    def find_by_provider_id(self, provider: str, psid: str) -> dict | None:
        return self.session(session_key(provider, psid))

    # ------------------------------------------------------------------ timeline
    def add_event(self, sid: str, kind: str, summary: str = "", payload: dict | None = None,
                  message_id: str = "", ts: float | None = None) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO events (session_id, ts, kind, summary, payload, message_id) VALUES (?,?,?,?,?,?)",
                       (sid, ts or time.time(), kind, summary[:600],
                        json.dumps(payload or {}, ensure_ascii=False)[:4000], message_id))
            n = db.execute("SELECT COUNT(*) FROM events WHERE session_id = ?", (sid,)).fetchone()[0]
            if n > TIMELINE_KEEP:
                db.execute("DELETE FROM events WHERE id IN (SELECT id FROM events WHERE session_id = ? ORDER BY id "
                           "LIMIT ?)", (sid, n - TIMELINE_KEEP))

    def events(self, sid: str, limit: int = 100, since: float = 0.0) -> list[dict]:
        rows = self._rows("SELECT * FROM events WHERE session_id = ? AND ts >= ? ORDER BY id DESC LIMIT ?",
                          (sid, since, int(limit)))
        for r in rows:
            try:
                r["payload"] = json.loads(r["payload"] or "{}")
            except ValueError:
                r["payload"] = {}
        return list(reversed(rows))

    def seen(self, message_id: str) -> bool:
        return bool(self._rows("SELECT 1 FROM events WHERE message_id = ? LIMIT 1", (message_id,))) or \
            bool(self._rows("SELECT 1 FROM messages WHERE message_id = ? AND direction = 'in' LIMIT 1",
                            (message_id,)))

    # ------------------------------------------------------------------ inbox / outbox
    def enqueue(self, env: Envelope, direction: str = "out") -> bool:
        """Idempotent: a message_id already stored is not stored twice (returns False)."""
        with self._tx() as db:
            if db.execute("SELECT 1 FROM messages WHERE message_id = ?", (env.message_id,)).fetchone():
                return False
            db.execute("INSERT INTO messages (message_id, direction, session_id, kind, correlation_id, request_id, "
                       "origin, hops, payload, created, expires_at, state, next_attempt_at) VALUES "
                       "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (env.message_id, direction, env.session_id, env.kind, env.correlation_id, env.request_id,
                        env.origin, env.hops, json.dumps(env.payload, ensure_ascii=False), env.timestamp,
                        env.expires_at, "queued" if direction == "out" else "acked", 0))
        return True

    def claim(self, sid: str, consumer: str, limit: int = 5, now: float | None = None) -> list[dict]:
        """Lease the due outbound messages of one session (oldest first) to `consumer`."""
        now = now or time.time()
        with self._tx() as db:
            db.execute("UPDATE messages SET state = 'expired' WHERE direction = 'out' AND state IN ('queued','claimed')"
                       " AND expires_at < ?", (now,))
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM messages WHERE direction = 'out' AND session_id = ? AND (state = 'queued' OR "
                "(state = 'claimed' AND claimed_until < ?)) AND next_attempt_at <= ? ORDER BY id LIMIT ?",
                (sid, now, now, int(limit)))]
            for r in rows:
                att = int(r["attempts"]) + 1
                state = "failed" if att > MAX_ATTEMPTS else "claimed"
                back = BACKOFF_S[min(att - 1, len(BACKOFF_S) - 1)]
                db.execute("UPDATE messages SET state = ?, attempts = ?, claimed_until = ?, claimed_by = ?, "
                           "next_attempt_at = ? WHERE id = ?", (state, att, now + CLAIM_S, consumer, now + back,
                                                                r["id"]))
                r["state"], r["attempts"] = state, att
        out = []
        for r in rows:
            if r["state"] != "claimed":
                continue
            try:
                r["payload"] = json.loads(r["payload"] or "{}")
            except ValueError:
                r["payload"] = {}
            out.append(r)
        return out

    def ack(self, message_ids: Iterable[str], delivery: str = "") -> int:
        n = 0
        with self._tx() as db:
            for mid in message_ids:
                n += db.execute("UPDATE messages SET state = 'delivered', delivered_at = ?, delivery = ? WHERE "
                                "message_id = ? AND state IN ('queued','claimed')",
                                (time.time(), delivery, str(mid))).rowcount
        return n

    def cancel(self, message_id: str) -> bool:
        return self._x("UPDATE messages SET state = 'cancelled' WHERE message_id = ? AND state IN ('queued','claimed')",
                       (message_id,)).rowcount > 0

    def pending(self, sid: str | None = None) -> list[dict]:
        sql = "SELECT * FROM messages WHERE direction = 'out' AND state IN ('queued','claimed') AND expires_at >= ?"
        args: list = [time.time()]
        if sid:
            sql += " AND session_id = ?"
            args.append(sid)
        return self._rows(sql + " ORDER BY id", args)

    def message(self, message_id: str) -> dict | None:
        r = self._rows("SELECT * FROM messages WHERE message_id = ?", (message_id,))
        return r[0] if r else None

    def replies(self, correlation_id: str) -> list[dict]:
        rows = self._rows("SELECT * FROM messages WHERE direction = 'in' AND correlation_id = ? ORDER BY id",
                          (correlation_id,))
        for r in rows:
            try:
                r["payload"] = json.loads(r["payload"] or "{}")
            except ValueError:
                r["payload"] = {}
        return rows

    # ------------------------------------------------------------------ approvals map
    def add_approval(self, request_id: str, sid: str, **kw) -> None:
        cols = {k: v for k, v in kw.items() if k in ("provider", "approval_id", "tool", "command", "cwd", "risk")}
        with self._tx() as db:
            db.execute("INSERT OR IGNORE INTO approvals (request_id, session_id, created) VALUES (?,?,?)",
                       (request_id, sid, time.time()))
            if cols:
                db.execute(f"UPDATE approvals SET {', '.join(f'{k} = ?' for k in cols)} WHERE request_id = ?",
                           (*cols.values(), request_id))

    def approval(self, request_id: str) -> dict | None:
        r = self._rows("SELECT * FROM approvals WHERE request_id = ?", (request_id,))
        return r[0] if r else None

    def decide_approval(self, request_id: str, decision: str, channel: str = "") -> bool:
        return self._x("UPDATE approvals SET state = 'decided', decision = ?, decided_at = ?, channel = ? WHERE "
                       "request_id = ? AND state = 'pending'", (decision, time.time(), channel, request_id)).rowcount > 0

    def approvals(self, state: str | None = "pending") -> list[dict]:
        if state:
            return self._rows("SELECT * FROM approvals WHERE state = ? ORDER BY created DESC", (state,))
        return self._rows("SELECT * FROM approvals ORDER BY created DESC LIMIT 100")

    # ------------------------------------------------------------------ audit + counters
    def audit(self, who: str, what: str, *, session_id: str = "", request_id: str = "", decision: str = "",
              result: str = "", detail: str = "") -> None:
        self._x("INSERT INTO audit (ts, who, what, session_id, request_id, decision, result, detail) VALUES "
                "(?,?,?,?,?,?,?,?)", (time.time(), who[:40], what[:60], session_id[:200], request_id[:80],
                                      decision[:40], result[:200], detail[:400]))

    def audit_rows(self, session_id: str | None = None, limit: int = 200) -> list[dict]:
        if session_id:
            return self._rows("SELECT * FROM audit WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                              (session_id, int(limit)))
        return self._rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (int(limit),))

    def bump(self, key: str, n: int = 1) -> int:
        d = today()
        with self._tx() as db:
            db.execute("INSERT INTO counters (day, key, n) VALUES (?,?,?) ON CONFLICT(day, key) DO UPDATE SET "
                       "n = n + excluded.n", (d, key, n))
            return db.execute("SELECT n FROM counters WHERE day = ? AND key = ?", (d, key)).fetchone()[0]

    def count(self, key: str) -> int:
        r = self._rows("SELECT n FROM counters WHERE day = ? AND key = ?", (today(), key))
        return int(r[0]["n"]) if r else 0

    def get_meta(self, key: str) -> str | None:
        r = self._rows("SELECT value FROM meta WHERE key = ?", (key,))
        return r[0]["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self._x("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))
