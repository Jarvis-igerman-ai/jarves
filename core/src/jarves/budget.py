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
unit. Expiry (#13): a reservation is expired only when its OWNER PROCESS IS GONE (crash) or it is older than
HARD_TTL_S (backstop for PID reuse); a long-running live owner keeps its reservation. Expired rows are COUNTED
(conservative: it may have been sent), never silently freed, and settlement counts a row at most once (an owner
committing a row that expiry already counted adds nothing). Usage is settled on the reservation's own day.
The provider-side project spend limit remains the final safety layer.
"""
from __future__ import annotations

import ctypes
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date

from . import logs
from .config import AIConfig
from .memory import MemoryStore

KINDS = ("ai_requests", "voice_seconds")
HARD_TTL_S = 6 * 3600            # backstop only: a reservation of a LIVE owner is never expired before this
RESERVATION_TTL_S = HARD_TTL_S   # (old name)


def pid_alive(pid: int) -> bool:
    """Is process `pid` still running? Never sends a signal (os.kill on Windows would terminate it)."""
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5 or k32.GetLastError() == 5   # access denied = exists
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259                           # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

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
    day: str = field(default_factory=lambda: date.today().isoformat())
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
    def __init__(self, memory: MemoryStore, ai: AIConfig, *, clock=time.time, alive=pid_alive) -> None:
        self.memory, self.ai = memory, ai
        self._clock, self._alive = clock, alive          # tests inject a fake clock / liveness
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

    def _expire(self, cur, now: float) -> None:
        """Reservations whose owner process is gone (or older than HARD_TTL_S) are counted, not freed: they may
        have been sent. A live owner's reservation is left alone, however long its request/session runs."""
        me = os.getpid()
        for rid, day, kind, n, pid, created in cur.execute(
                "SELECT id, day, kind, n, pid, created FROM reservations").fetchall():
            if now - created <= HARD_TTL_S and (pid == me or self._alive(pid)):
                continue
            cur.execute("DELETE FROM reservations WHERE id = ?", (rid,))
            cur.execute("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) "
                        "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count", (day, kind, n))
            logs.warn("budget", "BUDGET_STALE_RESERVATION", "reservation of a stopped process counted",
                      kind=kind, n=n, owner_pid=pid, age_s=round(now - created))

    @staticmethod
    def _used(cur, kind: str, day: str) -> int:
        r = cur.execute("SELECT count FROM usage WHERE day = ? AND kind = ?", (day, kind)).fetchone()
        return r[0] if r else 0

    @staticmethod
    def _held(cur, kind: str, day: str) -> int:
        return cur.execute("SELECT COALESCE(SUM(n), 0) FROM reservations WHERE day = ? AND kind = ?",
                           (day, kind)).fetchone()[0]

    def _settle(self, r: Reservation, count: int) -> int:
        """Settle once: if expiry already counted this row (it is gone), the owner's commit adds nothing."""
        def go(cur):
            gone = cur.execute("DELETE FROM reservations WHERE id = ?", (r.id,)).rowcount == 0
            if gone:
                logs.warn("budget", "BUDGET_ALREADY_SETTLED", "reservation was already counted by expiry", kind=r.kind)
            elif count > 0:                         # the reservation's own day (a request across midnight)
                cur.execute("INSERT INTO usage (day, kind, count) VALUES (?, ?, ?) "
                            "ON CONFLICT(day, kind) DO UPDATE SET count = count + excluded.count",
                            (r.day, r.kind, count))
            return self._used(cur, r.kind, date.today().isoformat())
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
        day, limit = r.day, self.limit(kind)

        def go(cur):
            now = self._clock()
            self._expire(cur, now)
            used = self._used(cur, kind, day)
            if used + self._held(cur, kind, day) + n > limit:
                raise BudgetExceeded(kind, limit, used)
            cur.execute("INSERT INTO reservations (id, day, kind, n, pid, created) VALUES (?, ?, ?, ?, ?, ?)",
                        (r.id, day, kind, n, os.getpid(), now))
        try:
            self._tx(go)
        except BudgetExceeded as e:
            logs.warn("budget", e.code, "budget limit reached", detail=str(e), action=e.action)
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
