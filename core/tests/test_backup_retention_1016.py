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
    monkeypatch.setattr(backup, "prune", lambda home, protect=(): pruned.append(1) or [])
    monkeypatch.setattr(backup, "_free_bytes", lambda p: 1024)
    with pytest.raises(backup.BackupError) as e:
        backup.create(tmp_path, "manual")
    assert e.value.code == "BACKUP_NO_SPACE" and "--manual 3 --checkpoints 1" in e.value.action
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
    manual = backup.create(tmp_path, "manual")                 # install-latest: "backup now"
    clock.t += 1
    pre = backup.checkpoint(tmp_path, "pre-update-1.0-to-99.0.0")   # updates.backup_before_update
    clock.t += 60
    assert backup.startup_checkpoint(tmp_path) == pre          # Core start: no third archive
    assert sorted(b["kind"] for b in backup.list_backups(tmp_path)) == ["manual", "pre-migration"]
    assert manual.is_file()
    assert backup.read_state(tmp_path)["version"] == "99.0.0"


def test_old_backup_does_not_stand_in_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    backup.create(tmp_path, "manual")
    clock.t += backup.RECENT_REUSE_S + 60
    p = backup.startup_checkpoint(tmp_path)
    assert p is not None and "pre-migration" in p.name


@pytest.mark.parametrize("kind", ["pre-restore", "manual", "auto"])
def test_only_a_pre_migration_checkpoint_stands_in_1016(tmp_path, monkeypatch, kind):
    clock = _aged(tmp_path, monkeypatch)                       # pre-restore holds the data BEFORE the restore;
    other = backup.create(tmp_path, kind)                      # manual/auto would hide the newest pre-migration one
    clock.t += 60
    p = backup.startup_checkpoint(tmp_path)
    assert p != other and "pre-migration" in p.name
    assert backup.list_backups(tmp_path)[0]["name"] == p.name  # "restore the newest pre-migration backup" is it


@pytest.mark.parametrize("where", ["db", "wal", "same_tick"])
def test_data_written_after_the_backup_forces_a_checkpoint_1016(tmp_path, monkeypatch, where):
    clock = _aged(tmp_path, monkeypatch, wal=where == "wal")
    recent = backup.checkpoint(tmp_path, "pre-update")
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
    assert p != recent and "pre-migration" in p.name


def test_empty_wal_left_by_a_reader_still_reuses_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    recent = backup.checkpoint(tmp_path, "pre-update")
    clock.t += 60
    (tmp_path / "memory.db-wal").write_bytes(b"")              # a read-only open leaves an empty -wal: no commit
    assert backup.startup_checkpoint(tmp_path) == recent


@pytest.mark.parametrize("damage", ["deleted", "not_a_zip"])
def test_missing_or_corrupt_recent_backup_is_not_reused_1016(tmp_path, monkeypatch, damage):
    clock = _aged(tmp_path, monkeypatch)
    recent = backup.checkpoint(tmp_path, "pre-update")
    clock.t += 60
    if damage == "deleted":
        recent.unlink()
    else:
        recent.write_bytes(b"not a zip")
    p = backup.startup_checkpoint(tmp_path)
    assert p is not None and p != recent and "pre-migration" in p.name and backup.verify(p)["ok"]


def test_reused_checkpoint_is_kept_by_prune_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    backup.set_settings(tmp_path, {"checkpoints": 1})
    first = backup.checkpoint(tmp_path, "pre-update")
    clock.t += 60
    assert backup.startup_checkpoint(tmp_path) == first
    clock.t += 60
    later = backup.checkpoint(tmp_path, "schema 3->4")         # a migration's own checkpoint, newer
    backup.prune(tmp_path)
    assert first.is_file() and later.is_file()                 # the stand-in checkpoint is protected


def test_low_disk_restore_keeps_the_archive_it_restores_1016(tmp_path, monkeypatch):
    clock = _aged(tmp_path, monkeypatch)
    bdir = tmp_path / "backups"
    old = backup.create(tmp_path, "manual", now=T0 - 30 * 86400)      # beyond the manual retention below
    for i in range(3):
        clock.t += 60
        backup.create(tmp_path, "manual")
    backup.set_settings(tmp_path, {"manual": 1})
    free = iter([1024])                                        # low disk at the pre-restore checkpoint only
    monkeypatch.setattr(backup, "_free_bytes", lambda p: next(free, 10**12))
    monkeypatch.setattr(backup, "core_running", lambda *a, **k: False)
    clock.t += 60
    res = backup.restore(old, tmp_path)
    assert res["ok"] and old.is_file() and res["from"] == old.name
    assert len([b for b in backup.list_backups(tmp_path) if b["kind"] == "manual"]) == 2   # old + newest


def test_space_check_counts_temp_snapshots_on_the_same_disk_1016(tmp_path, monkeypatch):
    _db(tmp_path)
    db = (tmp_path / "memory.db").stat().st_size
    seen = []
    monkeypatch.setattr(backup, "_device", lambda p: 1)
    monkeypatch.setattr(backup, "prune", lambda home, protect=(): [])
    monkeypatch.setattr(backup, "_free_bytes", lambda p: seen.append(p) or db + backup.SPACE_MARGIN + 1)
    with pytest.raises(backup.BackupError) as e:               # enough for the archive, not archive + snapshot
        backup.create(tmp_path, "manual")
    assert e.value.code == "BACKUP_NO_SPACE"
    assert seen == [backup.backup_dir(tmp_path)] * 2           # same disk: one combined check, before and after prune


def test_space_check_checks_a_separate_temp_disk_1016(tmp_path, monkeypatch):
    _db(tmp_path)
    tmp = backup.Path(backup.tempfile.gettempdir())
    monkeypatch.setattr(backup, "_device", lambda p: 2 if p == tmp else 1)
    monkeypatch.setattr(backup, "prune", lambda home, protect=(): [])
    monkeypatch.setattr(backup, "_free_bytes", lambda p: 1024 if p == tmp else 10**12)
    with pytest.raises(backup.BackupError) as e:               # the backups disk is fine, the temp disk is full
        backup.create(tmp_path, "manual")
    assert e.value.code == "BACKUP_NO_SPACE"


def test_doctor_warns_over_budget_1016(tmp_path):
    _db(tmp_path)
    bdir = tmp_path / "backups"
    bdir.mkdir()
    (bdir / "jarves-backup-20261101-030000-manual.zip").write_bytes(b"x" * (2 * 2**20))
    assert backup.doctor_row(tmp_path)[0] == "ok"
    backup.set_settings(tmp_path, {"budget_mb": 1})
    level, what, detail = backup.doctor_row(tmp_path)
    assert level == "warn" and "budget" in what and "jarvis backup prune" in detail
    with pytest.raises(ValueError):
        backup.set_settings(tmp_path, {"budget_mb": -1})


def test_default_budget_holds_the_default_retention_1016():
    d = backup.DEFAULTS                                        # archives are up to about 600 MB each (#1016)
    most = d["daily"] + d["weekly"] + d["manual"] + 2 * d["checkpoints"]
    assert d["budget_mb"] >= most * 600
