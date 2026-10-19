"""Opt-in face recognition store: `<state_dir>/faces.db`, local only, never uploaded, never in logs.

Per person: the name, WHEN and HOW consent was given, and a few 128-d embeddings (numbers, not photos: no image is
ever stored). `forget(name)` deletes the person and every embedding; `forget_all()` wipes the table and VACUUMs so
the deleted rows do not linger in the file. Israeli privacy law treats biometric data as sensitive: keep it
minimal, local and deletable (docs/plans/CAMERA_PLAN.md section 3).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np

from .detect import SFACE_COSINE_MATCH, unit

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, key TEXT NOT NULL UNIQUE,
                                   consent TEXT NOT NULL, consent_ts REAL NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS embeddings (id INTEGER PRIMARY KEY, person INTEGER NOT NULL REFERENCES people(id)
                                       ON DELETE CASCADE, vec BLOB NOT NULL);
"""
MAX_EMBEDDINGS = 8          # per person (a few angles / lighting conditions)


def name_key(name: str) -> str:
    return " ".join(str(name).split()).casefold()


class FaceStore:
    def __init__(self, path: Path | str, threshold: float = SFACE_COSINE_MATCH) -> None:
        self.path, self.threshold = path, threshold
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def people(self) -> list[dict]:
        rows = self._db.execute("SELECT p.name, p.consent_ts, COUNT(e.id) FROM people p LEFT JOIN embeddings e "
                                "ON e.person = p.id GROUP BY p.id ORDER BY p.name").fetchall()
        return [{"name": n, "consent_ts": ts, "samples": c} for n, ts, c in rows]

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM people").fetchone()[0]

    def enroll(self, name: str, vectors: list[np.ndarray], consent: str) -> int:
        """Add (or extend) a person; returns how many embeddings are stored for them. `consent` records how the
        person agreed (e.g. "voice: confirmed on screen")."""
        name = " ".join(str(name).split())[:60]
        if not name or not vectors or not consent:
            raise ValueError("name, vectors and consent are required")
        now = time.time()
        cur = self._db.cursor()
        cur.execute("BEGIN")
        try:
            row = cur.execute("SELECT id FROM people WHERE key = ?", (name_key(name),)).fetchone()
            if row is None:
                cur.execute("INSERT INTO people (name, key, consent, consent_ts, created) VALUES (?, ?, ?, ?, ?)",
                            (name, name_key(name), consent, now, now))
                pid = cur.lastrowid
            else:
                pid = row[0]
                cur.execute("UPDATE people SET consent = ?, consent_ts = ? WHERE id = ?", (consent, now, pid))
            for v in vectors:
                cur.execute("INSERT INTO embeddings (person, vec) VALUES (?, ?)", (pid, unit(v).tobytes()))
            n = cur.execute("SELECT COUNT(*) FROM embeddings WHERE person = ?", (pid,)).fetchone()[0]
            if n > MAX_EMBEDDINGS:      # keep the newest samples
                cur.execute("DELETE FROM embeddings WHERE id IN (SELECT id FROM embeddings WHERE person = ? "
                            "ORDER BY id LIMIT ?)", (pid, n - MAX_EMBEDDINGS))
                n = MAX_EMBEDDINGS
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise
        return n

    def forget(self, name: str) -> int:
        """Delete one person and all their embeddings. Returns 1 if someone was deleted, else 0."""
        n = self._db.execute("DELETE FROM people WHERE key = ?", (name_key(name),)).rowcount
        if n:
            self._db.execute("VACUUM")
        return n

    def forget_all(self) -> int:
        n = self.count()
        self._db.execute("DELETE FROM embeddings")
        self._db.execute("DELETE FROM people")
        self._db.execute("VACUUM")
        return n

    def match(self, vec: np.ndarray) -> tuple[str, float] | None:
        """Best enrolled person above the threshold, or None (unknown face = guest)."""
        q = unit(vec)
        best: tuple[str, float] | None = None
        for name, blob in self._db.execute("SELECT p.name, e.vec FROM embeddings e JOIN people p ON p.id = e.person"):
            s = float(np.dot(q, np.frombuffer(blob, np.float32)))
            if best is None or s > best[1]:
                best = (name, s)
        return best if best is not None and best[1] >= self.threshold else None
