"""Conversation history store (SQLite, <state_dir>/store.db) for the dashboard, `jarvis cost` and self-diagnosis.

Fed by a LOG SINK (logs.add_sink): every structured log event the Core already writes (LIVE_STARTED, LIVE_USER_SAID,
LIVE_JARVES_SAID, LIVE_TOOL_CALL, TOOL_RESULT, LIVE_BACKEND_DONE, VOICE_ENDED, errors...) is turned into sessions,
turns, tool calls, backend delegations and events, so the Core needs no extra calls. The same `ingest()` replays an
old jarves.log (`jarvis diagnose --import-log`). Records pass through the log formatter first, so privacy mode
(log.privacy) applies here too: with privacy on, only lengths and timings are stored, never what was said.
Credentials never reach the store (the formatter masks them in both modes).

Costs are ESTIMATES from [pricing] (voice minutes x price, backend tokens x model price); the OpenAI bill is the truth.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import logs

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, core_session TEXT, kind TEXT NOT NULL DEFAULT 'live', started REAL NOT NULL, ended REAL,
    end_reason TEXT, model TEXT, backend_model TEXT, tier TEXT, voice_seconds REAL, stats TEXT, last_event REAL,
    diagnosed REAL);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, ts REAL NOT NULL, role TEXT NOT NULL,
    text TEXT, chars INTEGER NOT NULL DEFAULT 0, wait_s REAL);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL NOT NULL, name TEXT NOT NULL, delegation TEXT,
    args TEXT, ok INTEGER, code TEXT, origin TEXT, done_ts REAL, duration_s REAL);
CREATE TABLE IF NOT EXISTS delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL NOT NULL, kind TEXT NOT NULL DEFAULT 'voice',
    model TEXT, tier TEXT, status TEXT, duration_s REAL, input_tokens INTEGER, cached_tokens INTEGER,
    output_tokens INTEGER, web_searches INTEGER, calls INTEGER NOT NULL DEFAULT 1, billed INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts REAL NOT NULL, level TEXT, component TEXT,
    code TEXT, msg TEXT, fields TEXT);
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
    title TEXT NOT NULL, detail TEXT, evidence TEXT, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 1, last_session TEXT, status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS issue_hits (fingerprint TEXT NOT NULL, session_id TEXT NOT NULL,
    PRIMARY KEY (fingerprint, session_id));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS turns_s ON turns (session_id, ts);
CREATE INDEX IF NOT EXISTS tools_s ON tool_calls (session_id, ts);
CREATE INDEX IF NOT EXISTS deleg_s ON delegations (session_id, ts);
CREATE INDEX IF NOT EXISTS events_s ON events (session_id, ts);
CREATE INDEX IF NOT EXISTS sessions_t ON sessions (started);
"""
# Events kept besides turns/tools/delegations: every WARNING/ERROR plus these INFO codes (timeline + diagnosis).
KEEP_INFO = {"LIVE_STARTED", "LIVE_FIRST_AUDIO", "LIVE_EARLY_RESPONSE", "LIVE_PROGRESS", "LIVE_BARGE_IN",
             "LIVE_RESUMED", "LIVE_REMOTE_CLOSED", "LIVE_TIME_LIMIT", "LIVE_CONFIRM_NOTE", "VOICE_ENDED",
             "VOICE_NO_SPEECH", "VOICE_IDLE", "LIVE_ENDED", "WAKE_WORD", "RT_STARTED", "RT_BARGE_IN", "GREETING",
             "CORE_READY", "CORE_STOPPED", "LIVE_BACKEND_DONE", "LIVE_TASK", "TASK_STARTED", "TASK_DONE"}
_MASKED = re.compile(r"^<(\d+) chars>$")
_MODELS = re.compile(r"\(([^()\s]+)\s*->\s*([^()\s]+)\)")
OPEN_SESSION_STALE_S = 600          # an "open" session with no event for this long is not shown as live


def parse_ts(ts: str | float | None) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _text(v) -> tuple[str | None, int]:
    """(stored text, length). A privacy-masked value ("<12 chars>") keeps only its length."""
    if v is None:
        return None, 0
    s = str(v)
    m = _MASKED.match(s)
    if m:
        return None, int(m.group(1))
    return s, len(s)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


class Store:
    """All access goes through one connection guarded by a lock (writer thread, CLI and dashboard each own one)."""

    def __init__(self, path: Path | str, *, tier: str = "", backend_model: str = "") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        self.tier, self.backend_model = tier, backend_model
        self._cur: str | None = None              # open live session id
        self._pending_tools: dict[str, list[int]] = {}
        self._last_user_ts: float | None = None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args)]

    def commit(self) -> None:
        with self._lock:
            self._db.commit()

    # ------------------------------------------------------------------ ingestion
    def ingest(self, d: dict) -> str | None:
        """One structured log record (as written to jarves.log). Returns the id of a session that just ENDED."""
        code, ts = str(d.get("code") or "-"), parse_ts(d.get("ts"))
        level, comp = str(d.get("level") or "INFO"), str(d.get("component") or "-")
        ended = None
        with self._lock:
            if code in ("LIVE_STARTED", "RT_STARTED"):
                self._open(d, ts, "realtime" if code == "RT_STARTED" else "live")
            elif code == "CORE_STARTING":
                if self._cur:                      # previous process died without VOICE_ENDED
                    self._close(self._cur, None, "core_restarted", None, {})
                self._cur, self._pending_tools = None, {}
            sid = self._cur
            if sid:
                self._db.execute("UPDATE sessions SET last_event = ? WHERE id = ?", (ts, sid))
            if code in ("LIVE_USER_SAID", "LIVE_JARVES_SAID"):
                text, n = _text(d.get("text"))
                if sid is None:
                    sid = self._open(d, ts, "live")
                role = "user" if code == "LIVE_USER_SAID" else "assistant"
                self._db.execute("INSERT INTO turns (session_id, ts, role, text, chars, wait_s) VALUES (?,?,?,?,?,?)",
                                 (sid, ts, role, text, n, _num(d.get("wait_s"))))
            elif code == "LIVE_TOOL_CALL":
                name = str(d.get("tool") or str(d.get("msg", "")).rsplit(" ", 1)[-1])
                args = d.get("args")
                cur = self._db.execute("INSERT INTO tool_calls (session_id, ts, name, delegation, args) VALUES (?,?,?,?,?)",
                                       (sid, ts, name, d.get("delegation"),
                                        None if args is None else json.dumps(args, ensure_ascii=False)[:300]))
                self._pending_tools.setdefault(name, []).append(cur.lastrowid)
            elif code == "TOOL_RESULT":
                name = str(d.get("tool") or "")
                rc = str(d.get("msg", "")).rpartition("-> ")[2] or None
                pend = self._pending_tools.get(name) or []
                if pend:
                    rid = pend.pop(0)
                    t0 = self._db.execute("SELECT ts FROM tool_calls WHERE id = ?", (rid,)).fetchone()[0]
                    self._db.execute("UPDATE tool_calls SET ok=?, code=?, origin=?, done_ts=?, duration_s=? WHERE id=?",
                                     (int(bool(d.get("ok"))), rc, d.get("origin"), ts, round(ts - t0, 3), rid))
                else:                              # typed chat / local command: no matching LIVE_TOOL_CALL
                    self._db.execute("INSERT INTO tool_calls (session_id, ts, name, ok, code, origin, done_ts) "
                                     "VALUES (?,?,?,?,?,?,?)", (sid, ts, name, int(bool(d.get("ok"))), rc,
                                                                d.get("origin"), ts))
            elif code == "LIVE_BACKEND_DONE":
                status = str(d.get("msg", "")).rsplit(" ", 1)[-1]
                model = d.get("model") or (self._session_backend(sid) if sid else None) or self.backend_model
                self._db.execute(
                    "INSERT INTO delegations (session_id, ts, kind, model, tier, status, duration_s, input_tokens, "
                    "cached_tokens, output_tokens, web_searches) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, ts, "voice", model, d.get("tier") or self.tier, status, _num(d.get("backend_s")),
                     _num(d.get("input_tokens")), _num(d.get("cached_tokens")), _num(d.get("output_tokens")),
                     _num(d.get("web_searches"))))
            elif code == "AI_REPLY":
                plan = "chatgpt" in str(d.get("msg", "")).lower()
                self._db.execute(
                    "INSERT INTO delegations (session_id, ts, kind, model, tier, status, calls, billed, input_tokens, "
                    "output_tokens) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sid, ts, "text", d.get("model") or self.backend_model, "", "completed",
                     int(_num(d.get("model_calls")) or 1), 0 if plan else 1, _num(d.get("input_tokens")),
                     _num(d.get("output_tokens"))))
            elif code == "SKILL_REGISTERED":
                skills = json.loads(self.meta("skills") or "{}")
                name = str(d.get("msg", "")).removeprefix("skill ").removesuffix(" registered")
                skills[name] = {"tools": d.get("tools") or [], "ts": ts}
                self._set_meta("skills", json.dumps(skills, ensure_ascii=False))
            if level in ("WARNING", "ERROR") or code in KEEP_INFO:
                fields = {k: v for k, v in d.items()
                          if k not in ("ts", "level", "component", "code", "msg", "cid", "session")}
                self._db.execute("INSERT INTO events (session_id, ts, level, component, code, msg, fields) "
                                 "VALUES (?,?,?,?,?,?,?)", (sid, ts, level, comp, code, str(d.get("msg", ""))[:300],
                                                            json.dumps(fields, ensure_ascii=False, default=str)[:2000]))
            if code == "VOICE_ENDED":
                stats = {k: v for k, v in d.items() if k.endswith(("drops", "_s")) and _num(v) is not None}
                if sid is None:                    # session never logged its start: rebuild it from the length
                    sid = self._open(d, ts - float(_num(d.get("voice_seconds")) or 0), "live")
                self._close(sid, ts, d.get("reason"), _num(d.get("voice_seconds")), stats)
                ended, self._cur, self._pending_tools = sid, None, {}
        return ended

    def _session_backend(self, sid: str) -> str | None:
        r = self._db.execute("SELECT backend_model FROM sessions WHERE id = ?", (sid,)).fetchone()
        return r[0] if r else None

    def _open(self, d: dict, ts: float, kind: str) -> str:
        if self._cur:
            self._close(self._cur, None, "replaced", None, {})
        m = _MODELS.search(str(d.get("msg", "")))
        model = m.group(1) if m else (re.search(r"\(([^()]+)\)", str(d.get("msg", ""))) or [None, None])[1]
        sid = f"{d.get('session') or 'x'}-{datetime.fromtimestamp(ts).strftime('%Y%m%d-%H%M%S')}"
        self._db.execute("INSERT OR IGNORE INTO sessions (id, core_session, kind, started, model, backend_model, tier, "
                         "last_event) VALUES (?,?,?,?,?,?,?,?)",
                         (sid, d.get("session"), kind, ts, model, (m.group(2) if m else None) or self.backend_model,
                          self.tier, ts))
        self._cur, self._pending_tools = sid, {}
        return sid

    def _close(self, sid: str, ts: float | None, reason, seconds, stats: dict) -> None:
        if ts is None:
            r = self._db.execute("SELECT last_event FROM sessions WHERE id = ?", (sid,)).fetchone()
            ts = r[0] if r else time.time()
        self._db.execute("UPDATE sessions SET ended = ?, end_reason = ?, voice_seconds = COALESCE(?, voice_seconds), "
                         "stats = ? WHERE id = ?", (ts, reason, seconds, json.dumps(stats), sid))

    def newest_ts(self) -> float:
        r = self.x("SELECT MAX(t) FROM (SELECT MAX(ts) t FROM turns UNION ALL SELECT MAX(ts) FROM events "
                   "UNION ALL SELECT MAX(last_event) FROM sessions UNION ALL SELECT MAX(ts) FROM tool_calls)").fetchone()
        return r[0] or 0.0

    def ingest_log(self, path: Path, skip_until: float = 0.0) -> list[str]:
        """Replay a jarves.log (JSON lines; rotated files too). Records at or before `skip_until` are skipped
        (already recorded live). Returns the ids of sessions that ended."""
        ended = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and parse_ts(d.get("ts")) > skip_until:
                sid = self.ingest(d)
                if sid:
                    ended.append(sid)
        self.commit()
        return ended

    # ------------------------------------------------------------------ meta / retention
    def meta(self, key: str) -> str | None:
        r = self.x("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = "
                         "excluded.value", (key, value))

    def prune(self, keep_days: int) -> int:
        cut = time.time() - keep_days * 86400
        with self._lock:
            old = [r[0] for r in self._db.execute("SELECT id FROM sessions WHERE started < ?", (cut,))]
            for t in ("turns", "tool_calls", "delegations", "events"):
                self._db.execute(f"DELETE FROM {t} WHERE ts < ?", (cut,))
            self._db.execute("DELETE FROM sessions WHERE started < ?", (cut,))
            self._db.commit()
        return len(old)

    # ------------------------------------------------------------------ queries
    def sessions(self, limit: int = 100, day: str | None = None) -> list[dict]:
        if day:
            t0 = datetime.fromisoformat(day).timestamp()
            return self.rows("SELECT * FROM sessions WHERE started >= ? AND started < ? ORDER BY started DESC",
                             (t0, t0 + 86400))
        return self.rows("SELECT * FROM sessions ORDER BY started DESC LIMIT ?", (limit,))

    def session(self, sid: str) -> dict | None:
        r = self.rows("SELECT * FROM sessions WHERE id = ?", (sid,))
        return r[0] if r else None

    def live_session(self) -> dict | None:
        r = self.rows("SELECT * FROM sessions WHERE ended IS NULL AND last_event > ? ORDER BY started DESC LIMIT 1",
                      (time.time() - OPEN_SESSION_STALE_S,))
        return r[0] if r else None

    def timeline(self, sid: str) -> dict:
        return {"turns": self.rows("SELECT * FROM turns WHERE session_id = ? ORDER BY ts, id", (sid,)),
                "tools": self.rows("SELECT * FROM tool_calls WHERE session_id = ? ORDER BY ts, id", (sid,)),
                "delegations": self.rows("SELECT * FROM delegations WHERE session_id = ? ORDER BY ts, id", (sid,)),
                "events": self.rows("SELECT * FROM events WHERE session_id = ? ORDER BY ts, id", (sid,))}

    def issues(self, status: str | None = "open") -> list[dict]:
        order = "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, last_seen DESC"
        if status:
            return self.rows(f"SELECT * FROM issues WHERE status = ? ORDER BY {order}", (status,))
        return self.rows(f"SELECT * FROM issues ORDER BY {order}")

    def set_issue_status(self, iid: int, status: str) -> bool:
        if status not in ("open", "resolved", "ignored"):
            raise ValueError("status must be open, resolved or ignored")
        with self._lock:
            n = self._db.execute("UPDATE issues SET status = ? WHERE id = ?", (status, iid)).rowcount
            self._db.commit()
        return n > 0

    def tool_stats(self, days: int = 30) -> dict[str, dict]:
        out = {}
        for r in self.rows("SELECT name, COUNT(*) n, SUM(ok = 0) fails, AVG(duration_s) avg_s, MAX(ts) last "
                           "FROM tool_calls WHERE ts > ? GROUP BY name", (time.time() - days * 86400,)):
            out[r["name"]] = r
        return out

    def skills(self) -> dict:
        return json.loads(self.meta("skills") or "{}")


# ---------------------------------------------------------------------- cost (estimates)
def _price_table(pricing) -> dict:
    from .config import PricingConfig
    return {**PricingConfig().models, **pricing.models}


def delegation_cost(row: dict, pricing) -> float:
    if not row.get("billed", 1):
        return 0.0                                # ChatGPT plan: no API spending
    models = _price_table(pricing)
    p = models.get(row.get("model") or "") or models.get("gpt-6-luna")
    calls = row.get("calls") or 1
    tin = row.get("input_tokens")
    tout = row.get("output_tokens")
    cached = row.get("cached_tokens") or 0
    if tin is None:
        tin = pricing.est_input_tokens * calls
    if tout is None:
        tout = pricing.est_output_tokens * calls
    usd = ((max(tin - cached, 0) * p.input + cached * p.cached_input + tout * p.output) / 1e6
           * pricing.tier_multiplier.get(row.get("tier") or "", 1.0))
    return usd + (row.get("web_searches") or 0) * pricing.web_search_per_call


def session_cost(store: Store, sess: dict, pricing) -> dict:
    """{"voice": usd, "backend": usd, "total": usd, "estimated_tokens": bool, "voice_minutes": m}"""
    secs = sess.get("voice_seconds")
    if secs is None:                              # still running: wall clock so far
        secs = max(0.0, (sess.get("ended") or sess.get("last_event") or time.time()) - sess["started"])
    per_min = pricing.voice_per_minute.get(sess.get("model") or "", pricing.voice_per_minute.get("gpt-live-1", 0.05))
    voice = secs / 60 * per_min
    dels = store.rows("SELECT * FROM delegations WHERE session_id = ?", (sess["id"],))
    backend = sum(delegation_cost(r, pricing) for r in dels)
    return {"voice": round(voice, 5), "backend": round(backend, 5), "total": round(voice + backend, 5),
            "voice_minutes": round(secs / 60, 2), "backend_responses": len(dels),
            "estimated_tokens": any(r.get("input_tokens") is None for r in dels),
            "web_searches_counted": any(r.get("web_searches") is not None for r in dels)}


def daily_costs(store: Store, pricing, days: int = 30) -> list[dict]:
    """Per local day: conversations, voice minutes, voice/backend/text USD (estimates), newest first."""
    out: dict[str, dict] = {}
    start = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for s in store.rows("SELECT * FROM sessions WHERE started >= ?", (start.timestamp(),)):
        day = datetime.fromtimestamp(s["started"]).date().isoformat()
        c = session_cost(store, s, pricing)
        d = out.setdefault(day, {"day": day, "sessions": 0, "voice_minutes": 0.0, "voice": 0.0, "backend": 0.0,
                                 "text": 0.0})
        d["sessions"] += 1
        d["voice_minutes"] += c["voice_minutes"]
        d["voice"] += c["voice"]
        d["backend"] += c["backend"]
    for r in store.rows("SELECT * FROM delegations WHERE session_id IS NULL AND ts >= ?", (start.timestamp(),)):
        day = datetime.fromtimestamp(r["ts"]).date().isoformat()
        d = out.setdefault(day, {"day": day, "sessions": 0, "voice_minutes": 0.0, "voice": 0.0, "backend": 0.0,
                                 "text": 0.0})
        d["text"] += delegation_cost(r, pricing)
    for d in out.values():
        d["total"] = d["voice"] + d["backend"] + d["text"]
        for k in ("voice_minutes", "voice", "backend", "text", "total"):
            d[k] = round(d[k], 4)
    return sorted(out.values(), key=lambda d: d["day"], reverse=True)


# ---------------------------------------------------------------------- live log sink
class StoreSink(logging.Handler):
    """Log handler -> queue -> one writer thread (SQLite never blocks the voice event loop). After a conversation
    ends, the writer runs the rule-based self-diagnosis for it."""

    def __init__(self, store: Store, cfg=None) -> None:
        super().__init__(logging.INFO)
        self.store, self.cfg = store, cfg
        self._fmt = logs.JsonFormatter()
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="jarves-store", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._q.put(self._fmt.format(record))      # same masking as the log file (privacy + credentials)
        except Exception:
            pass                                      # history must never break logging

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            try:
                ended = self.store.ingest(json.loads(item))
                if self._q.empty():
                    self.store.commit()
                if ended and self.cfg is not None:
                    self.store.commit()
                    from . import diagnose
                    diagnose.run(self.store, self.cfg, [ended])
            except Exception as e:                    # never log from here (it would feed back into the sink)
                print(f"jarves history: {type(e).__name__}: {str(e)[:120]}", flush=True)
        self.store.commit()

    def close(self) -> None:
        if self._thread.is_alive():
            self._q.put(None)
            self._thread.join(timeout=5)
        super().close()


def attach(home: Path, cfg) -> StoreSink | None:
    """Start recording conversation history into <home>/store.db (call after logs.setup). None if disabled."""
    for old in [h for h in logs._sinks if isinstance(h, StoreSink)]:
        detach(old)                               # one history writer per process (a Core that was not stopped)
    if not cfg.history.enabled:
        return None
    store = Store(home / "store.db", tier=cfg.ai.voice_service_tier, backend_model=cfg.ai.text_model)
    store.prune(cfg.history.keep_days)
    sink = StoreSink(store, cfg)
    logs.add_sink(sink)
    return sink


def detach(sink: StoreSink | None) -> None:
    if sink is None:
        return
    logs.remove_sink(sink)
    sink.close()
    sink.store.close()
