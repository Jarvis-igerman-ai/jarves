"""One Core per user: an OS file lock on <state_dir>/core.lock (released by the OS even when the process crashes).

The state dir is per user (%LOCALAPPDATA%\\jarves, ~/.local/state/jarves), so two Windows users each get their own
Core while a second start by the same user (autostart at a new login, `jarvis start` twice, a double click) is
refused before it opens the microphone or a voice session. The lock file also holds the owner's pid for `status`.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

LOCK_FILE = "core.lock"


class AlreadyRunning(RuntimeError):
    def __init__(self, holder: dict | None) -> None:
        self.holder = holder or {}
        pid = self.holder.get("pid")
        super().__init__(f"JARVES is already running for this user (pid {pid})" if pid else
                         "JARVES is already running for this user")


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)      # byte 0; the pid text lives after it
            return True
        except OSError:
            return False
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def read_holder(home: Path) -> dict | None:
    """What the lock file says about its owner (may be stale when no Core runs: use `held()` for truth)."""
    try:
        with open(home / LOCK_FILE, "rb") as f:
            f.seek(1)                         # byte 0 is locked by the owner (mandatory on Windows): skip it
            raw = f.read(4096).decode("utf-8", "replace").strip()
        return json.loads(raw) if raw else None
    except (OSError, ValueError):
        return None


class InstanceLock:
    """acquire() -> True (we are the Core) or raises AlreadyRunning. Re-entrant per process is NOT needed."""

    def __init__(self, home: Path) -> None:
        self.path = home / LOCK_FILE
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, mode: str = "") -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        for attempt in range(5):             # a `held()` probe holds the lock for microseconds: retry briefly
            if _try_lock(fd):
                break
            if attempt == 4:
                os.close(fd)
                raise AlreadyRunning(read_holder(self.path.parent))
            time.sleep(0.05)
        info = json.dumps({"pid": os.getpid(), "started": time.time(), "mode": mode}).encode("utf-8")
        # byte 0 is the locked byte (Windows locks are mandatory: never write it); the owner info follows it
        os.lseek(fd, 1, os.SEEK_SET)
        os.write(fd, info)
        os.ftruncate(fd, 1 + len(info))
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        _unlock(fd)
        os.close(fd)


def held(home: Path) -> bool:
    """True when some process holds the Core lock right now (probe: lock + immediately unlock)."""
    path = home / LOCK_FILE
    if not path.exists():
        return False
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        if _try_lock(fd):
            _unlock(fd)
            return False
        return True
    finally:
        os.close(fd)
