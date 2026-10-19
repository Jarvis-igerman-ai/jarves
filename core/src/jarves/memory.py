"""User-controlled persistent memory (SQLite): atomic facts with provenance, settings, usage counters.

A fact comes from the user asking to remember it (source "user", confidence 1.0) or from the background curator
(source "auto": durable facts/preferences extracted after a conversation, see curator.py) with the session id,
date and confidence. Every fact is listable, editable and deletable (recall / forget tools, dashboard).
Conversation history is NOT persisted here (kept in RAM per run); usage counters support the cost ceiling.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
                                     created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage (day TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL,
                                  PRIMARY KEY (day, kind));
"""
# provenance columns (added Oct 2026 for the curator; an older memory.db is migrated in place)
_COLUMNS = {"source": "TEXT NOT NULL DEFAULT 'user'", "session": "TEXT", "confidence": "REAL NOT NULL DEFAULT 1.0",
            "kind": "TEXT NOT NULL DEFAULT 'fact'"}
MAX_TEXT = 500
DUPLICATE_RATIO = 0.85     # normalized facts at least this similar are the same fact
_PUNCT = re.compile(r"[^\w\s]+")
_NIQQUD = re.compile(r"[֑-ׇ]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str) -> str:
    """Comparison form of a fact: case, punctuation, spacing and Hebrew niqqud removed."""
    return " ".join(_PUNCT.sub(" ", _NIQQUD.sub("", text.casefold())).split())


class MemoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        have = {r[1] for r in self._db.execute("PRAGMA table_info(memories)")}
        for col, decl in _COLUMNS.items():
            if col not in have:
                self._db.execute(f"ALTER TABLE memories ADD COLUMN {col} {decl}")
        self._db.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    # --- memories
    def add(self, text: str, *, source: str = "user", session: str | None = None, confidence: float = 1.0,
            kind: str = "fact") -> int:
        text = text.strip()
        if not text:
            raise ValueError("empty memory")
        if len(text) > MAX_TEXT:
            raise ValueError(f"memory longer than {MAX_TEXT} characters")
        now = _now()
        return self._x("INSERT INTO memories (text, created, updated, source, session, confidence, kind) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (text, now, now, source, session, max(0.0, min(1.0, confidence)), kind)).lastrowid

    def find_duplicate(self, text: str) -> dict | None:
        """The stored fact that says the same thing (equal or near-identical normalized wording), if any."""
        n = normalize(text)
        if not n:
            return None
        best, best_r = None, 0.0
        for r in self._x("SELECT * FROM memories ORDER BY id DESC LIMIT 500"):
            m = normalize(r["text"])
            ratio = 1.0 if m == n else difflib.SequenceMatcher(None, m, n).ratio()
            if ratio > best_r:
                best, best_r = dict(r), ratio
        return best if best_r >= DUPLICATE_RATIO else None

    def upsert(self, text: str, *, source: str = "auto", session: str | None = None, confidence: float = 0.7,
               kind: str = "fact") -> tuple[int, bool]:
        """Add a fact unless an equivalent one is stored: (id, created). A duplicate keeps the existing wording,
        the higher confidence wins and its date is refreshed (heard again = confirmed)."""
        dup = self.find_duplicate(text)
        if dup is None:
            return self.add(text, source=source, session=session, confidence=confidence, kind=kind), True
        self._x("UPDATE memories SET updated = ?, confidence = MAX(confidence, ?), "
                "source = CASE WHEN ? = 'user' THEN 'user' ELSE source END WHERE id = ?",
                (_now(), max(0.0, min(1.0, confidence)), source, dup["id"]))
        return dup["id"], False

    def get(self, mid: int) -> dict | None:
        r = self._x("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
        return dict(r) if r else None

    def list(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,))]

    def for_prompt(self, limit: int = 20, min_confidence: float = 0.0) -> list[dict]:
        """Facts for the model's instructions: what the user asked to keep first, then the most confident and
        most recently confirmed automatic ones."""
        return [dict(r) for r in self._x(
            "SELECT * FROM memories WHERE confidence >= ? ORDER BY (source = 'user') DESC, confidence DESC, "
            "updated DESC, id DESC LIMIT ?", (min_confidence, limit))]

    def search(self, q: str, limit: int = 10) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM memories WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                                         (f"%{q}%", limit))]

    def update(self, mid: int, text: str) -> bool:
        return self._x("UPDATE memories SET text = ?, updated = ? WHERE id = ?", (text.strip(), _now(), mid)).rowcount > 0

    def delete(self, mid: int) -> bool:
        return self._x("DELETE FROM memories WHERE id = ?", (mid,)).rowcount > 0

    def delete_session(self, session: str) -> int:
        """Forget everything the curator learned from one conversation."""
        return self._x("DELETE FROM memories WHERE session = ? AND source = 'auto'", (session,)).rowcount

    def clear(self) -> int:
        return self._x("DELETE FROM memories").rowcount

    def count(self) -> int:
        return self._x("SELECT COUNT(*) FROM memories").fetchone()[0]

    # --- settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self._x("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self._x("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))

    def delete_setting(self, key: str) -> bool:
        return self._x("DELETE FROM settings WHERE key = ?", (key,)).rowcount > 0

    # --- usage (cost ceiling)
    def bump_usage(self, kind: str, n: int = 1) -> int:
        day = date.today().isoformat()
        self._x("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) "
                "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count", (day, kind, n))
        return self.usage_today(kind)

    def usage_today(self, kind: str) -> int:
        r = self._x("SELECT count FROM usage WHERE day = ? AND kind = ?", (date.today().isoformat(), kind)).fetchone()
        return r[0] if r else 0
