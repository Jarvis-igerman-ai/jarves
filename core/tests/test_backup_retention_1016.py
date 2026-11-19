"""Regression coverage for owner-controlled backup retention (issue #1016)."""
import argparse
import json
import os
import sqlite3

import pytest

from jarves import backup


def test_cli_sets_manual_and_checkpoint_retention_1016(tmp_path, capsys):
    parser = argparse.ArgumentParser()
    backup.add_cli(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args([
        "backup", "settings", "--manual", "2", "--checkpoints", "1",
    ])

    assert backup.cmd_backup(args, None, tmp_path) == 0
    settings = backup.get_settings(tmp_path)
    assert settings["manual"] == 2
    assert settings["checkpoints"] == 1
    shown = json.loads(capsys.readouterr().out)
    assert shown["manual"] == 2
    assert shown["checkpoints"] == 1


def _db(home):
    import sqlite3
    db = sqlite3.connect(str(home / "memory.db"))
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS t (x)")
    db.execute("INSERT INTO t VALUES ('hello')")
    db.commit()
    db.close()


def test_defaults_keep_fewer_backups_1016():
    d = backup.DEFAULTS
    assert (d["daily"], d["weekly"], d["manual"], d["checkpoints"]) == (3, 2, 5, 2)


def test_low_disk_prunes_first_then_refuses_1016(tmp_path, monkeypatch):
    _db(tmp_path)
    pruned = []
    monkeypatch.setattr(backup, "prune", lambda home: pruned.append(1) or [])
    monkeypatch.setattr(backup, "_free_bytes", lambda p: 1024)
    try:
        backup.create(tmp_path, "manual")
        raise AssertionError("expected BackupError")
    except backup.BackupError as e:
        assert e.code == "BACKUP_NO_SPACE" and "--manual" in e.action
    assert pruned == [1]
    assert not list((tmp_path / "backups").glob("*.zip")) and not list((tmp_path / "backups").glob("*.partial"))


def test_low_disk_recovers_after_prune_1016(tmp_path, monkeypatch):
    _db(tmp_path)
    free = iter([1024, 10**12])
    monkeypatch.setattr(backup, "_free_bytes", lambda p: next(free, 10**12))
    assert backup.create(tmp_path, "manual").is_file()


OLD, T0 = 1_700_000_000, 1_800_000_000.0        # data written long ago; a fixed fake "now" (clock-free)


class _Clock:
    t = T0

    def time(self):
        return self.t


def _aged(home, monkeypatch, wal=False):
    _db(home)
    if wal:
        db = sqlite3.connect(home / "memory.db")
        db.execute("PRAGMA journal_mode=WAL")
        db.close()
    for p in home.rglob("*"):
        if p.is_file():
            os.utime(p, (OLD, OLD))
    clock = _Clock()
    monkeypatch.setattr(backup, "time", clock)
    monkeypatch.setattr(backup, "__version__", "99.0.0")
    return clock


def test_install_backup_stands_in_for_premigration_checkpoint_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    manual = backup.create(tmp_path, "manual", note="before a hotfix")
    clock.t += 60
    assert backup.startup_checkpoint(tmp_path) == manual
    assert [b["kind"] for b in backup.list_backups(tmp_path)] == ["manual"]   # one backup per install
    assert backup.read_state(tmp_path)["version"] == "99.0.0"


def test_old_backup_does_not_stand_in_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    backup.create(tmp_path, "manual")
    clock.t += backup.RECENT_REUSE_S + 60
    p = backup.startup_checkpoint(tmp_path)
    assert p is not None and "pre-migration" in p.name


def test_pre_restore_checkpoint_never_stands_in_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    pre = backup.create(tmp_path, "pre-restore", note="before restoring X")   # holds the data BEFORE the restore
    clock.t += 60
    p = backup.startup_checkpoint(tmp_path)
    assert p != pre and "pre-migration" in p.name


@pytest.mark.parametrize("where", ["db", "wal", "same_tick"])
def test_data_written_after_the_backup_forces_a_checkpoint_1016(tmp_path, monkeypatch, where):
    clock = _aged(tmp_path, monkeypatch, wal=where == "wal")
    manual = backup.create(tmp_path, "manual")
    clock.t += 60
    db = tmp_path / "memory.db"
    writer = None
    if where == "db":
        os.utime(db, (T0 + 30, T0 + 30))                       # a write between the install backup and Core stop
    elif where == "wal":
        writer = sqlite3.connect(db)                           # a commit still only in the WAL (writer open)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO t VALUES ('late')")
        writer.commit()
        assert db.stat().st_mtime == OLD and (tmp_path / "memory.db-wal").stat().st_size
        os.utime(tmp_path / "memory.db-wal", (T0 + 30, T0 + 30))
    else:
        os.utime(db, (T0 - 1, T0 - 1))                         # same 2 s FAT/exFAT tick as the backup start
    try:
        p = backup.startup_checkpoint(tmp_path)
    finally:
        if writer is not None:
            writer.close()
    assert p != manual and "pre-migration" in p.name


def test_empty_wal_left_by_a_reader_still_reuses_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    manual = backup.create(tmp_path, "manual")
    clock.t += 60
    (tmp_path / "memory.db-wal").write_bytes(b"")              # a read-only open leaves an empty -wal: no commit
    assert backup.startup_checkpoint(tmp_path) == manual


@pytest.mark.parametrize("damage", ["deleted", "not_a_zip"])
def test_missing_or_corrupt_recent_backup_is_not_reused_1016(tmp_path, monkeypatch, damage):
    clock = _aged(tmp_path, monkeypatch)
    manual = backup.create(tmp_path, "manual")
    clock.t += 60
    if damage == "deleted":
        manual.unlink()
    else:
        manual.write_bytes(b"not a zip")
    p = backup.startup_checkpoint(tmp_path)
    assert p is not None and p != manual and "pre-migration" in p.name and backup.verify(p)["ok"]


def test_reused_auto_backup_is_kept_by_prune_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    first = backup.create(tmp_path, "auto")
    clock.t += 60
    assert backup.startup_checkpoint(tmp_path) == first
    clock.t += 60
    later = backup.create(tmp_path, "auto")                    # same day: the daily rule keeps only the newest
    backup.prune(tmp_path)
    assert first.is_file() and later.is_file()                 # the stand-in checkpoint is protected


def test_space_check_counts_temp_snapshots_on_the_same_disk_1016(tmp_path, monkeypatch):
    _db(tmp_path)
    db = (tmp_path / "memory.db").stat().st_size
    seen = []
    monkeypatch.setattr(backup, "_device", lambda p: 1)
    monkeypatch.setattr(backup, "prune", lambda home: [])
    monkeypatch.setattr(backup, "_free_bytes", lambda p: seen.append(p) or db + backup.SPACE_MARGIN + 1)
    try:                                                       # enough for the archive, not archive + snapshot
        backup.create(tmp_path, "manual")
        raise AssertionError("expected BackupError")
    except backup.BackupError as e:
        assert e.code == "BACKUP_NO_SPACE"


def test_doctor_warns_over_budget_1016(tmp_path):
    _db(tmp_path)
    bdir = tmp_path / "backups"
    bdir.mkdir()
    (bdir / "jarves-backup-20261101-030000-manual.zip").write_bytes(b"x" * (2 * 2**20))
    assert backup.doctor_row(tmp_path)[0] == "ok"
    backup.set_settings(tmp_path, {"budget_mb": 1})
    level, what, detail = backup.doctor_row(tmp_path)
    assert level == "warn" and "budget" in what and "jarvis backup prune" in detail
    try:
        backup.set_settings(tmp_path, {"budget_mb": -1})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
