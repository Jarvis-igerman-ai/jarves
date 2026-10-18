"""User-controlled persistent memory (SQLite). Only facts the user explicitly asks to keep are stored.

Conversation history is NOT persisted here (kept in RAM per run); usage counters support the cost ceiling.
"""
from __future__ import annotations

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
MAX_TEXT = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
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
    def add(self, text: str) -> int:
        text = text.strip()
        if not text:
            raise ValueError("empty memory")
        if len(text) > MAX_TEXT:
            raise ValueError(f"memory longer than {MAX_TEXT} characters")
        now = _now()
        return self._x("INSERT INTO memories (text, created, updated) VALUES (?, ?, ?)", (text, now, now)).lastrowid

    def list(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,))]

    def search(self, q: str, limit: int = 10) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM memories WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                                         (f"%{q}%", limit))]

    def update(self, mid: int, text: str) -> bool:
        return self._x("UPDATE memories SET text = ?, updated = ? WHERE id = ?", (text.strip(), _now(), mid)).rowcount > 0

    def delete(self, mid: int) -> bool:
        return self._x("DELETE FROM memories WHERE id = ?", (mid,)).rowcount > 0

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

    # --- usage (cost ceiling)
    def bump_usage(self, kind: str, n: int = 1) -> int:
        day = date.today().isoformat()
        self._x("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) "
                "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count", (day, kind, n))
        return self.usage_today(kind)

    def usage_today(self, kind: str) -> int:
        r = self._x("SELECT count FROM usage WHERE day = ? AND kind = ?", (date.today().isoformat(), kind)).fetchone()
        return r[0] if r else 0
