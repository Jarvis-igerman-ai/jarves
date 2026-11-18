"""A public exclusive cross-process file lock for modules that merge a settings/state file (first user: reports_drive).
Same pattern as the private drive_mode._file_lock (issue #300); moving drive_mode onto this helper is a separate change."""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path


@contextlib.contextmanager
def file_lock(path: Path, wait_s: float = 5.0, busy: str = "file is locked by another process"):
    """An exclusive cross-process lock on `<path>.lock` (msvcrt on Windows, flock elsewhere). Fails closed: if it
    cannot be taken within `wait_s` seconds a TimeoutError(busy) is raised and nothing is written (issue #300)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path.with_name(path.name + ".lock"), "a+b")
    try:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(busy) from None
                time.sleep(0.02)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
