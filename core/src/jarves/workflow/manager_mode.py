"""Workflow manager mode (#715 slice 1): which scheduler owns this profile, native Claude or the JARVES Orchestrator.

Deterministic local state only: `<home>/manager.json` (the choice, default native) and `<home>/manager.lock` (one live
owner). It starts nothing, approves nothing, merges nothing and adds no second database. Switching the mode never
steals a lock held by a live process of the other mode.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from pathlib import Path

MODES = ("native", "jarves")
STATE_NAME = "manager.json"
LOCK_NAME = "manager.lock"


class ManagerConflict(RuntimeError):
    def __init__(self, message: str, owner: dict | None = None):
        super().__init__(message)
        self.owner = owner or {}


def _alive(pid: int) -> bool:
    from ..budget import pid_alive
    return pid_alive(pid)


def _read(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_mode(home: Path) -> str:
    """The chosen mode; a missing or damaged file means native (fail closed to the default manager)."""
    mode = (_read(Path(home) / STATE_NAME) or {}).get("mode")
    return mode if mode in MODES else "native"


GRACE = 10.0          # seconds an empty/unreadable lock still counts as "being created"


def _fresh(path: Path, now=time.time) -> bool:
    try:
        return now() - path.stat().st_mtime < GRACE
    except OSError:
        return False


def lock_owner(home: Path, alive=_alive, now=time.time) -> dict | None:
    """The live lock holder, or None (no lock, a holder process that is gone, or a damaged lock past the grace).
    An empty/unreadable lock younger than GRACE is treated as held (being created): {"pending": True}."""
    path = Path(home) / LOCK_NAME
    held = _read(path)
    if not held:
        return {"pending": True, "mode": None} if path.exists() and _fresh(path, now) else None
    try:
        return held if alive(int(held.get("pid"))) else None
    except (TypeError, ValueError):
        return None


def _public(rec: dict | None) -> dict | None:
    return {k: v for k, v in rec.items() if k != "token"} if rec else rec


def status(home: Path, alive=_alive) -> dict:
    owner = lock_owner(home, alive)
    return {"mode": get_mode(home), "owner": _public(owner), "active_scheduler": owner.get("mode") if owner else None,
            "note": "Only one scheduler owns the profile; no owner means nobody is scheduling."}


GUARD_WAIT = 5.0      # seconds to wait for the profile guard before refusing (fail closed)


@contextlib.contextmanager
def _guarded(home: Path):
    """One interprocess guard (an OS file lock on manager.json.lock, freed by the OS if the process dies) serialises
    every mode switch, acquire, stale recovery and release, so each check-then-write is atomic for this profile."""
    from ..drive_mode import _file_lock
    try:
        with _file_lock(Path(home) / STATE_NAME, wait_s=GUARD_WAIT):
            yield
    except OSError as exc:                  # guard busy (TimeoutError), unwritable profile, file in use, no rename
        raise ManagerConflict(f"the manager state could not be changed safely: {exc.__class__.__name__}") from exc


def set_mode(home: Path, mode: str, alive=_alive, now=time.time) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    with _guarded(home):
        owner = lock_owner(home, alive, now)
        if owner and owner.get("mode") != mode:                 # a pending lock (mode None) also counts
            raise ManagerConflict(f"the {owner.get('mode')} manager is still running; release it first", _public(owner))
        _write(Path(home) / STATE_NAME, {"mode": mode, "changed_at": now()})
    return status(home, alive)


def acquire(home: Path, mode: str, holder: str, pid: int, alive=_alive, now=time.time) -> dict:
    """Take the single scheduler lock for `mode`. `pid` must be the long-lived scheduler process (not the CLI) and
    alive. Under the profile guard: refused when the choice differs or a live (or still pending) holder exists, so a
    restart never produces a second scheduler; a dead holder's lock is replaced. The record is written atomically.
    Returns the record including the secret `token` used to release. The token stops accidents; it is stored in plain
    JSON under the user's profile, so it is not a security boundary against other processes of the same user."""
    if not isinstance(pid, int) or pid <= 0 or not alive(pid):
        raise ManagerConflict("pid must be the live scheduler process that will hold the lock")
    with _guarded(home):
        chosen = get_mode(home)
        if mode != chosen:
            raise ManagerConflict(f"manager mode is {chosen}, not {mode}")
        owner = lock_owner(home, alive, now)
        if owner:
            raise ManagerConflict("another scheduler already owns this profile", _public(owner))
        record = {"mode": mode, "holder": str(holder)[:80], "pid": pid, "since": now(), "token": uuid.uuid4().hex}
        _write(Path(home) / LOCK_NAME, record)
        return record


def release(home: Path, holder: str, token: str = "", force: bool = False, now=time.time) -> dict:
    """Release needs the holder name and secret token. `force` (owner terminal only, enforced by the CLI) skips them.
    Under the profile guard, so a duplicate or late release can never delete a lock that replaced the one it checked.
    Without force an unreadable lock is removed only once it is past GRACE (never while it may be being created)."""
    path = Path(home) / LOCK_NAME
    holder = str(holder)[:80]
    with _guarded(home):
        held = _read(path)
        if not force and path.exists():
            mine = bool(token) and bool(held) and held.get("token") == token and held.get("holder") == holder
            if not mine and (held or _fresh(path, now)):
                raise ManagerConflict("the lock belongs to another holder (holder name and token must match)", _public(held))
        path.unlink(missing_ok=True)
    return {"released": True}


def command(home: Path, args: list[str], force: bool = False) -> dict:
    """CLI: manager [status | set native|jarves | acquire HOLDER PID | release HOLDER TOKEN | release HOLDER --force].
    The CLI wrapper allows `set` and a forced release only from the owner's terminal."""
    sub = args[0] if args else "status"
    if sub == "status":
        return status(home)
    if sub == "set" and len(args) == 2:
        return set_mode(home, args[1])
    if sub == "acquire" and len(args) == 3 and args[2].isdigit():
        if int(args[2]) != os.getppid():    # bind the lock to the scheduler that runs this CLI, not any live process
            raise ManagerConflict("PID must be the scheduler process that runs this command (its parent process)")
        return acquire(home, get_mode(home), args[1], int(args[2]))
    if sub == "release" and (len(args) == 3 or (len(args) == 2 and force)):
        return release(home, args[1], args[2] if len(args) == 3 else "", force=force)
    raise ValueError("usage: manager [status | set native|jarves | acquire HOLDER PID | release HOLDER TOKEN | release HOLDER --force]")
