"""JAR-158: disk-footprint measurement of the backups folder after a simulated 40-day life (hermetic, clock-injected).

Not a size gate on real data: it pins the worst-case backup COUNT (7 daily + 4 weekly + 10 manual + 5+5 checkpoints) and
reports the measured MB so docs/reports/2026-11-10-disk-footprint.md stays checkable. Run with `-s` to see the numbers.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

from jarves import backup

DAY = 86400
MB = 1024 * 1024


def _fake_home(home, *, db_mb: int = 1, files_mb: int = 1):
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "memory.db")
    db.execute("CREATE TABLE t (b BLOB)")
    db.execute("INSERT INTO t VALUES (?)", (os.urandom(db_mb * MB),))     # random = incompressible: worst case for zip
    db.commit()
    db.close()
    (home / "workspace").mkdir()
    (home / "workspace" / "notes.bin").write_bytes(os.urandom(files_mb * MB))


def test_backup_folder_is_bounded_by_count_after_120_days(tmp_path):
    home = tmp_path / "jarves"
    _fake_home(home)
    start = datetime(2026, 1, 1, 3, 0, 0).timestamp()
    for d in range(40):
        t = start + d * DAY
        backup.create(home, "auto", now=t)
        if d % 10 == 0:
            backup.create(home, "manual", now=t + 60)
        if d % 7 == 0:                                   # an update: a pre-migration checkpoint, sometimes a restore
            backup.create(home, "pre-migration", now=t + 120)
        if d % 15 == 0:
            backup.create(home, "pre-restore", now=t + 180)
    items = backup.list_backups(home)
    by_kind = {k: [b for b in items if b["kind"] == k] for k in backup.KINDS}
    total = sum(b["size"] for b in items)
    one = max(b["size"] for b in items)
    print(f"\nJAR-158 measured: {len(items)} archives, {total / MB:.1f} MB total, {one / MB:.2f} MB each; "
          + ", ".join(f"{k}={len(v)}" for k, v in by_kind.items()))
    assert len(by_kind["manual"]) <= 10
    assert len(by_kind["pre-migration"]) <= 5 and len(by_kind["pre-restore"]) <= 5
    assert len(by_kind["auto"]) <= 7 + 4
    assert total <= len(items) * one * 1.05              # no hidden growth beyond count x size
    assert not list(backup.backup_dir(home).glob("*.partial"))
