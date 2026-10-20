"""Which processes are above this one (hook / channel processes prove which agent session started them).

A hook runs as a child of the Claude Code process, but often not directly: a Windows venv `python.exe` is a
launcher that starts the real interpreter as ITS child, and a shell may sit in between. So the plugin's processes
send their ancestor chain (a few levels, pids only) and JARVES checks that the session's own pid (from
`claude agents --json`, the documented source) is in it. Windows: a Toolhelp32 snapshot (one syscall, no psutil);
Linux: /proc/<pid>/stat.
"""
from __future__ import annotations

import os
import sys

MAX_DEPTH = 6


def _parents_windows() -> dict[int, int]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)            # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    out: dict[int, int] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def _parent_linux(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode("utf-8", "replace")
        return int(data.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def ancestors(pid: int | None = None, depth: int = MAX_DEPTH) -> list[int]:
    """[parent, grandparent, ...] of `pid` (default: this process), at most `depth` levels."""
    pid = pid or os.getpid()
    chain: list[int] = []
    try:
        if sys.platform == "win32":
            table = _parents_windows()
            cur = table.get(pid)
            while cur and cur not in chain and len(chain) < depth:
                chain.append(cur)
                cur = table.get(cur)
        else:
            cur = _parent_linux(pid) if sys.platform.startswith("linux") else os.getppid()
            while cur and cur > 1 and cur not in chain and len(chain) < depth:
                chain.append(cur)
                cur = _parent_linux(cur) if sys.platform.startswith("linux") else None
    except Exception:
        pass
    if not chain:
        try:
            chain = [os.getppid()]
        except OSError:
            chain = []
    return chain


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        return int(pid) in _parents_windows()
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
