"""Exercise the installer's actual backup/migration transaction against fake old data."""
import importlib.util
import json
from pathlib import Path

import pytest

from jarves import backup, migrations

PKG = Path(__file__).resolve().parents[2] / "packaging"

def load(name):
    spec = importlib.util.spec_from_file_location(name, PKG / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

installer = load("installer_state")
seed = load("seed_legacy")

def test_upgrade_verifies_backup_before_migration_and_preserves_settings(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    result = installer.prepare(home, state)
    assert not (home / migrations.MARKER).exists()
    assert len(result["backups"]) == 2
    assert all(backup.verify(Path(item["backup"]))["ok"] for item in result["backups"])
    installer.complete(home, state)
    assert seed.verify(home, legacy=False) == []
    assert (home / "config.toml").read_text() == seed.CONFIG


def test_corrupt_backup_blocks_migration(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    result = installer.prepare(home, state)
    Path(result["backups"][0]["backup"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="verifies"):
        installer.complete(home, state)
    assert seed.verify(home, legacy=True) == []


def test_failed_migration_restores_old_database_layout(tmp_path, monkeypatch):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    installer.prepare(home, state)
    def fail(target, **kwargs):
        (target / migrations.MARKER).write_text('{"schema": 999}')
        raise RuntimeError("fake migration failure")
    monkeypatch.setattr(migrations, "run", fail)
    with pytest.raises(RuntimeError):
        installer.complete(home, state)
    assert seed.verify(home, legacy=True) == []


def test_fresh_choice_keeps_original_data_models_and_credentials_in_rollback(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    installer.prepare(home, state)
    record = installer.complete(home, state, keep=False, language="he")
    previous = Path(record["previous_data"])
    assert seed.verify(previous, legacy=True) == []
    assert 'language = "he"' in (home / "config.toml").read_text()
    assert not (home / "secrets.env").exists()


def test_shim_retirement_is_limited_reversible_and_keeps_user_replacements(tmp_path):
    user, app = tmp_path / "user", tmp_path / "app"
    root = user / ".local/bin"
    root.mkdir(parents=True)
    shim = root / "jarvis.cmd"
    shim.write_bytes(b"@python -m jarves %*")
    other = root / "jarvis.exe"
    other.write_bytes(b"unrelated executable")
    state = tmp_path / "state.json"
    state.write_text("{}")
    assert len(installer.retire_shims(app, state, user=user, roaming=user / "roaming")) == 1
    assert not shim.exists() and other.exists()
    installer.restore_shims(state)
    assert shim.read_bytes() == b"@python -m jarves %*"
    installer.retire_shims(app, state, user=user, roaming=user / "roaming")
    shim.write_bytes(b"replacement from user")
    installer.restore_shims(state)
    assert shim.read_bytes() == b"replacement from user"


def test_setup_route_falls_back_until_registered(tmp_path):
    assert installer.setup_url("http://127.0.0.1/?t=private", tmp_path) == "http://127.0.0.1/?t=private"
    (tmp_path / "setup.js").write_text("PAGES.setup = legacyPage;")
    assert not installer.setup_url("http://127.0.0.1/", tmp_path).endswith("#/setup")
    (tmp_path / "wizard.js").write_text("PAGES.setup = pageTour;")
    assert installer.setup_url("http://127.0.0.1/?t=private", tmp_path).endswith("#/setup")


def test_protected_home_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        installer.safe_home(Path.home())


def test_offline_rollback_restores_verified_legacy_data(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state.json"
    seed.seed(home)
    installer.prepare(home, state)
    installer.complete(home, state)
    installer.restore_data(home, state)
    assert seed.verify(home, legacy=True) == []


@pytest.mark.windows_only
def test_upgrade_matrix_forwards_named_metrics_and_switches(tmp_path):
    import hashlib
    import shutil
    import subprocess
    cache = tmp_path / "cache"
    cache.mkdir()
    fixture = cache / "JARVES-Setup-4.0.0.153.exe"
    fixture.write_bytes(b"fake fixture; never executed")
    (tmp_path / "upgrade_fixtures.json").write_text(json.dumps({"fixtures": [{
        "series": "fake", "version": "4.0.0.153", "url": "https://invalid.example/unused",
        "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()}]}))
    shutil.copyfile(PKG / "upgrade_matrix.ps1", tmp_path / "upgrade_matrix.ps1")
    (tmp_path / "smoke_windows.ps1").write_text('''param([string]$Installer, [string]$Version, [string]$Work = "",
        [string]$UpgradeFrom, [switch]$Rollback, [switch]$SkipDoctor, [string]$MetricsOut)
if ($Work -or -not $Rollback -or -not $SkipDoctor -or -not $MetricsOut) { throw "wrong argument binding" }
@{version=$Version; old=$UpgradeFrom; candidate=$Installer} | ConvertTo-Json | Set-Content $MetricsOut
exit 0
''')
    metrics = tmp_path / "metrics"
    subprocess.run(["pwsh", "-NoProfile", "-File", str(tmp_path / "upgrade_matrix.ps1"),
                    "-Candidate", str(tmp_path / "candidate.exe"), "-Version", "4.0.1.1", "-Cache", str(cache),
                    "-MetricsDir", str(metrics), "-SkipDoctor"], check=True, capture_output=True, text=True)
    report = json.loads((metrics / "upgrade-4.0.0.153-metrics.json").read_text(encoding="utf-8-sig"))
    assert report["old"] == str(fixture) and report["version"] == "4.0.1.1"


def _pending(home, state):
    (home / "updates").mkdir(parents=True, exist_ok=True)
    (home / "updates" / "pending.json").write_text(json.dumps({"state": state, "from": "4.0.0", "to": "4.0.1"}))


def test_health_check_is_ok_when_the_core_marks_the_update_healthy(tmp_path):
    home = tmp_path / "home"
    _pending(home, "installing")
    ticks = []
    def sleep(_):
        ticks.append(1)
        _pending(home, "ok")                      # the new Core reached CORE_READY meanwhile
    assert installer.health(home, 30, 0, sleep=sleep) == "ok" and ticks == [1]


def test_health_check_fails_only_for_an_installing_update_that_never_becomes_ready(tmp_path):
    home = tmp_path / "home"
    _pending(home, "installing")
    now = [0.0]
    def sleep(s): now[0] += 10
    assert installer.health(home, 30, 1, sleep=sleep, clock=lambda: now[0]) == "failed"


def test_health_check_never_judges_a_manual_install_or_a_not_applied_update(tmp_path):
    home = tmp_path / "home"
    assert installer.health(home, 1) == "unknown"                 # no update record at all
    _pending(home, "not_applied")
    assert installer.health(home, 1) == "unknown"


def test_note_rollback_records_once_and_never_touches_data(tmp_path):
    home = tmp_path / "home"
    seed.seed(home)
    before = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
    _pending(home, "installing")
    before[home / "updates" / "pending.json"] = (home / "updates" / "pending.json").read_bytes()
    rec = installer.note_rollback(home, "rolled_back")
    assert rec["status"] == "rolled_back" and rec["from"] == "4.0.0" and rec["to"] == "4.0.1"
    after = {p: p.read_bytes() for p in home.rglob("*") if p.is_file() and p.name != "health.json"}
    assert after == before
    assert installer.note_rollback(home, "rollback_failed")["status"] == "rollback_failed"
    assert installer.note_rollback(home, "health_failed")["status"] == "health_failed"
    import pytest
    with pytest.raises(ValueError):
        installer.note_rollback(home, "anything")


def test_health_check_ignores_a_stale_record_for_another_version(tmp_path):
    """Review P2-1: pending.json says to=4.0.1 but this setup installs 4.0.2 (a stale record from an aborted attempt):
    the healthy 4.0.2 never marks that record ok, so judging it would roll back a good version. It is "unknown"."""
    home = tmp_path / "home"
    _pending(home, "installing")
    assert installer.health(home, 1, 0, expect="4.0.2", sleep=lambda _: None, clock=lambda: 99.0) == "unknown"
    now = [0.0]
    def sleep(s): now[0] += 10
    assert installer.health(home, 30, 1, expect="4.0.1.0", sleep=sleep, clock=lambda: now[0]) == "failed"
    assert installer.main(["health", "--home", str(home), "--timeout", "1", "--expect", "9.9.9"]) == 1


def _rollback_state(tmp_path, home, old_latest=3, **extra):
    state = tmp_path / "install-state.json"
    rec = {"home": str(home), "backups": [], **({"old_latest": old_latest} if old_latest is not None else {}), **extra}
    state.write_text(json.dumps(rec), encoding="utf-8")
    return state


def _stamp(profile, n):
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "schema.json").write_text(json.dumps({"schema": n}), encoding="utf-8")


def test_a_program_only_rollback_is_allowed_while_every_schema_is_one_the_old_program_knows(tmp_path):
    """Review #260 F1: the rule is "schema now <= the OLD program's LATEST", not "unchanged": a legacy 1 -> 3 or an
    empty 0 -> 3 stamp is safe when the old program knows 3; 3 -> 4 is not; unknown is never safe."""
    home = tmp_path / "home"
    seed.seed(home)                                     # legacy layout: no schema.json, databases present (1)
    assert installer.schema_of(home) == 1
    state = _rollback_state(tmp_path, home)
    assert installer.can_program_rollback(state) is True
    for p in installer.profiles(home):
        _stamp(p, 3)                                    # what `complete` does: legacy 1 -> 3, empty 0 -> 3
    assert installer.can_program_rollback(state) is True
    assert installer.main(["can-program-rollback", "--home", str(home), "--state", str(state)]) == 0
    _stamp(home / "profiles" / "never-started", 3)      # a profile created later, also checked
    assert installer.can_program_rollback(state) is True
    _stamp(home / "profiles" / "never-started", 4)      # newer than the old program knows
    assert installer.can_program_rollback(state) is False
    assert installer.main(["can-program-rollback", "--home", str(home), "--state", str(state)]) == 5
    _stamp(home / "profiles" / "never-started", 3)
    (home / "schema.json").write_text("{", encoding="utf-8")           # unreadable marker
    assert installer.can_program_rollback(state) is False
    _stamp(home, 3)
    assert installer.can_program_rollback(_rollback_state(tmp_path, home, old_latest=None)) is False
    assert installer.can_program_rollback(_rollback_state(tmp_path, home, old_latest="3")) is False
    assert installer.can_program_rollback(_rollback_state(tmp_path, home, previous_data="x")) is False
    assert installer.can_program_rollback(tmp_path / "missing.json") is False


def test_prepare_records_the_old_programs_latest_schema(tmp_path):
    home = tmp_path / "home"
    seed.seed(home)
    rec = installer.prepare(home, tmp_path / "state.json")
    assert rec["old_latest"] == migrations.LATEST and all("schema_before" in b for b in rec["backups"])


def test_schema_of_follows_the_migrations_rule(tmp_path):
    from jarves import migrations
    home = tmp_path / "p"
    home.mkdir()
    assert installer.schema_of(home) == migrations.current(home) == 0
    (home / "store.db").write_bytes(b"")
    assert installer.schema_of(home) == migrations.current(home) == migrations.LEGACY
    (home / "schema.json").write_text(json.dumps({"schema": 3}), encoding="utf-8")
    assert installer.schema_of(home) == migrations.current(home) == 3
    (home / "schema.json").write_text("{", encoding="utf-8")
    assert installer.schema_of(home) is None


def test_disk_check_counts_only_backed_up_data_and_adds_both_needs_on_one_volume(tmp_path):
    home, app = tmp_path / "home", tmp_path / "app"
    (home / "models").mkdir(parents=True)
    (home / "models" / "big.bin").write_bytes(b"x" * 50_000_000)          # not backed up: must not count
    (home / "memory.db").write_bytes(b"y" * 2_000_000)
    (app / "python").mkdir(parents=True)
    (app / "python" / "python.exe").write_bytes(b"z" * 4_000_000)
    MB = 1024 * 1024
    need_data = int(2_000_000 * 1.15) + 64 * MB
    need_app = int(4_000_000 * 1.25) + 64 * MB
    class U:
        free = need_data + need_app + 10 * MB
        def __call__(self, _):
            return type("D", (), {"free": self.free})()
    u = U()
    assert installer.disk_check(home, app, usage=u) == {"ok": True, "drive": "", "short_mb": 0, "need_mb": 0}
    u.free = need_data + need_app - 5 * MB                                    # same volume: needs are added
    r = installer.disk_check(home, app, usage=u)
    assert r["ok"] is False and r["short_mb"] == 5 and r["need_mb"] >= 130 and r["drive"]
    assert "\\" not in str(r.get("drive", "x")[-1:]) and set(r) == {"ok", "drive", "short_mb", "need_mb"}
    u.free = need_data + need_app + 10 * MB
    assert installer.disk_check(home, app, reserve_mb=100, usage=u)["ok"] is False   # /DISKRESERVEMB= is stricter only


def test_disk_check_never_blocks_on_a_failed_probe_and_cli_writes_numbers_only(tmp_path, monkeypatch):
    home, app = tmp_path / "home", tmp_path / "app"
    home.mkdir()
    def boom(_):
        raise OSError("no")
    assert installer.disk_check(home, app, usage=boom)["ok"] is True
    monkeypatch.setattr(installer.shutil, "disk_usage", lambda _: type("D", (), {"free": 0})())
    out = tmp_path / "disk.txt"
    code = installer.main(["diskcheck", "--home", str(home), "--app", str(app), "--out", str(out)])
    lines = out.read_text("ascii").splitlines()
    assert code == 4 and [x.split("=")[0] for x in lines] == ["drive", "short_mb", "need_mb"]
    assert all(str(tmp_path) not in x for x in lines)


NOT_RUNNING = lambda: False   # noqa: E731


def _dead_upgrade(tmp_path):
    """A migrated upgrade whose new program is dead: data at schema 3, old program kept in app/rollback, health_failed."""
    home, state, app = tmp_path / "home", tmp_path / "app" / "install-state.json", tmp_path / "app"
    app.mkdir()
    seed.seed(home)
    installer.prepare(home, state)
    installer.complete(home, state)
    for part, old, new in (("python", "OLD-PY", "NEW-BROKEN-PY"), ("face", "OLD-FACE", "NEW-FACE")):
        (app / "rollback" / part).mkdir(parents=True)
        (app / "rollback" / part / "v.txt").write_text(old)
        (app / part).mkdir()
        (app / part / "v.txt").write_text(new)
    installer.note_rollback(home, "health_failed")
    return home, state, app


def test_recover_after_health_failed_restores_data_and_old_program_only_with_approval(tmp_path):
    """#261: no VM needed: the whole transaction is plain files. Declined = nothing changes."""
    home, state, app = _dead_upgrade(tmp_path)
    assert installer.recover(app, state, approved=False, ask=lambda _: "no", running=NOT_RUNNING) == "declined"
    assert seed.verify(home, legacy=False) == [] and (app / "python" / "v.txt").read_text() == "NEW-BROKEN-PY"
    assert installer.recover(app, state, approved=False, ask=lambda _: "YES", running=NOT_RUNNING) == "recovered"
    assert seed.verify(home, legacy=True) == []                        # data back to the verified pre-update backup
    assert (app / "python" / "v.txt").read_text() == "OLD-PY" and (app / "face" / "v.txt").read_text() == "OLD-FACE"
    kept = list(app.glob("failed-recovery-*"))
    assert len(kept) == 1 and (kept[0] / "python" / "v.txt").read_text() == "NEW-BROKEN-PY"   # nothing deleted
    assert installer._read_json(home / "updates" / installer.HEALTH_FILE)["status"] == "rolled_back"


def test_recover_refuses_a_healthy_install_and_a_bad_backup(tmp_path):
    home, state, app = _dead_upgrade(tmp_path)
    installer.note_rollback(home, "rolled_back")
    assert installer.recover(app, state, approved=True, running=NOT_RUNNING) == "not_needed"
    assert (app / "python" / "v.txt").read_text() == "NEW-BROKEN-PY"
    installer.note_rollback(home, "health_failed")
    record = json.loads(state.read_text())
    Path(record["backups"][0]["backup"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="verifies"):
        installer.recover(app, state, approved=True, running=NOT_RUNNING)
    assert (app / "python" / "v.txt").read_text() == "NEW-BROKEN-PY"      # the program was not touched either


def test_recover_cli_exit_codes(tmp_path, monkeypatch):
    home, state, app = _dead_upgrade(tmp_path)
    monkeypatch.setattr(installer, "core_running", NOT_RUNNING)
    assert installer.main(["recover", "--app", str(app), "--state", str(state), "--yes"]) == 0
    assert installer.main(["recover", "--app", str(app), "--state", str(state), "--yes"]) == 6    # nothing left to recover


def test_setup_offers_the_recovery_entry_only_while_the_previous_program_is_kept():
    """#261: the Start-menu entry runs the OLD python with the recover action and is gated on the kept python existing."""
    iss = (PKG / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    line = next(x for x in iss.splitlines() if x.startswith(r'Name: "{group}\Recover previous JARVES'))
    assert r"rollback\python\python.exe" in line and " recover " in line and "Check: PreviousProgramKept" in line
    assert "function PreviousProgramKept" in iss


def test_recover_refuses_while_a_core_runs_and_without_args(tmp_path):
    home, state, app = _dead_upgrade(tmp_path)
    assert installer.recover(app, state, approved=True, running=lambda: True) == "core_running"
    assert (app / "python" / "v.txt").read_text() == "NEW-BROKEN-PY"
    with pytest.raises(SystemExit):
        installer.main(["recover"])


def test_core_running_reads_the_os_lock(tmp_path):
    assert installer.core_running(tmp_path / "none.lock") is False
    lock = tmp_path / "core.lock"
    lock.write_bytes(b"x" + b" " * 20)
    assert installer.core_running(lock) is False                       # nobody holds it
    import os
    import sys
    fd = os.open(str(lock), os.O_RDWR)
    try:
        if sys.platform == "win32":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert installer.core_running(lock) is True
    finally:
        os.close(fd)


def test_recover_undoes_the_program_swap_when_the_data_restore_fails(tmp_path, monkeypatch):
    """Review #261: restore after the swap fails -> program put back as it was, retry possible."""
    home, state, app = _dead_upgrade(tmp_path)

    def boom(h, s):
        raise ValueError("restore failed")
    monkeypatch.setattr(installer, "restore_data", boom)
    with pytest.raises(ValueError):
        installer.recover(app, state, approved=True, running=NOT_RUNNING)
    assert (app / "python" / "v.txt").read_text() == "NEW-BROKEN-PY" and (app / "rollback" / "python" / "v.txt").read_text() == "OLD-PY"
    assert (app / "face" / "v.txt").read_text() == "NEW-FACE" and (app / "rollback" / "face" / "v.txt").read_text() == "OLD-FACE"
    monkeypatch.undo()
    assert installer.recover(app, state, approved=True, running=NOT_RUNNING) == "recovered"      # retry works


def test_recover_never_leaves_a_hole_when_a_rename_fails_midway(tmp_path, monkeypatch):
    home, state, app = _dead_upgrade(tmp_path)
    real = Path.rename
    calls = {"n": 0}

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] == 3:                                              # python moved aside, then rollback\python -> python
            raise PermissionError("locked")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", flaky)
    assert installer.recover(app, state, approved=True, running=NOT_RUNNING) == "swap_failed"
    monkeypatch.undo()
    assert (app / "python" / "v.txt").read_text() == "NEW-BROKEN-PY" and (app / "rollback" / "python" / "v.txt").read_text() == "OLD-PY"
    assert seed.verify(home, legacy=False) == []                         # data untouched


def test_relaunch_hands_off_to_a_detached_child_and_does_not_wait(tmp_path, monkeypatch):
    """Review #261: the parent runs from app/rollback/python (locked exe); it must start the temp-copy child and return,
    never wait for it, and the child must be told to wait for the parent's pid before it swaps."""
    import os
    import sys
    from types import SimpleNamespace
    home, state, app = _dead_upgrade(tmp_path)
    exe = app / "rollback" / "python" / "python.exe"
    exe.write_text("fake exe")
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(installer, "core_running", NOT_RUNNING)
    calls = []
    args = SimpleNamespace(app=app, state=state, home=None, yes=False)
    assert installer._relaunch_from_copy(args, spawn=lambda cmd, **kw: calls.append((cmd, kw)), ask=lambda _: "no") == 6
    assert calls == []                                                    # declined: nothing started
    rc = installer._relaunch_from_copy(args, spawn=lambda cmd, **kw: calls.append((cmd, kw)), ask=lambda _: "YES")
    assert rc == 0 and len(calls) == 1
    cmd, kw = calls[0]
    assert "rollback" not in cmd[0] and Path(cmd[0]).name == "python.exe"          # runs from the temp copy
    assert cmd[cmd.index("--wait-pid") + 1] == str(os.getpid()) and "--yes" in cmd and "recover" in cmd
    if os.name == "nt":
        import subprocess
        assert kw["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP and kw["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "python.exe"))
    assert installer._relaunch_from_copy(args, spawn=lambda *a, **k: calls.append(1)) is None   # not inside rollback
    assert len(calls) == 1


def test_wait_for_exit_polls_until_the_launcher_is_gone():
    seq = iter([True, True, False])
    slept = []
    assert installer.wait_for_exit(123, alive=lambda p: next(seq), sleep=slept.append) is True and len(slept) == 2
    assert installer.wait_for_exit(123, 0.0, alive=lambda p: True, sleep=lambda s: None) is False
    assert installer._pid_alive(0) is False
