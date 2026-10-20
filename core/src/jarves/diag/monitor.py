"""Continuous health monitor (no tokens): event-driven plus a 60 s sampler, inside the Core.

Event-driven part (a log sink, the logging thread; O(1) work per record):
- per log code and level: counts per minute (error and warning rates per code, new codes, rate steps)
- turn latencies: reply wait (LIVE_JARVES_SAID wait_s), wake-to-first-audio (WAKE_WORD -> LIVE_FIRST_AUDIO),
  dead air (waits above history.dead_air_s), tool durations (TOOL_RESULT ms), tool failures per conversation
- false-action claims, wake false positives (VOICE_NO_SPEECH), budget hits (AI_BUDGET*)
Health listener: every component state change (ok -> degraded ...) is an event row (E/F bridges included when
they register their health component).
Sampler (every health.monitor_interval_s, default 60 s): process CPU %, RAM (working set / RSS), handles
(Windows) or open fds (Linux), threads, state folder size and disk free, event-loop lag, scheduler lateness,
and the monitor's own CPU time (the overhead budget: at most 0.1% CPU and 5 MB).

Only numbers and identifiers are read from records; message text and content fields are never looked at.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .. import logs
from . import db
from .metrics import MetricStore

FALSE_CLAIM = "LIVE_FALSE_ACTION_CLAIM"
WAKE_FALSE = "VOICE_NO_SPEECH"
BUDGET_CODES = ("AI_BUDGET", "AI_BUDGET_OVERRUN")
LIVE_OPEN = ("LIVE_STARTED", "REALTIME_STARTED")
LIVE_CLOSE = ("LIVE_STOPPED", "LIVE_CLOSED", "LIVE_ENDED", "REALTIME_STOPPED")


# ---------------------------------------------------------------------- process statistics (no psutil)
def _win_stats() -> dict:
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE          # 64-bit pseudo handle: never truncate to int
    k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    proc = k32.GetCurrentProcess()
    out: dict = {}
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    if psapi.GetProcessMemoryInfo(proc, ctypes.byref(pmc), pmc.cb):
        out["ram_mb"] = pmc.WorkingSetSize / 2**20
        out["private_mb"] = pmc.PrivateUsage / 2**20
    n = wintypes.DWORD()
    if k32.GetProcessHandleCount(proc, ctypes.byref(n)):
        out["handles"] = float(n.value)
    return out


def _linux_stats() -> dict:
    out: dict = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                out["ram_mb"] = int(line.split()[1]) / 1024
            elif line.startswith("Threads:"):
                out["threads"] = float(line.split()[1])
        out["handles"] = float(len(os.listdir("/proc/self/fd")))
    except OSError:
        pass
    return out


def process_stats() -> dict:
    try:
        d = _win_stats() if sys.platform == "win32" else _linux_stats()
    except Exception:
        d = {}
    d.setdefault("threads", float(threading.active_count()))
    return d


def dir_size_mb(path: Path, limit: int = 20000) -> float | None:
    """Size of the state folder (bounded walk: at most `limit` files)."""
    total, seen = 0, 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                seen += 1
                if seen > limit:
                    return round(total / 1e6, 2)
                try:
                    total += os.stat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    except OSError:
        return None
    return round(total / 1e6, 2)


def conv_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:10]


# ---------------------------------------------------------------------- the sink
class MonitorSink(logging.Handler):
    """Counts every record per code/level and extracts the numeric turn metrics. Runs on the logging thread."""

    def __init__(self, monitor: "Monitor") -> None:
        super().__init__(logging.INFO)
        self.m = monitor

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.m.on_record(getattr(record, "code", "-"), record.levelname, getattr(record, "component", "-"),
                             getattr(record, "fields", None) or {}, self.m.clock())
        except Exception:
            pass                                  # the monitor never breaks logging


class Monitor:
    def __init__(self, store, *, home: Path | None = None, cfg=None, clock: Callable[[], float] = time.time,
                 version: tuple[str, str] | None = None, dead_air_s: float | None = None) -> None:
        self.store, self.home, self.cfg, self.clock = store, Path(home) if home else None, cfg, clock
        self.metrics = MetricStore(store, clock=clock, version=version)
        self.dead_air_s = float(dead_air_s if dead_air_s is not None else
                                getattr(getattr(cfg, "history", None), "dead_air_s", 4.0) or 4.0)
        self._wake_at: float | None = None
        self._live: str | None = None
        self._live_n = 0
        self._last_states: dict[str, str] = {}
        self._cpu_last: tuple[float, float] | None = None
        self.own_cpu_s = 0.0                      # CPU time the monitor itself used (overhead budget)
        self.samples = 0
        self._sink: MonitorSink | None = None
        self._task: asyncio.Task | None = None
        self.last_sample: dict = {}

    # ---- events
    def conversation(self) -> str:
        return self._live or f"cid:{logs.get_cid()}"

    def on_record(self, code: str, level: str, component: str, f: dict, ts: float) -> None:
        t0 = time.thread_time()
        m = self.metrics
        m.count(code, level, component, ts)
        if code in LIVE_OPEN:
            self._live_n += 1
            self._live = f"live:{logs._SESSION}:{self._live_n}"
        elif code in LIVE_CLOSE:
            self._live = None
        elif code == "WAKE_WORD":
            self._wake_at = ts
        elif code == "LIVE_FIRST_AUDIO" and self._wake_at is not None:
            m.add("wake_to_first_audio_s", ts - self._wake_at, ts)
            m.event("turn", "wake_to_first_audio", ts - self._wake_at, ts=ts)
            self._wake_at = None
        elif code == "LIVE_JARVES_SAID":
            w = f.get("wait_s")
            if isinstance(w, (int, float)) and not isinstance(w, bool):
                m.add("turn_wait_s", w, ts)
                m.event("turn", "reply_wait", float(w), ref=conv_key(self.conversation()), ts=ts)
                if w > self.dead_air_s:
                    m.add("dead_air_s", w, ts)
        elif code == "TOOL_RESULT":
            tool = str(f.get("tool") or "?")[:60]
            ms = f.get("ms")
            if isinstance(ms, (int, float)) and not isinstance(ms, bool):
                m.add("tool_ms", ms, ts)
            if f.get("ok") is False:
                m.event("tool_fail", tool, 1.0, ref=conv_key(self.conversation()), ts=ts)
        elif code in BUDGET_CODES:
            m.add("budget_hits", 1, ts)
        self.own_cpu_s += time.thread_time() - t0

    def on_health(self, components: dict) -> None:
        for name, c in components.items():
            st = getattr(c, "status", None) or (c.get("status") if isinstance(c, dict) else None)
            if st is None:
                continue
            if self._last_states.get(name) != st:
                prev = self._last_states.get(name, "")
                self._last_states[name] = st
                self.metrics.event("component", name, None, detail=f"{prev}->{st}")

    # ---- sampler
    def sample(self, loop_lag_s: float | None = None) -> dict:
        t0 = time.thread_time()
        now = self.clock()
        d = process_stats()
        pt = time.process_time()
        if self._cpu_last is not None:
            dw, dc = now - self._cpu_last[0], pt - self._cpu_last[1]
            if dw > 0:
                d["cpu_pct"] = max(0.0, 100.0 * dc / dw / max(1, os.cpu_count() or 1))
        self._cpu_last = (now, pt)
        if self.home is not None:
            size = dir_size_mb(self.home)
            if size is not None:
                d["disk_state_mb"] = size
            try:
                d["disk_free_gb"] = shutil.disk_usage(self.home).free / 1e9
            except OSError:
                pass
        if loop_lag_s is not None:
            d["loop_lag_ms"] = loop_lag_s * 1000
        late = self._scheduler_lateness()
        if late is not None:
            d["scheduler_late_s"] = late
        for k, v in d.items():
            self.metrics.add(k, v, now)
        self.samples += 1
        self.own_cpu_s += time.thread_time() - t0
        self.metrics.add("monitor_cpu_s", self.own_cpu_s, now)
        self.last_sample = d
        return d

    def _scheduler_lateness(self) -> float | None:
        """Seconds the most recent job run started after its slot (memory.db job_runs), if any ran lately."""
        if self.home is None:
            return None
        p = self.home / "memory.db"
        if not p.is_file():
            return None
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=1)
            try:
                r = con.execute("SELECT scheduled_for, started FROM job_runs WHERE started IS NOT NULL "
                                "ORDER BY started DESC LIMIT 1").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return None
        if not r or r[0] is None or r[1] is None or self.clock() - float(r[1]) > 3600:
            return None
        return max(0.0, float(r[1]) - float(r[0]))

    # ---- lifecycle inside the Core
    def attach(self, health=None) -> None:
        self._sink = MonitorSink(self)
        logs.add_sink(self._sink)
        if health is not None:
            health.on_change(lambda: self.on_health(dict(health.components)))
            self.on_health(dict(health.components))

    async def run(self, interval_s: float = 60.0) -> None:
        loop = asyncio.get_running_loop()
        lag = None
        while True:
            try:
                await asyncio.to_thread(self.sample, lag)
                await asyncio.to_thread(self.metrics.flush)
            except Exception:
                logs.warn("health", "HEALTH_SAMPLE_FAILED", "health monitor sample failed")
            t = loop.time()
            await asyncio.sleep(interval_s)
            lag = max(0.0, loop.time() - t - interval_s)

    def start(self, interval_s: float = 60.0) -> asyncio.Task:
        self._task = asyncio.get_running_loop().create_task(self.run(interval_s), name="health-monitor")
        return self._task

    async def stop(self) -> None:
        if self._sink is not None:
            logs.remove_sink(self._sink)
            self._sink = None
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        try:
            self.metrics.flush(all_=True)
        except Exception:
            pass

    def overhead(self, wall_s: float) -> dict:
        """The monitor's own CPU share over `wall_s` seconds and its buffer size (budget: 0.1% CPU, 5 MB)."""
        cpu = 100.0 * self.own_cpu_s / max(wall_s, 1e-9) / max(1, os.cpu_count() or 1)
        buf = (len(self.metrics._m) + len(self.metrics._c) + len(self.metrics._ev)) * 200 / 2**20
        return {"cpu_pct": round(cpu, 5), "buffer_mb": round(buf, 3), "samples": self.samples,
                "own_cpu_s": round(self.own_cpu_s, 4), "ok": cpu <= 0.1 and buf <= 5}


def record_config(store, snapshot: dict, ts: float) -> bool:
    """Keep a snapshot of the NON-SECRET configuration when it changed (config drift). Returns True if new."""
    digest = hashlib.sha256(db.dumps(snapshot).encode()).hexdigest()[:16]
    last = db.rows(store, "SELECT digest FROM diag_config ORDER BY ts DESC LIMIT 1")
    if last and last[0]["digest"] == digest:
        return False
    db.write(store, "INSERT OR REPLACE INTO diag_config (ts, digest, snapshot) VALUES (?,?,?)",
             (ts, digest, db.dumps(snapshot)))
    return True


PERSONAL_KEYS = ("name", "phrase", "city", "address", "phone", "email", "user", "location", "lat", "lon", "path",
                 "dir", "url", "device", "title", "contact", "chat", "id")
IDENT = re.compile(r"^[a-z0-9_.\-]{1,32}$")       # enum-like values only (live, priority, gpt-6-luna, hey_jarvis)


def config_snapshot(cfg) -> dict:
    """Flattened config with secret / personal keys reduced to presence only (never their values)."""
    out: dict = {}
    try:
        data = cfg.model_dump()
    except Exception:
        return out

    def walk(prefix: str, v) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f"{prefix}.{k}" if prefix else str(k), x)
            return
        last = prefix.rsplit(".", 1)[-1]
        if logs.is_sensitive(last, True) or logs.is_credential(last):
            out[prefix] = "<set>" if v else "<empty>"
        elif isinstance(v, (bool, int, float)) or v is None:
            out[prefix] = v
        elif any(p in last.lower() for p in PERSONAL_KEYS):
            out[prefix] = "<set>" if v else "<empty>"
        else:
            s = str(v)
            out[prefix] = s if IDENT.match(s) else f"<{len(s)} chars>"

    walk("", data)
    return out
