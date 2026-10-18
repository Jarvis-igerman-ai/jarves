"""Central daily cost ceiling (CR-01, F3). Every paid/metered request goes through Budget.

- ai_requests:   model requests: API Agents SDK calls, ChatGPT-plan Responses calls, GPT-Live delegated backend
                 responses, cloud speech for typed replies. Limit: ai.daily_request_limit.
- voice_seconds: live voice session time. Limit: ai.daily_realtime_minutes * 60.

Rules: reserve BEFORE sending; a reserved request that reached the provider is counted even if it failed or was
cancelled (conservative: it may have been billed). Only a request proven never sent may be released uncounted.
Usage reported after the fact (session seconds) is recorded with record().

Cross-process (F3): reservations are ROWS in the same SQLite file as the usage counters. reserve() runs
`BEGIN IMMEDIATE` (one writer across all JARVES processes: Core, `jarvis ask`, `jarvis say`), sums committed usage
+ live reservations, and inserts its row only if the limit allows - so two processes can never both take the last
unit. A reservation left by a crashed process expires after RESERVATION_TTL_S and is then COUNTED (conservative:
it may have been sent), never silently freed. The provider-side project spend limit remains the final safety layer.
"""
from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import date

from . import logs
from .config import AIConfig
from .memory import MemoryStore

KINDS = ("ai_requests", "voice_seconds")
RESERVATION_TTL_S = 600          # longer than any request timeout (<= 240 s) incl. a delegated voice turn

SCHEMA = """
CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
                                         n INTEGER NOT NULL, pid INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage (day TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL,
                                  PRIMARY KEY (day, kind));
"""


class BudgetExceeded(Exception):
    def __init__(self, kind: str, limit: int, used: int) -> None:
        super().__init__(f"daily {kind} limit {limit} reached ({used} used)")
        self.kind, self.limit, self.used = kind, limit, used
        self.code = "AI_BUDGET" if kind == "ai_requests" else "VOICE_BUDGET"
        self.action = ("raise ai.daily_request_limit" if kind == "ai_requests"
                       else "raise ai.daily_realtime_minutes")


@dataclass
class Reservation:
    budget: "Budget"
    kind: str
    n: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    done: bool = field(default=False)

    def commit(self, actual: int | None = None) -> int:
        """Count the request(s); call once the request was sent (success, failure or cancel)."""
        if self.done:
            return self.budget.used(self.kind)
        self.done = True
        return self.budget._settle(self, self.n if actual is None else actual)

    def release(self) -> None:
        """Only for a request that was provably never sent to the provider."""
        if not self.done:
            self.done = True
            self.budget._settle(self, 0)

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *exc) -> None:
        self.commit()          # sent-or-unknown -> counted (conservative)


class Budget:
    def __init__(self, memory: MemoryStore, ai: AIConfig) -> None:
        self.memory, self.ai = memory, ai
        self._path = memory.path
        self._db = sqlite3.connect(self._path, timeout=15, isolation_level=None, check_same_thread=False)
        self._db.executescript(SCHEMA)
        if self._path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")

    # ------------------------------------------------------------------ shared-state helpers (one transaction each)
    def _tx(self, fn):
        """Run fn(cursor) inside BEGIN IMMEDIATE: serialises every budget writer across processes."""
        cur = self._db.cursor()
        for attempt in range(50):
            try:
                cur.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as e:      # busy beyond the 15 s timeout: retry briefly
                if "locked" not in str(e) or attempt == 49:
                    raise
                time.sleep(0.05)
        try:
            out = fn(cur)
            cur.execute("COMMIT")
            return out
        except BaseException:
            cur.execute("ROLLBACK")
            raise

    @staticmethod
    def _expire(cur, now: float) -> None:
        """Stale reservations (crashed process) are counted, not freed: they may have been sent."""
        for day, kind, n in cur.execute("SELECT day, kind, SUM(n) FROM reservations WHERE created < ? "
                                        "GROUP BY day, kind", (now - RESERVATION_TTL_S,)).fetchall():
            cur.execute("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) "
                        "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count", (day, kind, n))
            logs.warn("budget", "BUDGET_STALE_RESERVATION", "expired reservation counted", kind=kind, n=n)
        cur.execute("DELETE FROM reservations WHERE created < ?", (now - RESERVATION_TTL_S,))

    @staticmethod
    def _used(cur, kind: str, day: str) -> int:
        r = cur.execute("SELECT count FROM usage WHERE day = ? AND kind = ?", (day, kind)).fetchone()
        return r[0] if r else 0

    @staticmethod
    def _held(cur, kind: str, day: str) -> int:
        return cur.execute("SELECT COALESCE(SUM(n), 0) FROM reservations WHERE day = ? AND kind = ?",
                           (day, kind)).fetchone()[0]

    def _settle(self, r: Reservation, count: int) -> int:
        day = date.today().isoformat()

        def go(cur):
            cur.execute("DELETE FROM reservations WHERE id = ?", (r.id,))
            if count > 0:
                cur.execute("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) "
                            "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count", (day, r.kind, count))
            return self._used(cur, r.kind, day)
        return self._tx(go)

    # ------------------------------------------------------------------ public API
    def limit(self, kind: str) -> int:
        return self.ai.daily_request_limit if kind == "ai_requests" else self.ai.daily_realtime_minutes * 60

    def used(self, kind: str) -> int:
        return self.memory.usage_today(kind)

    def held(self, kind: str) -> int:
        """Units reserved right now by ALL processes."""
        return self._held(self._db.cursor(), kind, date.today().isoformat())

    def remaining(self, kind: str) -> int:
        day = date.today().isoformat()
        cur = self._db.cursor()
        return max(0, self.limit(kind) - self._used(cur, kind, day) - self._held(cur, kind, day))

    def check(self, kind: str = "ai_requests", n: int = 1) -> None:
        if self.remaining(kind) < n:
            raise BudgetExceeded(kind, self.limit(kind), self.used(kind))

    def reserve(self, kind: str = "ai_requests", n: int = 1) -> Reservation:
        """Atomically (across processes): raise BudgetExceeded unless used + held + n <= limit, else hold n."""
        r = Reservation(self, kind, n)
        day, limit = date.today().isoformat(), self.limit(kind)

        def go(cur):
            now = time.time()
            self._expire(cur, now)
            used = self._used(cur, kind, day)
            if used + self._held(cur, kind, day) + n > limit:
                raise BudgetExceeded(kind, limit, used)
            cur.execute("INSERT INTO reservations (id, day, kind, n, pid, created) VALUES (?, ?, ?, ?, ?, ?)",
                        (r.id, day, kind, n, os.getpid(), now))
        try:
            self._tx(go)
        except BudgetExceeded as e:
            logs.warn("budget", e.code, str(e), action=e.action)
            raise
        return r

    def record(self, kind: str, n: int) -> int:
        """Usage known only afterwards (voice seconds). Not for model requests: those must reserve()."""
        if n <= 0:
            return self.used(kind)
        day = date.today().isoformat()

        def go(cur):
            cur.execute("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) "
                        "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count", (day, kind, n))
            return self._used(cur, kind, day)
        return self._tx(go)

    def exhausted(self, kind: str) -> bool:
        return self.remaining(kind) <= 0

    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error:
            pass
