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
    def other_owner():
        owner = lock_owner(home, alive)
        return owner if owner and owner.get("mode") != mode else None      # a pending lock (mode None) also counts
    owner, previous = other_owner(), get_mode(home)
    if owner:
        raise ManagerConflict(f"the {owner.get('mode')} manager is still running; release it first", _public(owner))
    Path(home).mkdir(parents=True, exist_ok=True)
    _write(Path(home) / STATE_NAME, {"mode": mode, "changed_at": now()})
    owner = other_owner()               # re-check after writing: acquire re-checks the mode after linking, so one side backs out
    if owner:
        _write(Path(home) / STATE_NAME, {"mode": previous, "changed_at": now()})
        raise ManagerConflict(f"the {owner.get('mode')} manager started during the switch; mode kept", _public(owner))
    return status(home, alive)


def acquire(home: Path, mode: str, holder: str, pid: int, alive=_alive, now=time.time) -> dict:
    """Take the single scheduler lock for `mode`. `pid` must be the long-lived scheduler process (not the CLI) and
    alive. The lock is created atomically (temp file + os.link into an absent path, so it is never visible empty).
    Refused when the choice differs or a live holder exists (a restart never produces a second scheduler).
    Returns the record including the secret `token` used to release. The token stops accidents; it is stored in plain
    JSON under the user's profile, so it is not a security boundary against other processes of the same user."""
    chosen = get_mode(home)
    if mode != chosen:
        raise ManagerConflict(f"manager mode is {chosen}, not {mode}")
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
            except FileExistsError:
                owner = lock_owner(home, alive, now)
                if owner:
                    raise ManagerConflict("another scheduler already owns this profile", _public(owner))
                _recover_stale(path, alive, now)
                continue
            switched = get_mode(home)
            if switched != mode:            # the owner switched meanwhile (set_mode re-checks the lock after writing)
                _remove_if(path, lambda rec: (rec or {}).get("token") == record["token"])
                raise ManagerConflict(f"manager mode changed to {switched} while taking the lock")
            return record
        raise ManagerConflict("could not take the scheduler lock", _public(lock_owner(home, alive, now)))
    except OSError as exc:                  # e.g. no hard links (FAT, network share) or a file in use on Windows
        raise ManagerConflict(f"the scheduler lock file could not be changed: {exc.__class__.__name__}") from exc
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
        _remove_if(path, lambda rec: rec == judged)   # replaced between judging and moving: restored, not deleted
    finally:
        try:
            guard.unlink()
        except OSError:
            pass


def _remove_if(path: Path, check) -> bool:
    """Atomically take the lock aside, then delete it only if `check(record)` still holds; otherwise put it back.
    A lock replaced after the caller validated it is never deleted. False when it was absent or not ours."""
    grave = path.with_name(f"{LOCK_NAME}.{uuid.uuid4().hex}.stale")
    try:
        os.replace(path, grave)
    except FileNotFoundError:
        return False
    try:
        if check(_read(grave)):
            return True
        try:
            os.link(grave, path)            # not ours: restore (fails only if a newer lock already took its place)
        except FileExistsError:
            pass
        return False
    finally:
        try:
            grave.unlink()
        except OSError:
            pass


def release(home: Path, holder: str, token: str = "", force: bool = False) -> dict:
    """Release needs the holder name and secret token. `force` (owner terminal only, enforced by the CLI) skips them.
    A tokenless release of an unreadable lock is refused while it may still be being created (see lock_owner)."""
    path = Path(home) / LOCK_NAME
    holder = str(holder)[:80]
    mine = lambda rec: force or (bool(token) and bool(rec) and rec.get("token") == token and rec.get("holder") == holder)  # noqa: E731
    held = _read(path)
    if not force and path.exists() and (held or _fresh(path)) and not mine(held):
        raise ManagerConflict("the lock belongs to another holder (holder name and token must match)", _public(held))
    stale_damaged = not held          # past the grace (checked above): acquire never makes an empty lock
    try:
        if not _remove_if(path, lambda rec: mine(rec) or (stale_damaged and not rec)) and path.exists() and not force:
            raise ManagerConflict("the lock changed hands; it belongs to another holder now", _public(_read(path)))
    except OSError as exc:
        raise ManagerConflict(f"the scheduler lock file could not be changed: {exc.__class__.__name__}") from exc
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
