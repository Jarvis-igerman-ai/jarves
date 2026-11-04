"""Workflow manager mode (#715 slice 1): which scheduler owns this profile, native Claude or the JARVES Orchestrator.

Deterministic local state only: `<home>/manager.json` (the choice, default native) and `<home>/manager.lock` (one live
owner). It starts nothing, approves nothing, merges nothing and adds no second database. Switching the mode never
steals a lock held by a live process of the other mode.
"""
from __future__ import annotations

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
GUARD_STALE = 30.0    # a recovery guard older than this is a crashed recoverer


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


def set_mode(home: Path, mode: str, alive=_alive, now=time.time) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    owner = lock_owner(home, alive)
    if owner and owner.get("mode") != mode:
        raise ManagerConflict(f"the {owner.get('mode')} manager is still running; release it first", _public(owner))
    Path(home).mkdir(parents=True, exist_ok=True)
    _write(Path(home) / STATE_NAME, {"mode": mode, "changed_at": now()})
    return status(home, alive)


def acquire(home: Path, mode: str, holder: str, pid: int, alive=_alive, now=time.time) -> dict:
    """Take the single scheduler lock for `mode`. `pid` must be the long-lived scheduler process (not the CLI) and
    alive. The lock is created atomically (temp file + os.link into an absent path, so it is never visible empty).
    Refused when the choice differs or a live holder exists (a restart never produces a second scheduler).
    Returns the record including the secret `token` that only the holder can use to release."""
    if mode != get_mode(home):
        raise ManagerConflict(f"manager mode is {get_mode(home)}, not {mode}")
    if not isinstance(pid, int) or pid <= 0 or not alive(pid):
        raise ManagerConflict("pid must be the live scheduler process that will hold the lock")
    path = Path(home) / LOCK_NAME
    Path(home).mkdir(parents=True, exist_ok=True)
    record = {"mode": mode, "holder": str(holder)[:80], "pid": pid, "since": now(), "token": uuid.uuid4().hex}
    tmp = path.with_name(f"{LOCK_NAME}.{uuid.uuid4().hex}.new")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        for _ in range(2):
            try:
                os.link(tmp, path)          # atomic: fails if any lock (even one being recovered) exists
                return record
            except FileExistsError:
                owner = lock_owner(home, alive, now)
                if owner:
                    raise ManagerConflict("another scheduler already owns this profile", _public(owner))
                _recover_stale(path, alive, now)
        raise ManagerConflict("could not take the scheduler lock", _public(lock_owner(home, alive, now)))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _recover_stale(path: Path, alive, now) -> None:
    """Remove a stale lock without ever deleting another caller's fresh one: serialise recoverers with a guard file,
    move the stale file to a unique name (atomic), and put it back if it is not the file we judged stale."""
    guard = path.with_name(path.name + ".recover")
    try:
        os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        try:
            if now() - guard.stat().st_mtime > GUARD_STALE:
                guard.unlink()
        except OSError:
            pass
        return
    try:
        judged = _read(path)
        if lock_owner(path.parent, alive, now):
            return
        grave = path.with_name(f"{LOCK_NAME}.{uuid.uuid4().hex}.stale")
        try:
            os.replace(path, grave)
        except FileNotFoundError:
            return
        if _read(grave) != judged:          # someone replaced it between judging and moving: restore it
            try:
                os.link(grave, path)
            except OSError:
                pass
        try:
            grave.unlink()
        except OSError:
            pass
    finally:
        try:
            guard.unlink()
        except OSError:
            pass


def release(home: Path, holder: str, token: str = "", force: bool = False) -> dict:
    """Release needs the holder's secret token. `force` (owner terminal only, enforced by the CLI) skips the token."""
    path = Path(home) / LOCK_NAME
    held = _read(path)
    if held and not force:
        if not token or held.get("token") != token or held.get("holder") != str(holder)[:80]:
            raise ManagerConflict("the lock belongs to another holder (holder name and token must match)", _public(held))
    try:
        path.unlink()
    except FileNotFoundError:
        pass
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
        return acquire(home, get_mode(home), args[1], int(args[2]))
    if sub == "release" and (len(args) == 3 or (len(args) == 2 and force)):
        return release(home, args[1], args[2] if len(args) == 3 else "", force=force)
    raise ValueError("usage: manager [status | set native|jarves | acquire HOLDER PID | release HOLDER TOKEN | release HOLDER --force]")
