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


def _windows_sockets(port: int) -> list[tuple[int, bool]] | None:
    """(pid, listening) for every TCP socket whose LOCAL port is this port (IPv4 and IPv6), via GetExtendedTcpTable."""
    import ctypes
    from ctypes import wintypes
    iphlp = ctypes.WinDLL("iphlpapi")
    found: list[tuple[int, bool]] = []
    # (address family, row size in DWORDs, local port index, remote address+port indexes, owning pid index) for
    # MIB_TCP(6)ROW_OWNER_PID. A socket without a peer is the listening endpoint: dwState is not used because this
    # Windows build reported 0 instead of MIB_TCP_STATE_LISTEN (2) for live listeners (checked 2 Nov 2026).
    for family, row_dwords, port_at, peer_at, pid_at in ((2, 6, 2, (3, 4), 5), (23, 14, 5, (6, 7, 8, 9, 11), 13)):
        size, rc = wintypes.DWORD(0), 122
        iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, family, 5, 0)  # 5 = TCP_TABLE_OWNER_PID_ALL
        for _ in range(3):
            buf = (ctypes.c_ubyte * max(size.value, 4))()
            rc = iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, 5, 0)
            if rc != 122:  # ERROR_INSUFFICIENT_BUFFER: the table grew, retry with the new size
                break
        if rc != 0:
            return None
        words = (wintypes.DWORD * (len(buf) // 4)).from_buffer(buf)
        for i in range(int(words[0])):
            row = 1 + i * row_dwords
            raw = int(words[row + port_at]) & 0xFFFF
            # The port is stored in network byte order; pid 0 rows are kernel leftovers (TIME_WAIT, closed), no process.
            if ((raw & 0xFF) << 8 | raw >> 8) == int(port) and int(words[row + pid_at]):
                no_peer = not any(int(words[row + k]) for k in peer_at)
                found.append((int(words[row + pid_at]), no_peer))
    return found


def _linux_sockets(port: int) -> list[tuple[int, bool]] | None:
    """(pid, listening) from /proc; a socket owned by another user's process reports pid 0."""
    inodes: dict[str, bool] = {}
    try:
        for name in ("tcp", "tcp6"):
            path = Path("/proc/net") / name
            if not path.exists():
                continue
            for line in path.read_text(encoding="ascii", errors="replace").splitlines()[1:]:
                f = line.split()
                if len(f) > 9 and int(f[1].rsplit(":", 1)[1], 16) == int(port) and f[9] != "0":
                    inodes[f[9]] = f[3] == "0A"
    except (OSError, ValueError):
        return None
    found: list[tuple[int, bool]] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                target = os.readlink(fd)
                inode = target[8:-1] if target.startswith("socket:[") else ""
                if inode in inodes:
                    found.append((int(proc.name), inodes.pop(inode)))
        except OSError:
            continue
    return found + [(0, listening) for listening in inodes.values()]


def sockets(port: int) -> list[tuple[int, bool]] | None:
    """(pid, listening) per TCP socket bound locally to this port; None when the OS table is unreadable."""
    try:
        if sys.platform == "win32":
            return _windows_sockets(port)
        if sys.platform.startswith("linux"):
            return _linux_sockets(port)
    except (OSError, ValueError, AttributeError):
        return None
    return None


def census(home: Path, port: int, *, table=None, started=None) -> dict:
    """Read-only 'one dashboard server' check (#789) for factory-check and the CLI.

    Reads the OS socket table, not only the pid record: servers started by code
    older than the record never wrote one, so the supervisor cannot reap them.
    A process that holds accepted connections on the port without being the
    listener is a stale server still answering an open tab with old code.
    ok is True only when exactly one process listens, it is the recorded,
    still-matching dashboard server, and no other process serves the port.
    """
    table, started = table or sockets, started or started_at
    rows = table(port)
    recorded = listening_pid(home, port, started=started)
    base = {"port": int(port), "recorded_pid": recorded}
    if rows is None:
        return {**base, "ok": False, "state": "unknown", "listeners": [], "stale": [],
                "detail": "the port table could not be read on this system"}
    listening = sorted({pid for pid, listen in rows if listen})
    stale = sorted({pid for pid, listen in rows if not listen and pid} - set(listening))
    if len(listening) > 1:
        state, detail = "several", f"{len(listening)} processes listen on port {port}: stop the extra ones by pid"
    elif stale:
        state, detail = "stale", (f"pid {', '.join(map(str, stale))} still serves port {port} without listening "
                                  "(an older dashboard server): stop it by pid, then reload the tab")
    elif not listening:
        state, detail = "none", f"no dashboard server listens on port {port}"
    elif listening[0] != recorded:
        state, detail = "foreign", (f"pid {listening[0]} listens on port {port} but is not the recorded dashboard server"
                                    + (f" (recorded pid {recorded})" if recorded else " (no live record)"))
    else:
        state, detail = "one", f"one dashboard server (pid {recorded}, port {port})"
    return {**base, "ok": state == "one", "state": state, "listeners": listening, "stale": stale, "detail": detail}


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
