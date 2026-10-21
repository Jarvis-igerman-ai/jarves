"""Versioned data migrations (SW-V4 package L).

The data folder carries a marker `schema.json` {"schema": N, "app": "<version that wrote it>"}. A folder without the
marker but with databases is the old 0.1.0.x layout (schema 1). Every step moves one schema version up and is
idempotent. Before the first pending step a verified pre-migration backup is taken (jarves.backup.checkpoint, note
"schema A->B"); any failure there refuses to migrate and leaves the data as it was.

Down-path rule (the same for every step): the additive changes cannot be undone in place without risking data, so
`down(home, target)` restores the newest pre-migration backup that was taken when leaving `target`
(jarves.backup.restore, which checkpoints the current data first). `jarvis update --rollback` uses it.

    run(home)                      Core start / installer: bring the data to LATEST (no-op when there)
    current(home)                  the schema the data is at
    down(home, target, port=None)  restore the pre-migration backup of schema `target`
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__, logs

MARKER = "schema.json"
LEGACY = 1                      # 0.1.0.x: no marker
DB_NAMES = ("store.db", "memory.db")


class MigrationError(Exception):
    def __init__(self, code: str, message: str, action: str = ""):
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


@dataclass(frozen=True)
class Step:
    to: int
    label: str
    up: Callable[[Path], None]


def _up_3x(home: Path) -> None:
    """3.x: the additive columns of store.db (delegations.response_id, turns.audio_*) and memory.db (provenance)."""
    if (home / "store.db").is_file():
        from .store import Store
        Store(home / "store.db").close()
    if (home / "memory.db").is_file():
        from .memory import MemoryStore
        MemoryStore(home / "memory.db").close()


def _up_4x(home: Path) -> None:
    """4.x: the conversation-recall index in store.db (package G); the index is rebuilt lazily, tables only here."""
    p = home / "store.db"
    if not p.is_file():
        return
    from . import recall
    db = sqlite3.connect(p, timeout=5)
    try:
        recall.ensure(db)
        db.commit()
    finally:
        db.close()


STEPS: tuple[Step, ...] = (
    Step(2, "3.x additive columns", _up_3x),
    Step(3, "4.x recall index", _up_4x),
)
LATEST = STEPS[-1].to


def _marker(home: Path) -> Path:
    return Path(home) / MARKER


def has_data(home: Path) -> bool:
    return any((Path(home) / n).is_file() for n in DB_NAMES)


def current(home: Path) -> int:
    """The schema of this data folder; unreadable marker = the data cannot be trusted (MigrationError)."""
    home = Path(home)
    try:
        raw = json.loads(_marker(home).read_text(encoding="utf-8"))
        n = int(raw["schema"])
    except FileNotFoundError:
        return LEGACY if has_data(home) else 0
    except (OSError, ValueError, KeyError, TypeError):
        raise MigrationError("MIGRATION_BAD_MARKER", "schema.json is unreadable", "restore a backup (`jarvis restore`)") from None
    return n


def _stamp(home: Path, schema: int) -> None:
    p = _marker(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": schema, "app": __version__}), encoding="utf-8")
    os.replace(tmp, p)


def run(home: Path, *, checkpoint: Callable[[Path, str], Path] | None = None) -> dict:
    """Bring the data to LATEST. Returns {"from", "to", "steps", "backup"}. Never downgrades: data newer than this
    app (a rollback to an older program) is refused so the old program cannot corrupt it."""
    home = Path(home)
    cur = current(home)
    if cur > LATEST:
        raise MigrationError("MIGRATION_DATA_NEWER", "the data folder was written by a newer JARVES",
                             "install the newer version, or roll back with `jarvis update --rollback`")
    if cur == 0:                                    # fresh folder: nothing to migrate or protect
        _stamp(home, LATEST)
        return {"from": 0, "to": LATEST, "steps": [], "backup": None}
    todo = [s for s in STEPS if s.to > cur]
    if not todo:
        return {"from": cur, "to": cur, "steps": [], "backup": None}
    from . import backup
    take = checkpoint or backup.checkpoint
    try:
        bak = take(home, f"schema {cur}->{LATEST}")
    except backup.BackupError as e:
        raise MigrationError("MIGRATION_BACKUP_FAILED", f"backup before the migration failed ({e.code}); not migrating",
                             "free disk space, then start again") from e
    done = []
    at = cur
    for s in todo:
        try:
            s.up(home)
        except Exception as e:
            logs.error("migrations", "MIGRATION_FAILED", "migration step failed; data left at the last good schema",
                       error_type=type(e).__name__, step=s.to)
            raise MigrationError("MIGRATION_FAILED", f"migration to schema {s.to} failed ({type(e).__name__})",
                                 f"your data before the update is in {Path(bak).name}") from e
        _stamp(home, s.to)
        at = s.to
        done.append(s.to)
    logs.info("migrations", "MIGRATION_DONE", "data migrated", count=len(done), backup=Path(bak).name, step=at)
    return {"from": cur, "to": at, "steps": done, "backup": str(bak)}


def backup_for(home: Path, target: int) -> Path | None:
    """The newest pre-migration backup taken when the data was at schema `target` (its note starts "schema target->")."""
    from . import backup
    for b in backup.list_backups(home):
        if b["kind"] != "pre-migration":
            continue
        p = backup.backup_dir(home) / b["name"]
        try:
            note = str(backup.read_manifest(p).get("note") or "")
        except Exception:
            continue
        if note.startswith(f"schema {target}->"):
            return p
    return None


def down(home: Path, target: int, *, port: int | None = None) -> dict:
    """Put the data back at schema `target` by restoring the pre-migration backup taken when leaving it."""
    from . import backup
    home = Path(home)
    if target < LEGACY or target >= current(home):
        raise MigrationError("MIGRATION_NO_DOWN", f"the data is not newer than schema {target}")
    bak = backup_for(home, target)
    if bak is None:
        raise MigrationError("MIGRATION_NO_BACKUP", f"no pre-migration backup for schema {target} was found",
                             "choose a backup yourself (`jarvis backup list`, `jarvis restore`)")
    try:
        res = backup.restore(bak, home, port=port)
    except backup.BackupError as e:
        raise MigrationError(e.code, e.message, getattr(e, "action", "")) from e
    if target == LEGACY and _marker(home).is_file() and json.loads(_marker(home).read_text("utf-8")).get("schema", 0) > LEGACY:
        _marker(home).unlink()                      # the legacy backup predates the marker: the restore leaves ours
    return {"restored": res["from"], "schema": current(home)}
