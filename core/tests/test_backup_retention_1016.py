"""Regression coverage for owner-controlled backup retention (issue #1016)."""
import argparse
import json
import time

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


def test_install_backup_stands_in_for_premigration_checkpoint_1016(tmp_path, monkeypatch):
    _db(tmp_path)
    manual = backup.create(tmp_path, "manual", note="before a hotfix")
    monkeypatch.setattr(backup, "__version__", "99.0.0")
    assert backup.startup_checkpoint(tmp_path) == manual
    assert [b["kind"] for b in backup.list_backups(tmp_path)] == ["manual"]   # one backup per install
    assert backup.read_state(tmp_path)["version"] == "99.0.0"


def test_old_backup_does_not_stand_in_1016(tmp_path, monkeypatch):
    _db(tmp_path)
    backup.create(tmp_path, "manual", now=time.time() - backup.RECENT_REUSE_S - 60)
    monkeypatch.setattr(backup, "__version__", "99.0.0")
    p = backup.startup_checkpoint(tmp_path)
    assert p is not None and "pre-migration" in p.name


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
