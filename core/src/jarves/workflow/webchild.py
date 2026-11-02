"""Exactly one dashboard web server per workflow home and port (#789).

The web server records its OWN pid and process start time in `<home>/web.pid`
once it is bound. The supervisor's popen handle can be a venv launcher whose
real interpreter is a grandchild, so terminating that handle alone left
orphan servers on the port. Before starting a web child and when it leaves,
the supervisor stops the recorded server, by PID only and only when the
recorded start time still matches (a reused PID is never touched). Nothing
here matches processes by name or command line.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PID_FILE = "web.pid"
_START_TOLERANCE_S = 1.0


def _path(home: Path) -> Path:
    return Path(home) / PID_FILE


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _K32.OpenProcess.restype = wintypes.HANDLE
    _K32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _K32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _K32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    _K32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _K32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _K32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _K32.WaitForSingleObject.restype = wintypes.DWORD
    _QUERY, _TERMINATE, _SYNCHRONIZE, _STILL_ACTIVE = 0x1000, 0x0001, 0x00100000, 259

    def started_at(pid: int) -> float | None:
        """Process creation time (seconds since 1601) of a live process, or None."""
        handle = _K32.OpenProcess(_QUERY, False, int(pid))
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not _K32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _K32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                return None
            created = times[0]
            return ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 1e7
        finally:
            _K32.CloseHandle(handle)

    def terminate(pid: int, timeout: float = 10.0) -> bool:
        """Stop one exact PID and wait for it to exit; True when it is gone."""
        handle = _K32.OpenProcess(_TERMINATE | _SYNCHRONIZE | _QUERY, False, int(pid))
        if not handle:
            return started_at(pid) is None
        try:
            _K32.TerminateProcess(handle, 1)
            return _K32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            _K32.CloseHandle(handle)
else:
    def started_at(pid: int) -> float | None:
        """Start time in clock ticks after boot from /proc, or None (unknown platforms never match)."""
        try:
            stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="ascii", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
            return None if fields[0] == "Z" else float(fields[19])
        except (OSError, ValueError, IndexError):
            return None

    def terminate(pid: int, timeout: float = 10.0) -> bool:
        import signal
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if started_at(pid) is None:
                return True
            time.sleep(0.1)
        return started_at(pid) is None


def read(home: Path) -> dict:
    try:
        data = json.loads(_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and int(data.get("pid", 0)) > 0 else {}
    except (OSError, ValueError, TypeError):
        return {}


def record(home: Path, port: int, *, pid: int | None = None, started=started_at) -> None:
    """Called by the web server itself after its port is bound."""
    pid = os.getpid() if pid is None else int(pid)
    data = {"pid": pid, "port": int(port), "started": started(pid), "recorded_at": time.time()}
    tmp = _path(home).with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, _path(home))


def clear(home: Path, *, pid: int | None = None) -> None:
    """Remove the record only when it still names this process."""
    pid = os.getpid() if pid is None else int(pid)
    if read(home).get("pid") == pid:
        try:
            _path(home).unlink()
        except OSError:
            pass


def _matches(rec: dict, started) -> bool:
    now = started(int(rec["pid"]))
    then = rec.get("started")
    return now is not None and isinstance(then, (int, float)) and abs(now - then) <= _START_TOLERANCE_S


def listening_pid(home: Path, port: int, *, started=started_at) -> int:
    """The pid of the recorded live server on this port, or 0."""
    rec = read(home)
    return int(rec["pid"]) if rec and rec.get("port") == int(port) and _matches(rec, started) else 0


def stop_recorded(home: Path, port: int, *, started=None, kill=None) -> tuple[str, int]:
    """Stop the recorded server for this port. Returns (outcome, pid):
    none | other-port | self | stale (pid gone or reused: record dropped) | stopped | failed."""
    started, kill = started or started_at, kill or terminate
    rec = read(home)
    if not rec:
        return "none", 0
    pid = int(rec["pid"])
    if rec.get("port") != int(port):
        return "other-port", pid
    if pid == os.getpid():
        return "self", pid
    if not _matches(rec, started):
        try:
            _path(home).unlink()
        except OSError:
            pass
        return "stale", pid
    if not kill(pid):
        return "failed", pid
    try:
        _path(home).unlink()
    except OSError:
        pass
    return "stopped", pid


def reap(home: Path, port: int, record_event=None) -> tuple[str, int]:
    """Supervisor helper: stop a leftover server and log what happened (never raises)."""
    try:
        outcome, pid = stop_recorded(home, port)
    except Exception as exc:  # noqa: BLE001  cleanup must never stop the supervisor
        outcome, pid = f"error {type(exc).__name__}", 0
    if record_event and (outcome in {"stopped", "failed"} or outcome.startswith("error")):
        text = {"stopped": f"Stopped the previous dashboard server (pid {pid}) so exactly one serves port {port}",
                "failed": f"Could not stop the previous dashboard server (pid {pid}); port {port} may stay busy"}
        try:
            record_event("supervisor", text.get(outcome, f"Dashboard server cleanup failed: {outcome}"))
        except Exception:  # noqa: BLE001
            pass
    return outcome, pid
