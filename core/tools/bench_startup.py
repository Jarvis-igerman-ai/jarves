"""SW-V3 P0.3 startup / idle benchmark: silent, headless, offline, no microphone, nothing billable.

    core/.venv/Scripts/python.exe core/tools/bench_startup.py [--runs 3] [--idle-s 60] [--scenario core|cloud]

Starts the REAL `jarves run --background` Core in a throw-away JARVES_HOME (own port, no secrets, wake word off so no
microphone opens), waits for CORE_READY in its log, samples CPU / RAM / threads / disk I/O while it idles, then stops
it with `jarves stop`. Scenario "cloud" sets a dummy OPENAI_API_KEY so the live-voice code path (prewarm of the cloud
voice libraries) runs exactly as on an installed PC, but no request is ever sent (no conversation is started).
Prints one JSON line per run plus a median summary. Windows (ctypes) and Linux (/proc) - no extra packages.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
SRC = CORE / "src"

CONFIGS = {
    # Core only: no audio stack at all (the always-on skeleton: scheduler, health loop, link, tray, dashboard skill)
    "core": '[voice]\nmode = "off"\nwake_enabled = false\n',
    # Installed default path for an API-key user, minus the microphone: live voice selected, prewarm runs
    "cloud": '[ai]\nconnection = "api_key"\n[voice]\nmode = "live"\nwake_enabled = false\ngreeting = false\n',
    # The owner's installed choice: ChatGPT plan for typed answers + API key for GPT-Live (not signed in here)
    "combined": '[ai]\nconnection = "combined"\n[voice]\nmode = "live"\nwake_enabled = false\ngreeting = false\n',
}


# ------------------------------------------------------------------ process sampling (no psutil dependency)
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _k32, _psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi")

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes",
                                                         "OtherBytes")]

    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IO)]

    def sample(pid: int) -> dict | None:
        h = _k32.OpenProcess(0x1000 | 0x0010, False, pid)      # QUERY_LIMITED_INFORMATION | VM_READ
        if not h:
            return None
        try:
            m = _PMC(); m.cb = ctypes.sizeof(m)
            _psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb)
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u))
            ft = lambda f: ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7   # noqa: E731
            io = _IO()
            _k32.GetProcessIoCounters(h, ctypes.byref(io))
            return {"rss_mb": m.WorkingSetSize / 2**20, "peak_mb": m.PeakWorkingSetSize / 2**20,
                    "private_mb": m.PagefileUsage / 2**20, "cpu_s": ft(k) + ft(u),
                    "read_mb": io.ReadBytes / 2**20, "write_mb": io.WriteBytes / 2**20,
                    "threads": _threads(pid)}
        finally:
            _k32.CloseHandle(h)

    class _PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    def _procs() -> list[tuple[int, int, int]]:
        snap = _k32.CreateToolhelp32Snapshot(0x2, 0)
        out, pe = [], _PE()
        pe.dwSize = ctypes.sizeof(pe)
        ok = _k32.Process32First(snap, ctypes.byref(pe))
        while ok:
            out.append((pe.th32ProcessID, pe.th32ParentProcessID, pe.cntThreads))
            ok = _k32.Process32Next(snap, ctypes.byref(pe))
        _k32.CloseHandle(snap)
        return out

    def _threads(pid: int) -> int:
        return next((t for p, _, t in _procs() if p == pid), 0)

    def children(pid: int) -> int:
        return sum(1 for _, pp, _ in _procs() if pp == pid)

    def target(pid: int) -> int:
        """A uv/venv python.exe on Windows is a launcher that runs the real interpreter as its child: follow it."""
        for _ in range(3):
            kids = [p for p, pp, _ in _procs() if pp == pid]
            if len(kids) != 1:
                break
            pid = kids[0]
        return pid
else:
    def target(pid: int) -> int:
        return pid

    def sample(pid: int) -> dict | None:
        try:
            st = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            status = dict(l.split(":", 1) for l in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in l)
            io = dict(l.split(": ") for l in Path(f"/proc/{pid}/io").read_text().splitlines())
        except OSError:
            return None
        tck = os.sysconf("SC_CLK_TCK")
        kb = lambda k: int(status.get(k, "0 kB").split()[0]) / 1024   # noqa: E731
        return {"rss_mb": kb("VmRSS"), "peak_mb": kb("VmHWM"), "private_mb": kb("RssAnon"),
                "cpu_s": (int(st[11]) + int(st[12])) / tck, "read_mb": int(io["read_bytes"]) / 2**20,
                "write_mb": int(io["write_bytes"]) / 2**20, "threads": int(status["Threads"])}

    def children(pid: int) -> int:
        try:
            return len(Path(f"/proc/{pid}/task/{pid}/children").read_text().split())
        except OSError:
            return 0


# ------------------------------------------------------------------ one run
def _env(home: Path, scenario: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY")}
    env.update(JARVES_HOME=str(home), PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    if scenario in ("cloud", "combined"):
        env["OPENAI_API_KEY"] = "sk-bench-dummy-never-sent"
    return env


def one_run(scenario: str, idle_s: float, port: int) -> dict:
    home = Path(tempfile.mkdtemp(prefix="jarves-bench-"))
    (home / "setup_done").write_text("bench", encoding="utf-8")
    (home / "config.toml").write_text(f"[link]\nport = {port}\n" + CONFIGS[scenario], encoding="utf-8")
    env = _env(home, scenario)
    log = home / "logs" / "jarves.log"
    t0 = time.perf_counter()
    p = subprocess.Popen([sys.executable, "-m", "jarves.cli", "run", "--background"], env=env, cwd=str(CORE),
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    ready_s = core_ms = None
    peak_start = 0.0
    pid = p.pid
    while time.perf_counter() - t0 < 120 and p.poll() is None:
        if pid == p.pid:
            pid = target(p.pid)
        s = sample(pid)
        if s:
            peak_start = max(peak_start, s["rss_mb"])
        try:
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                if '"CORE_READY"' in line:
                    ready_s, core_ms = time.perf_counter() - t0, json.loads(line).get("ms")
                    break
        except OSError:
            pass
        if ready_s:
            break
        time.sleep(0.05)
    if ready_s is None:
        err = p.stderr.read().decode("utf-8", "replace")[-800:] if p.poll() is not None else "timeout"
        p.kill()
        raise SystemExit(f"Core did not become ready: {err}")
    at_ready = sample(pid)
    # settle: background prewarm / first scheduler pass finish; then measure the steady idle window
    settle = 15.0
    time.sleep(settle)
    a = sample(pid)
    rss_series, t_idle = [], time.perf_counter()
    while time.perf_counter() - t_idle < idle_s:
        time.sleep(1.0)
        s = sample(pid)
        if s:
            rss_series.append(s["rss_mb"])
    b = sample(pid)
    kids = children(pid)
    wall = time.perf_counter() - t_idle
    ts = time.perf_counter()
    subprocess.run([sys.executable, "-m", "jarves.cli", "stop"], env=env, cwd=str(CORE), capture_output=True, timeout=60)
    try:
        p.wait(30)
    except subprocess.TimeoutExpired:
        p.kill()
    stop_s = time.perf_counter() - ts
    prewarm = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"PREWARM_DONE"' in line:
            prewarm = json.loads(line).get("ms")
    return {"scenario": scenario, "spawn_to_ready_s": round(ready_s, 2), "core_start_ms": core_ms,
            "prewarm_ms": prewarm, "rss_at_ready_mb": round(at_ready["rss_mb"], 1),
            "peak_rss_mb": round(max(b["peak_mb"], peak_start), 1), "steady_rss_mb": round(statistics.median(rss_series), 1),
            "rss_growth_mb": round(rss_series[-1] - rss_series[0], 1), "private_mb": round(b["private_mb"], 1),
            "idle_cpu_pct": round(100 * (b["cpu_s"] - a["cpu_s"]) / wall, 2),
            "startup_cpu_s": round(a["cpu_s"], 2),
            "idle_disk_write_kb_per_min": round((b["write_mb"] - a["write_mb"]) * 1024 * 60 / wall, 1),
            "idle_disk_read_kb_per_min": round((b["read_mb"] - a["read_mb"]) * 1024 * 60 / wall, 1),
            "threads": b["threads"], "child_processes": kids, "stop_s": round(stop_s, 2), "exit": p.returncode}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--idle-s", type=float, default=60)
    ap.add_argument("--scenario", choices=sorted(CONFIGS), default="core")
    ap.add_argument("--port", type=int, default=18765)
    a = ap.parse_args()
    rows = []
    for i in range(a.runs):
        r = one_run(a.scenario, a.idle_s, a.port)
        rows.append(r)
        print(json.dumps(r), flush=True)
    med = {k: statistics.median(r[k] for r in rows) for k, v in rows[0].items()
           if isinstance(v, (int, float)) and all(r[k] is not None for r in rows)}
    print("MEDIAN " + json.dumps({"scenario": a.scenario, "runs": a.runs, **med}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
