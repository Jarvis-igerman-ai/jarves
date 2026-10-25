"""Stale-while-revalidate cache for slow dashboard API reads (#159 perf2).

A page that polls an endpoint costing 0.4-15 s (a probe, a subprocess, GitHub) must not make every poll pay for it:
a fresh answer is served from memory, a stale one is served at once while ONE background thread refreshes it, and the
very first call waits only `wait_s` (then gets `pending()` instead of hanging the request thread until the browser
gives up). Only successful (HTTP 200) results are kept; any state-changing route calls `invalidate()` so the next
read recomputes. Nothing here changes WHAT is returned, only when it is computed."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class SwrCache:
    def __init__(self, ttl_s: float, wait_s: float = 5.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s, self.wait_s, self._clock = ttl_s, wait_s, clock
        self._lock = threading.Lock()
        self._vals: dict[Any, tuple[float, Any]] = {}
        self._flight: dict[Any, threading.Event] = {}
        self._errors: dict[Any, BaseException] = {}
        self._last: dict[Any, Any] = {}          # newest result of any status, for the caller that waited
        self._gen = 0

    def invalidate(self) -> None:
        with self._lock:
            self._vals.clear()
            self._gen += 1                       # a refresh already running must not store its (older) result

    def get(self, key: Any, compute: Callable[[], Any], pending: Callable[[], Any] | None = None) -> Any:
        """compute() returns (status, payload); status 200 results are cached. Exceptions of a compute that
        finishes within wait_s reach the caller exactly as without the cache."""
        now = self._clock()
        with self._lock:
            hit = self._vals.get(key)
            if hit is not None and now - hit[0] <= self.ttl_s:
                return hit[1]
            ev = self._flight.get(key)
            starter = ev is None
            if starter:
                ev = self._flight[key] = threading.Event()
                self._errors.pop(key, None)
                self._last.pop(key, None)
                gen = self._gen
        if starter:
            threading.Thread(target=self._run, args=(key, compute, ev, gen), name="swr-refresh", daemon=True).start()
        if hit is not None:
            return hit[1]                        # stale, a refresh is running
        if not ev.wait(self.wait_s):
            return pending() if pending else (503, {"error": "still loading, retry in a few seconds", "pending": True})
        with self._lock:
            hit = self._vals.get(key)
            err = self._errors.get(key)
            last = self._last.get(key)
        if hit is not None:
            return hit[1]
        if err is not None:
            raise err
        return last if last is not None else (503, {"error": "still loading, retry in a few seconds", "pending": True})

    def _run(self, key: Any, compute: Callable[[], Any], ev: threading.Event, gen: int) -> None:
        try:
            res = compute()
            with self._lock:
                self._last[key] = res
                if gen == self._gen and isinstance(res, tuple) and res and res[0] == 200:
                    self._vals[key] = (self._clock(), res)
        except BaseException as e:               # noqa: BLE001 - handed to the waiting caller
            with self._lock:
                self._errors[key] = e
        finally:
            with self._lock:
                self._flight.pop(key, None)
            ev.set()
