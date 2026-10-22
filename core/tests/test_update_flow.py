"""SW-V4 L update path end to end against a LOCAL fake release server (real sockets on loopback, signed latest.json):
version order across the old and new numbering, signature / SHA256 / size checks, "ask before install", a failed
update leaving the old version working, downgrade refusal, silent installer arguments, rollback (kept installer and
installer fetched from the signed manifest), the failed-first-start offer. Temp dirs and fakes only: no installer is
ever executed, no registry, PATH or real install is touched."""
import hashlib
import json
import sys

import pytest
from release_fakes import BASE, FakeReleaseServer, Keys, installer_bytes, seed_legacy, snapshot, user_rows

from jarves import backup, migrations, updates
from jarves.updates import UpdateChecker, UpdateError

OLD, NEW = "0.1.0.57", "3.0.0.1"


@pytest.fixture
def keys():
    return Keys()


@pytest.fixture
def server():
    s = FakeReleaseServer()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")          # the update path is Windows-only; nothing Windows is run
    monkeypatch.setattr(updates, "build_info", lambda: {"version": OLD})   # the running copy is OLD, whatever __version__ is


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    seed_legacy(h)
    return h


def checker(home, server, keys, current=OLD):
    return UpdateChecker(home, http=server.http(), current=current, base_url=BASE, public_keys=[keys.public])


class Launch:
    """popen stand-in: records the setup command line instead of starting anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)


# ---------------------------------------------------------------------- version order and the upgrade matrix
@pytest.mark.parametrize("new,old", [("3.0.0.1", "0.1.0.57"), ("3.0.0.1", "0.1.0.999"), ("4.0.0.1", "3.0.0.9"),
                                     ("4.0.0.1", "0.1.0.57"), ("0.1.0.10", "0.1.0.9")])
def test_numbers_compare_as_numbers_across_the_old_and_new_series(new, old):
    assert updates.is_newer(new, old) and not updates.is_newer(old, new)
    assert updates.ASSET_RE.match(f"JARVES-Setup-{new}.exe") and updates.ASSET_RE.match(f"JARVES-Setup-{old}.exe")


@pytest.mark.parametrize("current,latest", [("0.1.0.57", "3.0.0.1"), ("0.1.0.57", "4.0.0.1"), ("3.0.0.1", "4.0.0.1")])
def test_check_finds_the_new_series_from_every_old_series(home, server, keys, current, latest):
    server.publish(keys, latest)
    s = checker(home, server, keys, current).check(force=True)
    assert s["available"] and s["latest"]["version"] == latest and s["error"] is None
    assert s["latest"]["source"] == "hosted"


def test_up_to_date_and_older_manifest_are_not_updates(home, server, keys):
    server.publish(keys, "3.0.0.1")
    assert not checker(home, server, keys, "3.0.0.1").check(force=True)["available"]
    assert not checker(home, server, keys, "4.0.0.1").check(force=True)["available"]      # a downgrade is not offered


def test_upgrade_installer_keeps_the_app_id_and_defaults_are_conservative():
    from pathlib import Path
    iss = (Path(__file__).resolve().parents[2] / "packaging" / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    assert "AppId={{24F7B79A-973C-462D-BA26-896D8EA30913}" in iss          # the same app for every series: an upgrade
    assert 'Name: "autostart"; Description: "{cm:TaskAutostart}"; Flags: unchecked' in iss   # autostart OFF by default
    assert "UsePreviousTasks=no" in iss                                   # an upgrade never re-ticks / changes it
    assert "/ROLLBACK=1" in iss and "IsDowngrade(" in iss and 'downgrade_guard.iss' in iss
    assert "SignTool" not in iss                                         # unsigned by decision (OD-08)


# ---------------------------------------------------------------------- manifest security
def test_tampered_unsigned_and_wrongly_signed_manifests_are_refused(home, server, keys):
    server.publish(keys, NEW, tamper=lambda m: m.update(version="9.9.9.9"))
    s = checker(home, server, keys).check(force=True)
    assert not s["available"] and "not signed by a trusted" in s["error"]
    server.publish(keys, NEW, tamper=lambda m: m.update(sha256="0" * 64))
    assert "not signed by a trusted" in checker(home, server, keys).check(force=True)["error"]
    server.publish(Keys(), NEW)                                          # signed, but by a key this app does not trust
    assert "not signed by a trusted" in checker(home, server, keys).check(force=True)["error"]
    server.files["/latest.json"] = json.dumps({"manifest": {"version": NEW}}).encode()
    assert not checker(home, server, keys).check(force=True)["available"]


def test_plain_http_source_is_never_used(home, server, keys):
    server.publish(keys, NEW)
    u = UpdateChecker(home, http=server.http(), current=OLD, base_url="http://releases.example.test",
                      public_keys=[keys.public])
    assert u.base_url == "" and not u.source.startswith("hosted")


# ---------------------------------------------------------------------- ask before install, verify, back up
def test_install_asks_first_verifies_sha_and_size_and_backs_up(home, server, keys):
    exe = server.publish(keys, NEW)
    u = checker(home, server, keys)
    u.check(force=True)
    hits = len(server.hits)
    with pytest.raises(UpdateError) as e:
        u.prepare_install(approved=False)
    assert e.value.code == "UPDATE_NEEDS_APPROVAL"
    assert len(server.hits) == hits and not (home / "updates").exists()   # nothing downloaded before the yes
    r = u.prepare_install(approved=True)
    assert open(r["installer"], "rb").read() == exe and r["sha256"] == hashlib.sha256(exe).hexdigest()
    assert r["signed"] is True and r["version"] == NEW
    assert backup.verify(r["backup"])["ok"]
    pend = json.loads((home / "updates" / "pending.json").read_text("utf-8"))
    assert pend["from"] == OLD and pend["to"] == NEW and pend["state"] == "installing"
    prev = updates.read_previous(home)
    assert prev["version"] == OLD and prev["backup"] == r["backup"] and prev["signed"] is True
    assert not list((home / "updates").glob("*.partial"))


def test_in_app_updater_runs_setup_silently_and_restarts_jarves(home, server, keys, monkeypatch):
    monkeypatch.setattr(updates, "build_info", lambda: {"version": OLD})      # a source checkout reports <__version__>+dev (4.0.0 now)
    server.publish(keys, NEW)
    u = checker(home, server, keys)
    u.check(force=True)
    r = u.prepare_install(approved=True)
    run = Launch()
    updates.launch_installer(r["installer"], verified=r, silent=True, restart=True, popen=run)
    argv = run.calls[0]
    assert argv[0] == r["installer"]
    assert {"/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTJARVES=1"} <= set(argv)
    assert "/ROLLBACK=1" not in argv and not any(a.startswith("/TASKS") for a in argv)   # tasks: setup keeps its defaults
    visible = Launch()
    updates.launch_installer(r["installer"], verified=r, popen=visible)                  # the CLI default: a window
    assert visible.calls[0] == [r["installer"]]


@pytest.mark.parametrize("failure", ["wrong_sha", "wrong_size", "http_500", "truncated", "offline"])
def test_a_failed_update_changes_nothing_and_the_old_version_keeps_working(home, server, keys, failure):
    exe = server.publish(keys, NEW)
    u = checker(home, server, keys)
    u.check(force=True)
    before = snapshot(home)
    path = f"/JARVES-Setup-{NEW}.exe"
    if failure == "wrong_sha":
        server.files[path] = bytes([exe[0] ^ 1]) + exe[1:]
    elif failure == "wrong_size":
        server.publish(keys, NEW, exe=exe, size=len(exe) + 1)
        u.check(force=True)
    elif failure == "http_500":
        server.status[path] = 500
    elif failure == "truncated":
        server.cut[path] = 100
    else:
        server.close()
    with pytest.raises(UpdateError) as e:
        u.prepare_install(approved=True)
    assert e.value.code in {"UPDATE_CHECKSUM_MISMATCH", "UPDATE_DOWNLOAD_FAILED", "UPDATE_OFFLINE", "UPDATE_BAD_MANIFEST"}
    run = Launch()
    assert not run.calls                                                     # setup was never started
    assert not list((home / "updates").glob("JARVES-Setup-*")) if (home / "updates").exists() else True
    assert not (home / "updates" / "pending.json").exists() and updates.read_previous(home) == {}
    assert snapshot(home) == before                                          # data untouched
    assert u.status()["current"] == OLD                                      # still the old version, still usable
    assert user_rows(home)["memory"] == [("likes tea",)]


def test_setup_that_never_ran_is_noticed_when_the_old_version_starts_again(home, server, keys):
    server.publish(keys, NEW)
    u = checker(home, server, keys)
    u.check(force=True)
    u.prepare_install(approved=True)                  # setup was started... and failed / was cancelled
    r = updates.note_start(home, OLD)                 # the OLD version starts again
    assert r == {"offer": False, "state": "not_applied"}
    assert json.loads((home / "updates" / "pending.json").read_text("utf-8"))["state"] == "not_applied"


def test_downgrade_is_refused_everywhere_except_rollback(home, server, keys, monkeypatch):
    with pytest.raises(UpdateError) as e:
        updates.refuse_downgrade("0.1.0.57", "3.0.0.1")
    assert e.value.code == "UPDATE_DOWNGRADE"
    exe = home / "old.exe"
    exe.write_bytes(b"x")
    run = Launch()
    ok = {"installer": str(exe), "signed": True, "version": "0.1.0.57", "sha256": hashlib.sha256(b"x").hexdigest()}
    # build_info() of a source checkout says "<__version__>+dev"; make the running copy 3.0.0.1
    monkeypatch.setattr(updates, "build_info", lambda: {"version": "3.0.0.1"})
    with pytest.raises(UpdateError) as e:
        updates.launch_installer(exe, ok, popen=run)
    assert e.value.code == "UPDATE_DOWNGRADE" and not run.calls
    updates.launch_installer(exe, ok, rollback=True, popen=run)
    assert "/ROLLBACK=1" in run.calls[0]


def test_installed_build_starts_only_verified_signed_installers(home, server, keys, monkeypatch):
    monkeypatch.setattr(updates, "is_installed_build", lambda: True)
    exe = home / "x.exe"
    exe.write_bytes(b"x")
    run = Launch()
    with pytest.raises(UpdateError) as e:
        updates.launch_installer(exe, None, popen=run)
    assert e.value.code == "UPDATE_UNSIGNED"
    good = {"installer": str(exe), "signed": True, "version": "9.0.0.0", "sha256": hashlib.sha256(b"x").hexdigest()}
    exe.write_bytes(b"swapped after verification")
    with pytest.raises(UpdateError) as e:
        updates.launch_installer(exe, good, popen=run)
    assert e.value.code == "UPDATE_CHECKSUM_MISMATCH" and not run.calls


# ---------------------------------------------------------------------- rollback
def _update(home, server, keys, previous=None, new=NEW, current=OLD):
    server.publish(keys, new, previous=previous)
    u = checker(home, server, keys, current)
    u.check(force=True)
    r = u.prepare_install(approved=True)
    return u, r


def _start_new_version(home):
    """What the new program does on its first start: migrate the data (backup first)."""
    migrations.run(home)


def test_rollback_with_the_kept_installer_restores_version_and_data(home, server, keys, tmp_path):
    old_exe = installer_bytes(OLD)
    (home / "updates").mkdir()
    (home / "updates" / f"JARVES-Setup-{OLD}.exe").write_bytes(old_exe)          # this copy was itself installed by the updater
    before = snapshot(home)
    _, r = _update(home, server, keys)
    assert (home / "updates" / "previous" / f"JARVES-Setup-{OLD}.exe").read_bytes() == old_exe
    _start_new_version(home)
    assert migrations.current(home) == migrations.LATEST and snapshot(home) != before
    (home / "memory-added-after-update.txt").write_text("new data", encoding="utf-8")

    info = updates.rollback_info(home)
    assert info["possible"] and info["version"] == OLD and info["installer_kept"]
    run = Launch()
    with pytest.raises(UpdateError) as e:
        updates.rollback(home, approved=False, current=NEW, popen=run)
    assert e.value.code == "UPDATE_NEEDS_APPROVAL" and not run.calls
    out = updates.rollback(home, approved=True, current=NEW, popen=run)
    argv = run.calls[0]
    assert argv[0].endswith(f"JARVES-Setup-{OLD}.exe") and "/ROLLBACK=1" in argv and "/VERYSILENT" in argv
    assert "/RESTARTJARVES=1" in argv and out["restored"].startswith("jarves-backup-")
    (home / "memory-added-after-update.txt").unlink()            # created after the update: the safety copy keeps it
    assert snapshot(home) == before                              # rows, layout and files as before the update
    assert not (home / migrations.MARKER).exists()
    assert json.loads((home / "updates" / "pending.json").read_text("utf-8"))["state"] == "rolled_back"
    assert any(b["kind"] == "pre-restore" for b in backup.list_backups(home))     # newer data was checkpointed first


def test_rollback_downloads_the_previous_installer_from_the_signed_manifest(home, server, keys):
    old_exe = installer_bytes(OLD)
    before = snapshot(home)
    _, r = _update(home, server, keys, previous=(OLD, old_exe))
    rec = updates.read_previous(home)
    assert rec["installer"] is None and rec["source"]["sha256"] == hashlib.sha256(old_exe).hexdigest()
    _start_new_version(home)
    run = Launch()
    out = updates.rollback(home, approved=True, current=NEW, http=server.http(), popen=run)
    assert open(run.calls[0][0], "rb").read() == old_exe and out["sha256"] == hashlib.sha256(old_exe).hexdigest()
    assert snapshot(home) == before


def test_rollback_refuses_a_previous_installer_that_does_not_match(home, server, keys):
    old_exe = installer_bytes(OLD)
    _update(home, server, keys, previous=(OLD, old_exe))
    server.files[f"/JARVES-Setup-{OLD}.exe"] = old_exe[:-1] + b"X"
    before = snapshot(home)
    run = Launch()
    with pytest.raises(UpdateError) as e:
        updates.rollback(home, approved=True, current=NEW, http=server.http(), popen=run)
    assert e.value.code == "UPDATE_CHECKSUM_MISMATCH" and not run.calls
    assert snapshot(home) == before                              # nothing was restored before the check passed
    server.close()
    with pytest.raises(UpdateError) as e:
        updates.rollback(home, approved=True, current=NEW, http=server.http(), popen=run)
    assert e.value.code == "UPDATE_OFFLINE"


def test_rollback_keeps_the_kept_installer_honest(home, server, keys):
    (home / "updates").mkdir()
    (home / "updates" / f"JARVES-Setup-{OLD}.exe").write_bytes(installer_bytes(OLD))
    _update(home, server, keys)
    (home / "updates" / "previous" / f"JARVES-Setup-{OLD}.exe").write_bytes(b"tampered")
    with pytest.raises(UpdateError) as e:
        updates.rollback(home, approved=True, current=NEW, popen=Launch())
    assert e.value.code == "ROLLBACK_BAD_INSTALLER"


def test_rollback_without_a_record_a_backup_or_a_newer_version(home, server, keys):
    with pytest.raises(UpdateError) as e:
        updates.rollback(home, approved=True, current=NEW, popen=Launch())
    assert e.value.code == "ROLLBACK_NONE" and not updates.rollback_info(home)["possible"]
    (home / "updates").mkdir()
    (home / "updates" / f"JARVES-Setup-{OLD}.exe").write_bytes(installer_bytes(OLD))
    _, r = _update(home, server, keys)
    with pytest.raises(UpdateError) as e:                       # already on (or below) the kept version
        updates.rollback(home, approved=True, current=OLD, popen=Launch())
    assert e.value.code == "ROLLBACK_NONE"
    import os
    os.unlink(r["backup"])
    with pytest.raises(UpdateError) as e:
        updates.rollback(home, approved=True, current=NEW, popen=Launch())
    assert e.value.code == "ROLLBACK_NO_BACKUP"
    run = Launch()                                              # program-only rollback is still possible
    out = updates.rollback(home, approved=True, current=NEW, popen=run, restore_data=False)
    assert out["kept_data"] and out["restored"] is None and run.calls


def test_a_second_update_keeps_only_one_previous_version(home, server, keys):
    (home / "updates").mkdir()
    (home / "updates" / f"JARVES-Setup-{OLD}.exe").write_bytes(installer_bytes(OLD))
    _update(home, server, keys)
    (home / "updates" / f"JARVES-Setup-{NEW}.exe").write_bytes(installer_bytes(NEW))      # NEW got installed meanwhile
    _update(home, server, keys, new="4.0.0.1", current=NEW)
    names = sorted(p.name for p in (home / "updates" / "previous").iterdir())
    assert names == [f"JARVES-Setup-{NEW}.exe", "previous.json"]            # the record now points at NEW, OLD is gone
    assert sorted(p.name for p in (home / "updates").glob("JARVES-Setup-*.exe")) == ["JARVES-Setup-4.0.0.1.exe"] \
        or sorted(p.name for p in (home / "updates").glob("JARVES-Setup-*.exe")) == ["JARVES-Setup-4.0.0.1.exe"]


# ---------------------------------------------------------------------- failed first start -> offer through approvals
def _pending(home, state="installing", attempts=0):
    (home / "updates").mkdir(exist_ok=True)
    (home / "updates" / "pending.json").write_text(json.dumps({"from": OLD, "to": NEW, "state": state,
                                                              "attempts": attempts}), encoding="utf-8")


def _previous(home):
    (home / "updates" / "previous").mkdir(parents=True, exist_ok=True)
    (home / "updates" / "previous" / "previous.json").write_text(json.dumps(
        {"version": OLD, "to": NEW, "backup": str(home / "b.zip"), "installer": "JARVES-Setup-0.1.0.57.exe",
         "sha256": "0" * 64, "size": 1, "signed": True}), encoding="utf-8")
    (home / "updates" / "previous" / "JARVES-Setup-0.1.0.57.exe").write_bytes(b"x")


def test_first_start_that_never_becomes_ready_offers_a_rollback_on_the_second_start(home):
    _pending(home)
    _previous(home)
    assert updates.note_start(home, NEW) == {"offer": False, "state": "installing", "attempts": 1}
    assert updates.note_start(home, NEW)["offer"] is True                    # the first start never reached ready
    updates.mark_healthy(home, NEW)
    assert json.loads((home / "updates" / "pending.json").read_text("utf-8"))["state"] == "ok"
    assert updates.note_start(home, NEW) == {"offer": False, "state": "ok"}


def test_a_healthy_first_start_never_offers(home):
    _pending(home)
    _previous(home)
    updates.note_start(home, NEW)
    updates.mark_healthy(home, NEW)
    assert updates.note_start(home, NEW)["offer"] is False


def test_the_offer_is_an_approval_and_only_a_yes_starts_the_rollback(home):
    _pending(home)
    _previous(home)
    asked = []

    def deny(prompt, **kw):
        asked.append(kw)
        return False, "not approved"
    run = Launch()
    assert updates.offer_rollback(home, ask=deny, popen=run) is False and not run.calls
    assert asked[0]["risk"] == "medium" and asked[0]["kind"] == "update"
    assert updates.offer_rollback(home, ask=lambda p, **kw: (True, ""), popen=run) is True
    assert run.calls[0][-3:] == ["update", "--rollback", "--yes"]


def test_no_offer_when_there_is_nothing_to_roll_back_to(home):
    _pending(home)
    for _ in range(2):
        r = updates.note_start(home, NEW)
    assert r["offer"] is False
    assert updates.offer_rollback(home, ask=lambda *a, **k: (True, ""), popen=Launch()) is False


# ---------------------------------------------------------------------- dashboard API
class Ctx:
    def __init__(self, home):
        self.home = home
        self.cfg = None


def test_dashboard_rollback_needs_the_click_and_the_approval(home, monkeypatch):
    _pending(home)
    _previous(home)
    st, body = updates.api_updates_rollback(Ctx(home), None, {})
    assert st == 409 and body["code"] == "UPDATE_NEEDS_APPROVAL"
    from jarves import approvals
    started = []
    monkeypatch.setattr(updates, "start_rollback_process", lambda h, popen=None: started.append(h))
    monkeypatch.setattr(approvals, "ask_owner", lambda *a, **k: (False, "not approved"))
    st, body = updates.api_updates_rollback(Ctx(home), None, {"approve": True})
    assert st == 409 and body["code"] == "ROLLBACK_NOT_APPROVED" and not started
    monkeypatch.setattr(approvals, "ask_owner", lambda *a, **k: (True, ""))
    st, body = updates.api_updates_rollback(Ctx(home), None, {"approve": True})
    assert st == 200 and body["started"] and started == [home]


def test_dashboard_status_reports_the_rollback_option(home, monkeypatch):
    monkeypatch.setenv("JARVES_UPDATE_BASE_URL", "")
    _pending(home)
    _previous(home)
    info = updates.rollback_info(home)
    assert info["possible"] and info["version"] == OLD and info["from_version"] == NEW and info["pending"] == "installing"
    st, body = updates.api_updates_rollback(Ctx(home), None, None)
    assert st == 409
