"""SW-V4 L data migrations: every schema version up (backup first, data and settings kept) and back (restore of the
pre-migration backup), refusal of data newer than the program, a failing step leaves the data readable."""
import json
import sqlite3

import pytest
from release_fakes import seed_legacy, snapshot, user_rows

from jarves import backup, migrations


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    seed_legacy(h)
    return h


def cols(home, db, table):
    c = sqlite3.connect(home / db)
    try:
        return {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}
    finally:
        c.close()


def test_legacy_folder_reads_as_schema_1_and_empty_as_0(home, tmp_path):
    assert migrations.current(home) == migrations.LEGACY == 1
    assert migrations.current(tmp_path / "nothing") == 0
    assert "response_id" not in cols(home, "store.db", "delegations")        # the fixture really is the old layout
    assert "source" not in cols(home, "memory.db", "memories")


def test_fresh_folder_is_stamped_latest_without_a_backup(tmp_path):
    h = tmp_path / "fresh"
    r = migrations.run(h)
    assert r == {"from": 0, "to": migrations.LATEST, "steps": [], "backup": None}
    assert migrations.current(h) == migrations.LATEST
    assert not (h / "backups").exists()


def test_up_from_legacy_takes_a_verified_backup_first_and_keeps_every_row(home):
    before = user_rows(home)
    settings_before = {n: (home / n).read_bytes() for n in ("config.toml", "features.json", "identity.json")}
    r = migrations.run(home)
    assert r["from"] == 1 and r["to"] == migrations.LATEST and r["steps"] == [2, 3]
    assert backup.verify(r["backup"])["ok"]
    assert migrations.current(home) == migrations.LATEST
    assert user_rows(home) == before                                          # conversation, memory, job, setting
    assert {n: (home / n).read_bytes() for n in settings_before} == settings_before
    assert {"response_id"} <= cols(home, "store.db", "delegations")
    assert {"audio_started", "audio_ended"} <= cols(home, "store.db", "turns")
    assert {"source", "confidence", "kind"} <= cols(home, "memory.db", "memories")
    assert "recall_docs" in {r[0] for r in sqlite3.connect(home / "store.db").execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert backup.read_manifest(r["backup"])["note"] == "schema 1->3"
    assert "secrets.env" not in {f["name"] for f in backup.read_manifest(r["backup"])["files"]}


def test_run_is_idempotent_and_takes_no_second_backup(home):
    migrations.run(home)
    n = len(backup.list_backups(home))
    assert migrations.run(home) == {"from": migrations.LATEST, "to": migrations.LATEST, "steps": [], "backup": None}
    assert len(backup.list_backups(home)) == n


@pytest.mark.parametrize("start", [1, 2])
def test_up_and_back_from_every_schema(home, start):
    """Up to LATEST and back to `start`: the data (rows AND layout) is what it was, byte-for-byte for files."""
    if start == 2:                      # the 3.x layout: additive columns present, no recall index yet
        migrations.STEPS[0].up(home)
        migrations._stamp(home, 2)
        db = sqlite3.connect(home / "store.db")
        for t in ("recall_fts", "recall_meta", "recall_docs"):
            db.execute(f"DROP TABLE {t}")
        db.commit()
        db.close()
    want = snapshot(home)
    assert migrations.current(home) == start
    migrations.run(home)
    assert snapshot(home) != want                                             # the layout really changed
    r = migrations.down(home, start)
    assert migrations.current(home) == start
    assert snapshot(home) == want
    assert r["schema"] == start
    if start == 1:
        assert not (home / migrations.MARKER).exists()                       # the legacy layout has no marker


def test_down_without_a_backup_or_to_a_newer_schema_is_refused(home):
    migrations.run(home)
    for f in backup.backup_dir(home).glob("*.zip"):
        f.unlink()
    with pytest.raises(migrations.MigrationError) as e:
        migrations.down(home, 1)
    assert e.value.code == "MIGRATION_NO_BACKUP"
    with pytest.raises(migrations.MigrationError) as e:
        migrations.down(home, migrations.LATEST)
    assert e.value.code == "MIGRATION_NO_DOWN"


def test_data_newer_than_the_program_is_refused_not_downgraded(home):
    (home / migrations.MARKER).write_text(json.dumps({"schema": migrations.LATEST + 1}), encoding="utf-8")
    with pytest.raises(migrations.MigrationError) as e:
        migrations.run(home)
    assert e.value.code == "MIGRATION_DATA_NEWER"
    assert migrations.current(home) == migrations.LATEST + 1                 # untouched


def test_backup_failure_refuses_to_migrate_and_changes_nothing(home):
    want = snapshot(home)

    def broken(h, reason):
        raise backup.BackupError("BACKUP_FAILED", "disk full")
    with pytest.raises(migrations.MigrationError) as e:
        migrations.run(home, checkpoint=broken)
    assert e.value.code == "MIGRATION_BACKUP_FAILED"
    assert snapshot(home) == want and not (home / migrations.MARKER).exists()


def test_a_failing_step_keeps_the_last_good_schema_and_points_at_the_backup(home, monkeypatch):
    def boom(h):
        raise RuntimeError("step broke")
    monkeypatch.setattr(migrations, "STEPS", (migrations.STEPS[0], migrations.Step(3, "broken", boom)))
    with pytest.raises(migrations.MigrationError) as e:
        migrations.run(home)
    assert e.value.code == "MIGRATION_FAILED" and "jarves-backup" in e.value.action
    assert migrations.current(home) == 2                                     # step 2 done and stamped, 3 not
    assert user_rows(home)["memory"] == [("likes tea",)]


def test_unreadable_marker_is_an_error_not_a_guess(home):
    (home / migrations.MARKER).write_text("{broken", encoding="utf-8")
    with pytest.raises(migrations.MigrationError) as e:
        migrations.current(home)
    assert e.value.code == "MIGRATION_BAD_MARKER"
