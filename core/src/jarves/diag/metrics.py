"""Compact time series in store.db: 1-minute roll-ups kept 7 days, hourly roll-ups kept 90 days.

Every row carries app_version and commit, so regression.py can compare versions. Writers buffer in memory and
flush whole minutes; nothing here blocks on the network or holds a lock while computing.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from . import db

MINUTE_KEEP_S = 7 * 86400
HOUR_KEEP_S = 90 * 86400
EVENT_KEEP_S = 90 * 86400


def minute(ts: float) -> int:
    return int(ts // 60 * 60)


def hour(ts: float) -> int:
    return int(ts // 3600 * 3600)


class MetricStore:
    """Buffered writer + reader of diag_metrics / diag_codes / diag_events."""

    def __init__(self, store, *, clock: Callable[[], float] = time.time, version: tuple[str, str] | None = None):
        self.store, self.clock = store, clock
        self.version, self.commit = version or db.version_info()
        self._lock = threading.Lock()
        self._m: dict[tuple[int, str], list[float]] = {}            # (minute, name) -> [sum, min, max, n]
        self._c: dict[tuple[int, str, str], list] = {}               # (minute, code, level) -> [n, component]
        self._ev: list[tuple] = []
        db.ensure(store)

    # ---- writing
    def add(self, name: str, value: float, ts: float | None = None) -> None:
        if value is None:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        if v != v:                                          # NaN
            return
        k = (minute(self.clock() if ts is None else ts), name)
        with self._lock:
            b = self._m.get(k)
            if b is None:
                self._m[k] = [v, v, v, 1]
            else:
                b[0] += v
                b[1] = min(b[1], v)
                b[2] = max(b[2], v)
                b[3] += 1

    def count(self, code: str, level: str, component: str, ts: float | None = None, n: int = 1) -> None:
        k = (minute(self.clock() if ts is None else ts), str(code)[:64], str(level)[:8])
        with self._lock:
            b = self._c.get(k)
            if b is None:
                self._c[k] = [n, str(component)[:32]]
            else:
                b[0] += n

    def event(self, kind: str, name: str, value: float | None = None, detail: str = "", ref: str = "",
              ts: float | None = None) -> None:
        with self._lock:
            self._ev.append((self.clock() if ts is None else ts, kind, str(name)[:80], value, str(detail)[:300],
                             str(ref)[:80], self.version, self.commit))

    def flush(self, *, all_: bool = False) -> int:
        """Write finished minutes (all buffered minutes with all_=True), roll up finished hours, prune."""
        now = self.clock()
        cur = minute(now)
        with self._lock:
            mk = [k for k in self._m if all_ or k[0] < cur]
            ck = [k for k in self._c if all_ or k[0] < cur]
            mrows = [("m", k[0], k[1], self._m[k][0] / self._m[k][3], self._m[k][1], self._m[k][2], self._m[k][3],
                      self.version, self.commit) for k in mk]
            crows = [("m", k[0], k[1], k[2], self._c[k][1], self._c[k][0], self.version, self.commit) for k in ck]
            for k in mk:
                del self._m[k]
            for k in ck:
                del self._c[k]
            ev, self._ev = self._ev, []
        db.many(self.store, "INSERT INTO diag_metrics (res, ts, name, value, vmin, vmax, n, app_version, git_commit) "
                            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(res, name, ts) DO UPDATE SET "
                            "value=(value*n + excluded.value*excluded.n)/(n+excluded.n), "
                            "vmin=MIN(vmin, excluded.vmin), vmax=MAX(vmax, excluded.vmax), n=n+excluded.n", mrows)
        db.many(self.store, "INSERT INTO diag_codes (res, ts, code, level, component, n, app_version, git_commit) "
                            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(res, ts, code, level) DO UPDATE SET n=n+excluded.n",
                crows)
        db.many(self.store, "INSERT INTO diag_events (ts, kind, name, value, detail, ref, app_version, git_commit) "
                            "VALUES (?,?,?,?,?,?,?,?)", ev)
        self.rollup(now)
        return len(mrows) + len(crows) + len(ev)

    def rollup(self, now: float) -> None:
        """Hourly rows for every finished hour that has minute rows; then retention."""
        done_before = hour(now)
        st = self.store
        with st._lock:
            last = st._db.execute("SELECT value FROM diag_metrics WHERE res='x' AND name='rolled_until'").fetchone()
            since = int(last[0]) if last else 0
            if done_before > since:
                st._db.execute(
                    "INSERT INTO diag_metrics (res, ts, name, value, vmin, vmax, n, app_version, git_commit) "
                    "SELECT 'h', (ts/3600)*3600 AS hh, name, SUM(value*n)/SUM(n), MIN(vmin), MAX(vmax), SUM(n), "
                    "MAX(app_version), MAX(git_commit) FROM diag_metrics WHERE res='m' AND ts >= ? AND ts < ? "
                    "GROUP BY hh, name ON CONFLICT(res, name, ts) DO UPDATE SET value=excluded.value, "
                    "vmin=excluded.vmin, vmax=excluded.vmax, n=excluded.n", (since, done_before))
                st._db.execute(
                    "INSERT INTO diag_codes (res, ts, code, level, component, n, app_version, git_commit) "
                    "SELECT 'h', (ts/3600)*3600 AS hh, code, level, MAX(component), SUM(n), MAX(app_version), "
                    "MAX(git_commit) FROM diag_codes WHERE res='m' AND ts >= ? AND ts < ? GROUP BY hh, code, level "
                    "ON CONFLICT(res, ts, code, level) DO UPDATE SET n=excluded.n", (since, done_before))
                st._db.execute("INSERT INTO diag_metrics (res, ts, name, value) VALUES ('x', 0, 'rolled_until', ?) "
                               "ON CONFLICT(res, name, ts) DO UPDATE SET value=excluded.value", (done_before,))
            st._db.execute("DELETE FROM diag_metrics WHERE res='m' AND ts < ?", (now - MINUTE_KEEP_S,))
            st._db.execute("DELETE FROM diag_metrics WHERE res='h' AND ts < ?", (now - HOUR_KEEP_S,))
            st._db.execute("DELETE FROM diag_codes WHERE res='m' AND ts < ?", (now - MINUTE_KEEP_S,))
            st._db.execute("DELETE FROM diag_codes WHERE res='h' AND ts < ?", (now - HOUR_KEEP_S,))
            st._db.execute("DELETE FROM diag_events WHERE ts < ?", (now - EVENT_KEEP_S,))
            st._db.commit()

    # ---- direct hourly writes (replay harness, imports)
    def put_hour(self, ts: float, name: str, value: float, *, n: int = 60, version: str | None = None,
                 commit: str | None = None) -> None:
        db.write(self.store, "INSERT INTO diag_metrics (res, ts, name, value, vmin, vmax, n, app_version, git_commit)"
                             " VALUES ('h',?,?,?,?,?,?,?,?) ON CONFLICT(res, name, ts) DO UPDATE SET "
                             "value=excluded.value, n=excluded.n",
                 (hour(ts), name, float(value), float(value), float(value), n, version or self.version,
                  commit if commit is not None else self.commit))


# ---------------------------------------------------------------------- reading
def series(store, name: str, since: float, until: float | None = None, res: str = "h") -> list[tuple[int, float]]:
    q = "SELECT ts, value FROM diag_metrics WHERE res=? AND name=? AND ts >= ?" + (" AND ts < ?" if until else "") + \
        " ORDER BY ts"
    args = (res, name, int(since)) + ((int(until),) if until else ())
    return [(r["ts"], r["value"]) for r in db.rows(store, q, args)]


def names(store, since: float, res: str = "h") -> list[str]:
    return [r["name"] for r in db.rows(store, "SELECT DISTINCT name FROM diag_metrics WHERE res=? AND ts >= ?",
                                       (res, int(since)))]


def code_counts(store, since: float, until: float | None = None, res: str = "h",
                levels: tuple[str, ...] = ("ERROR", "WARNING")) -> dict[str, dict[int, int]]:
    """code -> {hour ts -> count} (levels given)."""
    q = (f"SELECT ts, code, SUM(n) n FROM diag_codes WHERE res=? AND ts >= ? "
         f"{'AND ts < ? ' if until else ''}AND level IN ({','.join('?' * len(levels))}) GROUP BY ts, code")
    args = (res, int(since)) + ((int(until),) if until else ()) + tuple(levels)
    out: dict[str, dict[int, int]] = {}
    for r in db.rows(store, q, args):
        out.setdefault(r["code"], {})[r["ts"]] = int(r["n"])
    return out


def code_total(store, code: str, since: float, until: float | None = None, res: str = "h") -> int:
    q = "SELECT COALESCE(SUM(n),0) n FROM diag_codes WHERE res=? AND code=? AND ts >= ?" + (" AND ts < ?" if until else "")
    args = (res, code, int(since)) + ((int(until),) if until else ())
    return int(db.rows(store, q, args)[0]["n"])


def events(store, kind: str, since: float, until: float | None = None) -> list[dict]:
    q = "SELECT * FROM diag_events WHERE kind=? AND ts >= ?" + (" AND ts < ?" if until else "") + " ORDER BY ts"
    return db.rows(store, q, (kind, since) + ((until,) if until else ()))
