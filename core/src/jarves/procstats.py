"""Process sampling without psutil (CPU seconds, RSS, private commit, I/O bytes, threads, children) for any pid.
Windows (ctypes) and Linux (/proc). Shared by tools/bench_startup.py and the speed test (jarves.speedtest), so the
gate, the bench and `jarvis speedtest` measure with the same code."""
from __future__ import annotations

import os
import sys
from pathlib import Path

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
