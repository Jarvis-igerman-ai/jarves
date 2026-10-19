"""Backup / restore (SW-V2 M5, spec section 12, D17) with fault injection: consistent SQLite snapshots under a live
writer, no credentials in archives, verification catches damage, retention 7 daily + 4 weekly, restore into an
isolated profile, a failing restore keeps the previous data, full disk leaves no half archive, pre-migration
checkpoint on a version change; the sanitized size-bounded diagnostic bundle; dashboard + CLI."""
import http.client
import json
import os
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timedelta

import pytest

from jarves import backup, dashboard, diagbundle
from jarves.config import Config
from jarves.memory import MemoryStore
from jarves.store import Store


@pytest.fixture
def profile(home):
    m = MemoryStore(home / "memory.db")
    for i in range(5):
        m.add(f"עובדה {i}")
    m.set_setting("voice.voice", "cedar")
    m.close()
    st = Store(home / "store.db")
    st.ingest({"ts": time.time(), "code": "LIVE_STARTED", "msg": "live (gpt-live-1 -> gpt-6-luna)", "session": "s1"})
    st.ingest({"ts": time.time(), "code": "LIVE_USER_SAID", "text": "שלום"})
    st.commit()
    st.close()
    (home / "config.toml").write_text('[voice]\nmode = "live"\n', encoding="utf-8")
    (home / "features.json").write_text('{"version": 1, "enabled": {"music": true}}', encoding="utf-8")
    (home / "workspace").mkdir()
    (home / "workspace" / "SOUL.md").write_text("# SOUL\nאני JARVES.", encoding="utf-8")
    (home / "notes").mkdir()
    (home / "notes" / "shopping.md").write_text("חלב", encoding="utf-8")
    # credentials and volatile files that must never be archived
    (home / "secrets.env").write_text("OPENAI_API_KEY=sk-test-not-real-0123456789abcdef\n", encoding="utf-8")
    (home / "link_token").write_text("abc", encoding="utf-8")
    (home / "chatgpt").mkdir()
    (home / "chatgpt" / "account.json").write_text('{"refresh_token": "rt"}', encoding="utf-8")
    (home / "google").mkdir()
    (home / "google" / "token.json").write_text('{"refresh_token": "rt"}', encoding="utf-8")
    (home / "workspace" / "LEAK.md").write_text("key sk-proj-ABCDEFGHIJKLMNOPQRSTUV here", encoding="utf-8")
    (home / "logs").mkdir()
    (home / "logs" / "jarves.log").write_text(json.dumps({"ts": "2026-10-19T10:00:00", "level": "INFO",
        "component": "voice", "code": "LIVE_USER_SAID", "text": "my secret plan", "email": "me@example.com",
        "msg": "Authorization: Bearer abcdefghijklmnop"}) + "\n", encoding="utf-8")
    (home / "health.json").write_text(json.dumps({"ts": time.time(), "core": "stopped"}), encoding="utf-8")
    return home


def names(path):
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


def test_backup_contents_and_no_credentials(profile):
    p = backup.create(profile, "manual")
    got = names(p)
    assert {"data/memory.db", "data/store.db", "data/config.toml", "data/features.json", "data/workspace/SOUL.md",
            "data/notes/shopping.md", "manifest.json"} <= got
    assert not any(n.endswith(("secrets.env", "link_token", "health.json")) or "/chatgpt/" in n or "/google/" in n
                   or "/logs/" in n or n.endswith("LEAK.md") for n in got)
    with zipfile.ZipFile(p) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
        man = json.loads(z.read("manifest.json"))
    assert b"sk-test-not-real" not in blob and b"sk-proj-" not in blob
    reasons = {e["name"]: e["reason"] for e in man["excluded"]}
    assert reasons["workspace/LEAK.md"] == "contains a credential" and "secrets.env" in reasons
    assert man["contains_credentials"] is False and backup.verify(p)["ok"]


def test_sqlite_snapshot_is_consistent_under_a_live_writer(profile):
    stop = threading.Event()

    def writer():
        db = sqlite3.connect(str(profile / "memory.db"), timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS pairs (a INTEGER, b INTEGER)")
        i = 0
        while not stop.is_set():
            with db:                       # one transaction writes both rows: a snapshot must hold both or none
                db.execute("INSERT INTO pairs VALUES (?, 1)", (i,))
                db.execute("INSERT INTO pairs VALUES (?, 2)", (i,))
            i += 1
        db.close()
    t = threading.Thread(target=writer)
    t.start()
    try:
        time.sleep(0.2)
        paths = [backup.create(profile, "manual") for _ in range(3)]
    finally:
        stop.set()
        t.join()
    for p in paths:
        r = backup.restore_test(p)
        assert r["ok"], r
        rows = next(c["rows"] for c in r["checks"] if c["db"] == "memory.db")
        assert rows["pairs"] % 2 == 0 and rows["memories"] == 5


def test_verify_detects_damage(profile, tmp_path):
    p = backup.create(profile, "manual")
    raw = bytearray(p.read_bytes())
    i = raw.find(b"SQLite format 3")
    raw[i + 200] ^= 0xFF                                            # flip a byte inside a stored db
    bad = tmp_path / "flipped.zip"
    bad.write_bytes(bytes(raw))
    assert not backup.verify(bad)["ok"]
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    assert backup.verify(tmp_path / "junk.zip")["errors"][0].startswith("not a readable")
    missing = tmp_path / "missing.zip"                               # manifest lists a file the zip lacks
    with zipfile.ZipFile(p) as src, zipfile.ZipFile(missing, "w") as dst:
        for n in src.namelist():
            if n != "data/config.toml":
                dst.writestr(n, src.read(n))
    assert "missing config.toml" in backup.verify(missing)["errors"]
    with pytest.raises(backup.BackupError) as e:
        backup.restore(missing, tmp_path / "x")
    assert e.value.code == "RESTORE_VERIFY_FAILED"


def test_restore_into_isolated_profile_and_sign_in_hints(profile, tmp_path):
    p = backup.create(profile, "manual")
    r = backup.restore_test(p)
    assert r["ok"] and r["memories"] == 5 and r["conversations"] == 1
    target = tmp_path / "newpc"
    out = backup.restore(p, target)
    assert MemoryStore(target / "memory.db").get_setting("voice.voice") == "cedar"
    assert (target / "workspace" / "SOUL.md").read_text(encoding="utf-8").startswith("# SOUL")
    assert not (target / "secrets.env").exists()
    assert any("jarvis key" in h for h in out["sign_in_again"])
    assert backup.read_state(profile)["last_restore_test"]["ok"]


def test_failed_restore_keeps_previous_data(profile, monkeypatch):
    p = backup.create(profile, "manual")
    m = MemoryStore(profile / "memory.db")
    m.add("added after the backup")
    m.close()
    (profile / "workspace" / "SOUL.md").write_text("# SOUL v2", encoding="utf-8")
    real = os.replace
    n = {"i": 0}

    def flaky(src, dst):
        if ".restore-staging" in str(src):
            n["i"] += 1
            if n["i"] == 3:
                raise OSError(28, "No space left on device")
        return real(src, dst)
    monkeypatch.setattr(backup.os, "replace", flaky)
    with pytest.raises(backup.BackupError) as e:
        backup.restore(p, profile)
    monkeypatch.setattr(backup.os, "replace", real)
    assert e.value.code == "RESTORE_FAILED"
    m = MemoryStore(profile / "memory.db")
    assert m.count() == 6                                              # the newer data is still there
    m.close()
    assert (profile / "workspace" / "SOUL.md").read_text(encoding="utf-8") == "# SOUL v2"
    assert any(b["kind"] == "pre-restore" for b in backup.list_backups(profile))
    assert not list(profile.glob(".restore-staging*"))


def test_restore_refuses_while_core_runs(profile, monkeypatch):
    p = backup.create(profile, "manual")
    monkeypatch.setattr(backup, "core_running", lambda home, port=None: True)
    with pytest.raises(backup.BackupError) as e:
        backup.restore(p, profile)
    assert e.value.code == "RESTORE_CORE_RUNNING"
    assert backup.restore_test(p)["ok"]                                # the drill is always safe


def test_full_disk_leaves_no_half_archive(profile, monkeypatch):
    good = backup.create(profile, "manual")
    real = zipfile.ZipFile.write

    def full(self, *a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(zipfile.ZipFile, "write", full)
    with pytest.raises(backup.BackupError) as e:
        backup.create(profile, "manual")
    monkeypatch.setattr(zipfile.ZipFile, "write", real)
    assert e.value.code == "BACKUP_FAILED"
    files = sorted(x.name for x in (profile / "backups").iterdir() if x.suffix in (".zip", ".partial"))
    assert files == [good.name] and backup.verify(good)["ok"]


def test_retention_7_daily_4_weekly(profile):
    start = datetime(2026, 10, 19, 3, 0)
    bdir = profile / "backups"
    bdir.mkdir()

    def fake(kind, when):                       # retention looks at names only: placeholders keep the test fast
        (bdir / f"jarves-backup-{when:%Y%m%d-%H%M%S}-{kind}.zip").write_bytes(b"x")
    for d in range(40):                                                # 40 days, two automatic backups per day
        for h in (0, 6):
            fake("auto", start - timedelta(days=d, hours=h))
    for i in range(12):
        fake("manual", start - timedelta(minutes=i))
    backup.create(profile, "auto", now=(start + timedelta(minutes=1)).timestamp())    # a real one prunes
    start = start + timedelta(minutes=1)
    autos = [b for b in backup.list_backups(profile) if b["kind"] == "auto"]
    days = {datetime.fromtimestamp(b["ts"]).date() for b in autos}
    assert len(days) <= 7 + 4 and len(autos) == len(days)             # one per kept day/week
    assert max(days) == start.date()
    assert len([b for b in backup.list_backups(profile) if b["kind"] == "manual"]) == 10
    backup.set_settings(profile, {"daily": 2, "weekly": 0})
    backup.prune(profile)
    assert len([b for b in backup.list_backups(profile) if b["kind"] == "auto"]) == 2
    with pytest.raises(ValueError):
        backup.set_settings(profile, {"daily": 0})


def test_auto_backup_due_daily_and_switchable(profile):
    assert backup.due(profile)
    assert backup.maybe_auto(profile) is not None
    assert not backup.due(profile) and backup.maybe_auto(profile) is None
    backup.set_settings(profile, {"auto": False})
    assert not backup.due(profile, now=time.time() + 2 * 86400)


def test_pre_migration_checkpoint_on_version_change(profile, monkeypatch):
    p = backup.startup_checkpoint(profile)
    assert p is not None and "pre-migration" in p.name and backup.verify(p)["ok"]
    assert backup.startup_checkpoint(profile) is None                 # same version: nothing to do
    monkeypatch.setattr(backup, "__version__", "9.9.9")
    monkeypatch.setattr(backup, "create", lambda *a, **k: (_ for _ in ()).throw(
        backup.BackupError("BACKUP_FAILED", "disk full", "free space")))

    class H:
        def __init__(self):
            self.calls = []

        def set(self, *a):
            self.calls.append(a)
    h = H()
    assert backup.startup_checkpoint(profile, h) is None
    assert h.calls[0][:2] == ("backup", "degraded")
    assert backup.read_state(profile)["version"] != "9.9.9"           # retried at the next start


def test_diag_bundle_is_sanitized_and_size_bounded(profile, tmp_path):
    lines = [json.dumps({"ts": "2026-10-19T10:00:00", "level": "INFO", "component": "x", "code": "C",
                         "msg": "m" * 500, "text": "personal words"}) for _ in range(3000)]
    (profile / "logs" / "jarves.log").write_text("\n".join(lines) + "\n" + (profile / "logs" / "jarves.log.1").name,
                                                 encoding="utf-8")
    (profile / "logs" / "jarves.log.1").write_text(json.dumps({"code": "OLD", "msg": "key sk-ABCDEFGHIJKLMNOPQRS"}),
                                                   encoding="utf-8")
    out = tmp_path / "diag.zip"
    man = diagbundle.build(profile, Config(), out, lines=5000, max_bytes=300_000)
    assert out.stat().st_size <= 300_000 and man["log_lines_cut_for_size"] > 0
    with zipfile.ZipFile(out) as z:
        blob = "".join(z.read(n).decode("utf-8") for n in z.namelist())
        info = json.loads(z.read("info.json"))
    assert "personal words" not in blob and "sk-ABCDEFGH" not in blob and "sk-test-not-real" not in blob
    assert "me@example.com" not in blob and "abcdefghijklmnop" not in blob
    assert info["keys_present"]["openai_api"] is False
    assert {"features.json", "usage.json", "backups.json", "store_summary.json", "files.json"} <= set(man["members"])


def test_dashboard_usage_and_backup_routes(profile):
    d = dashboard.Dashboard(profile, Config()).start()
    try:
        assert {"jarves.usage", "jarves.backup"} <= set(d.plugins)

        def call(method, path, body=None):
            c = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
            h = {"Host": f"127.0.0.1:{d.port}", "Cookie": f"{dashboard.COOKIE}={d.url.split('t=')[1]}",
                 "X-Jarves": "1"}
            c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
            r = c.getresponse()
            data = r.read()
            c.close()
            return r.status, data
        s, data = call("GET", "/api/usage")
        u = json.loads(data)
        assert s == 200 and u["footer"]["chatgpt_plan"]["meter"]["percent_text"] == "Unknown"
        assert call("POST", "/api/usage/settings", {"thresholds": "80,100"})[0] == 200
        assert call("POST", "/api/usage/settings", {"thresholds": "500"})[0] == 400
        s, data = call("POST", "/api/backup", {})
        name = json.loads(data)["name"]
        assert s == 200 and json.loads(call("POST", "/api/backup/verify", {"name": name})[1])["ok"]
        assert json.loads(call("POST", "/api/backup/test", {"name": name})[1])["ok"]
        assert call("POST", "/api/backup/verify", {"name": "../memory.db"})[0] == 404
        listing = json.loads(call("GET", "/api/backup")[1])
        assert listing["backups"][0]["name"] == name and listing["guidance"]
        s, js = call("GET", "/static/usage.js")
        assert s == 200 and b"PAGES.usage" in js
        s, html = call("GET", "/")
        assert b"/static/usage.js" in html
    finally:
        d.stop()


def test_cli_backup_restore_usage(profile, capsys, monkeypatch):
    from jarves import cli
    monkeypatch.setattr(backup, "core_running", lambda home, port=None: False)
    assert cli.main(["backup"]) == 0
    assert cli.main(["backup", "list"]) == 0
    assert cli.main(["backup", "verify"]) == 0
    assert cli.main(["restore", "--test", backup.list_backups(profile)[0]["name"]]) == 0
    assert cli.main(["restore", "--yes", backup.list_backups(profile)[0]["name"]]) == 0
    assert cli.main(["usage"]) == 0
    assert cli.main(["usage", "thresholds", "70,100"]) == 0
    out = capsys.readouterr().out
    assert "PASSED" in out and "Unknown" in out and "[70, 100]" in out
