"""Workflow manager mode (#715 slice 1): which scheduler owns this profile, native Claude or the JARVES Orchestrator.

Deterministic local state only: `<home>/manager.json` (the choice, default native) and `<home>/manager.lock` (one live
owner). It starts nothing, approves nothing, merges nothing and adds no second database. Switching the mode never
steals a lock held by a live process of the other mode.
"""
from __future__ import annotations

import json
import os
import time
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


def lock_owner(home: Path, alive=_alive) -> dict | None:
    """The live lock holder, or None (no lock, damaged lock or a holder process that is gone)."""
    held = _read(Path(home) / LOCK_NAME)
    if not held:
        return None
    try:
        return held if alive(int(held.get("pid"))) else None
    except (TypeError, ValueError):
        return None


def status(home: Path, alive=_alive) -> dict:
    owner = lock_owner(home, alive)
    return {"mode": get_mode(home), "owner": owner, "active_scheduler": owner["mode"] if owner else None,
            "note": "Only one scheduler owns the profile; no owner means nobody is scheduling."}


def set_mode(home: Path, mode: str, alive=_alive, now=time.time) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    owner = lock_owner(home, alive)
    if owner and owner.get("mode") != mode:
        raise ManagerConflict(f"the {owner.get('mode')} manager is still running; release it first", owner)
    Path(home).mkdir(parents=True, exist_ok=True)
    _write(Path(home) / STATE_NAME, {"mode": mode, "changed_at": now()})
    return status(home, alive)


def acquire(home: Path, mode: str, holder: str, pid: int | None = None, alive=_alive, now=time.time) -> dict:
    """Take the single scheduler lock for `mode`. Refused when the choice differs or a live holder exists
    (including the same mode: a restart never produces a second scheduler)."""
    if mode != get_mode(home):
        raise ManagerConflict(f"manager mode is {get_mode(home)}, not {mode}")
    pid = os.getpid() if pid is None else pid
    path = Path(home) / LOCK_NAME
    Path(home).mkdir(parents=True, exist_ok=True)
    record = {"mode": mode, "holder": str(holder)[:80], "pid": pid, "since": now()}
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = lock_owner(home, alive)
            if owner:
                raise ManagerConflict("another scheduler already owns this profile", owner)
            try:
                path.unlink()      # holder process is gone: recover the stale lock once
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        return record
    raise ManagerConflict("could not take the scheduler lock", lock_owner(home, alive))


def release(home: Path, holder: str) -> dict:
    path = Path(home) / LOCK_NAME
    held = _read(path)
    if held and held.get("holder") != str(holder)[:80]:
        raise ManagerConflict("the lock belongs to another holder", held)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return {"released": True}


def command(home: Path, args: list[str]) -> dict:
    """CLI: manager [status | set native|jarves | acquire HOLDER | release HOLDER]."""
    sub = args[0] if args else "status"
    if sub == "status":
        return status(home)
    if sub == "set" and len(args) == 2:
        return set_mode(home, args[1])
    if sub == "acquire" and len(args) == 2:
        return acquire(home, get_mode(home), args[1])
    if sub == "release" and len(args) == 2:
        return release(home, args[1])
    raise ValueError("usage: manager [status | set native|jarves | acquire HOLDER | release HOLDER]")
