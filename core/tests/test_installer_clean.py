"""SW-V4 L clean-machine install checks WITHOUT a real install: the residue audit that packaging/smoke_windows.ps1 runs
(autostart OFF by default, nothing left behind, data kept unless asked), the first-run wizard with a FAKE autostart backend,
the seeded old-layout data used by the CI upgrade / rollback scenario, and the installer script's policy lines.
Temp dirs and fakes only: no installer is run, no real registry, PATH, Start menu or scheduled task is read or written."""
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from release_fakes import snapshot, user_rows

from jarves import migrations, updates

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "packaging"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"pkg_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


audit = _load("install_audit")
seed_legacy = _load("seed_legacy")


# ---------------------------------------------------------------------- the residue audit on fake machine states
def machine(**over):
    base = {"run": {"OneDrive": "C:\\od.exe"}, "path": "C:\\Tools;C:\\Python", "tasks": ["Backup"], "menu": False,
            "app": False, "uninstaller": False, "data": {}, "data_dir": False}
    base.update(over)
    return base


def installed_machine(before, **over):
    st = {**before, "menu": True, "app": True, "uninstaller": True, "data": {"config.toml": "aa"}, "data_dir": True}
    st.update(over)
    return st


def test_a_default_install_with_autostart_off_is_clean():
    before = machine()
    assert audit.installed_findings(before, installed_machine(before)) == []


def test_autostart_on_after_a_default_install_is_caught():
    before = machine()
    after = installed_machine(before, run={**before["run"], "JARVES": "pythonw.exe -m jarves run --background"})
    bad = audit.installed_findings(before, after)
    assert any("autostart is ON" in b for b in bad) and any("Run key changed" in b for b in bad)


def test_path_must_be_unchanged_or_gain_exactly_the_program_folder():
    before = machine()
    app = "C:\\Users\\x\\JARVES"
    assert audit.installed_findings(before, installed_machine(before, path=app + ";" + before["path"]), app) == []
    assert audit.installed_findings(before, installed_machine(before, path=app.upper() + ";" + before["path"]), app) == []
    assert audit.installed_findings(before, installed_machine(before, path=before["path"] + ";" + app))   # not asked for
    assert audit.installed_findings(before, installed_machine(before, path=before["path"] + ";C:\\Else"), app)
    assert audit.installed_findings(before, installed_machine(before, path="C:\\Tools"), app)               # entries lost


def test_install_must_leave_the_files_and_no_scheduled_task():
    before = machine()
    assert any("program folder" in b for b in audit.installed_findings(before, installed_machine(before, app=False)))
    assert any("uninstaller" in b for b in audit.installed_findings(before, installed_machine(before, uninstaller=False)))
    assert any("Start-menu" in b for b in audit.installed_findings(before, installed_machine(before, menu=False)))
    assert any("scheduled task" in b for b in audit.installed_findings(before, installed_machine(before,
                                                                                               tasks=["Backup", "JARVES keep-alive"])))


def test_uninstall_leaves_nothing_and_keeps_data_byte_for_byte_unless_purged():
    before = machine()
    data = {"config.toml": "aa", "store.db": "bb"}
    clean = machine(data=data, data_dir=True)
    assert audit.uninstalled_findings(before, clean, keep_data=True, data_before=data) == []
    assert audit.uninstalled_findings(before, machine(), keep_data=False, data_before=data) == []
    # residue of every kind is named
    dirty = machine(app=True, menu=True, run={**before["run"], "JARVES": "x"}, path=before["path"] + ";C:\\JARVES",
                    tasks=["Backup", "JARVES nightly"], data=data, data_dir=True)
    bad = " | ".join(audit.uninstalled_findings(before, dirty, keep_data=True, data_before=data))
    for needle in ("program files", "Start-menu", "autostart residue", "Run key", "PATH", "scheduled task"):
        assert needle in bad, needle
    # user data: changed, deleted, or kept when a purge was asked
    assert audit.uninstalled_findings(before, machine(data={"config.toml": "zz", "store.db": "bb"}, data_dir=True),
                                      keep_data=True, data_before=data)
    assert audit.uninstalled_findings(before, machine(data={}, data_dir=False), keep_data=True, data_before=data)
    assert any("purge" in b for b in audit.uninstalled_findings(before, clean, keep_data=False, data_before=data))


def test_collect_reads_through_injected_readers_only(tmp_path):
    app, home, menu = tmp_path / "app", tmp_path / "home", tmp_path / "menu"
    for d in (app, home, menu):
        d.mkdir()
    (app / "unins000.exe").write_bytes(b"u")
    (home / "config.toml").write_text("x", encoding="utf-8")
    st = audit.collect(app, home, read_run=lambda: {"A": "1"}, read_path=lambda: "C:\\p", list_tasks=lambda: ["T"],
                       start_menu=menu)
    assert st["run"] == {"A": "1"} and st["path"] == "C:\\p" and st["tasks"] == ["T"]
    assert st["menu"] and st["app"] and st["uninstaller"] and st["data_dir"]
    assert list(st["data"]) == ["config.toml"] and len(st["data"]["config.toml"]) == 64
    assert audit.collect(tmp_path / "none", tmp_path / "none2", read_run=dict, read_path=str, list_tasks=list,
                         start_menu=tmp_path / "nomenu")["app"] is False


def test_audit_cli_exit_codes(tmp_path, capsys):
    b = machine()
    f1, f2 = tmp_path / "b.json", tmp_path / "a.json"
    f1.write_text(json.dumps(b), encoding="utf-8")
    f2.write_text(json.dumps(installed_machine(b)), encoding="utf-8")
    assert audit.main(["installed", "--before", str(f1), "--after", str(f2)]) == 0
    f2.write_text(json.dumps(installed_machine(b, run={"JARVES": "x"})), encoding="utf-8")
    assert audit.main(["installed", "--before", str(f1), "--after", str(f2)]) == 1
    assert "INSTALL AUDIT (installed): FAIL" in capsys.readouterr().out


# ---------------------------------------------------------------------- first run on a fresh data folder, fake autostart
for _m in ("jarves.app", "jarves.operations", "jarves.runtime", "jarves.usage", "jarves.backup", "jarves.appearance",
           "jarves.skills.generated", "jarves.guides"):
    importlib.import_module(_m)

from jarves import features as feat  # noqa: E402
from jarves import setup_wizard as sw  # noqa: E402
from jarves.runtime import autostart  # noqa: E402


class FakeRunKey(dict):
    """The HKCU Run key as a dict (autostart.WindowsAutostart accepts any get/set/delete object)."""

    def get(self, name, default=None):
        return super().get(name, default)

    def set(self, name, value):
        self[name] = value

    def delete(self, name):
        return self.pop(name, None) is not None


def test_first_run_wizard_on_a_fresh_profile_leaves_autostart_off_and_finishes(tmp_path, monkeypatch):
    from jarves.config import load_config
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(feat.Features, "available", lambda self, fid: (True, ""))
    run_key = FakeRunKey()
    fake = autostart.WindowsAutostart(reg=run_key, command=["pythonw.exe", "-m", "jarves", "run", "--background"])
    cfg, _ = load_config(tmp_path / "config.toml")
    out = []

    def set_autostart(on):
        fake.enable() if on else fake.disable()
    assert sw.first_run(tmp_path)
    w = sw.Wizard(cfg, tmp_path, config_path=tmp_path / "config.toml", io=sw.ConsoleIO(None, out.append), defaults=True,
                  lang="en", features=feat.Features(cfg, tmp_path),
                  actions={"ask_key": lambda h: 1, "prepare": lambda fid, f, h: (True, ""), "google_start": lambda: 0,
                           "autostart": set_autostart})
    assert w.run() == 0 and not sw.first_run(tmp_path)
    assert run_key == {} and not fake.enabled()                                # autostart OFF by default
    assert not feat.Features(cfg, tmp_path).enabled("autostart")
    assert "What to do next" in "\n".join(out)


def test_autostart_is_a_user_choice_and_survives_nothing_but_off(tmp_path):
    run_key = FakeRunKey({"OneDrive": "x"})
    fake = autostart.WindowsAutostart(reg=run_key, command=["pythonw.exe", "-m", "jarves", "run", "--background"])
    assert not fake.enabled()
    fake.enable()
    assert fake.enabled() and run_key["JARVES"].endswith("--background") and run_key["OneDrive"] == "x"
    assert fake.disable() is True and run_key == {"OneDrive": "x"}               # the uninstaller's `autostart off`
    assert fake.disable() is False                                                # nothing to remove: not an error


def test_addon_check_wiring_reports_pass_and_fail_with_fake_bundles(monkeypatch):
    """`jarvis setup --check-addons` (run by smoke_windows.ps1 on the INSTALLED app): a missing part is a FAIL line."""
    monkeypatch.setattr(sw, "EXTRA_BUNDLE", {})
    good = sw.check_addons(bundle={"music": [lambda: (True, "ok")]})
    bad = sw.check_addons(bundle={"music": [lambda: (False, "part missing")]})
    assert good["ok"] and not bad["ok"]
    lines = []
    sw.print_check(bad, lines.append)
    assert any("FAIL" in x and "part missing" in x for x in lines) and lines[-1] == "ADDON CHECK: FAIL"


# ---------------------------------------------------------------------- the seeded data of the CI upgrade scenario
def test_ci_seed_is_a_frozen_old_layout_and_the_schema_constant_follows_the_code(tmp_path):
    assert seed_legacy.LATEST_SCHEMA == migrations.LATEST
    home = tmp_path / "home"
    seed_legacy.seed(home)
    assert migrations.current(home) == migrations.LEGACY
    assert seed_legacy.verify(home, legacy=True) == []
    assert seed_legacy.verify(home, legacy=False)                               # not migrated yet: reported
    from release_fakes import seed_legacy as fake_seed                          # same rows as the unit-test fixture
    other = tmp_path / "other"
    fake_seed(other)
    assert user_rows(home) == user_rows(other)


def test_ci_scenario_upgrade_then_rollback_with_the_ci_helper_scripts(tmp_path, monkeypatch):
    """The exact helper calls smoke_windows.ps1 -UpgradeFrom -Rollback makes, against the real migration and rollback code."""
    monkeypatch.setattr(sys, "platform", "win32")
    home = tmp_path / "home"
    seed_legacy.seed(home)
    old_exe = tmp_path / "old.exe"
    old_exe.write_bytes(b"MZ old installer")
    before = snapshot(home)
    migrations.run(home)                                                         # the new version's first start
    assert seed_legacy.verify(home, legacy=False) == []
    rec = seed_legacy.previous(home, old_exe, "0.1.0.57", "3.0.0.9")
    assert updates.rollback_info(home)["possible"] and rec["schema"] == 1
    calls = []
    out = updates.rollback(home, approved=True, current="3.0.0.9", popen=lambda argv, **kw: calls.append(argv))
    assert "/ROLLBACK=1" in calls[0] and out["restored"].startswith("jarves-backup-")
    assert seed_legacy.verify(home, legacy=True) == []
    assert snapshot(home) == before


# ---------------------------------------------------------------------- installer script policy (text level)
def test_uninstall_without_the_purge_switch_never_deletes_the_data_folder():
    iss = (PKG / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    step = iss.split("procedure CurUninstallStepChanged", 1)[1]
    silent = step.split("if UninstallSilent() then", 1)[1].split("else if MsgBox", 1)[0]
    assert "PurgeRequested()" in silent and "DelTree(Dir" in silent             # silent: only with /PURGE=1
    assert "IDYES" in step and "MB_DEFBUTTON2" in step                           # interactive: asks, default is No
    assert step.index("IsDataDirSafeToPurge(Dir)") < step.index("DelTree(")     # and the guard comes first
    assert 'Parameters: "/c """"{app}\\jarvis.cmd"" autostart off"""' in iss      # the uninstaller removes the Run entry
    assert "RemoveFromPath(ExpandConstant('{app}'))" in iss


def test_silent_updates_stop_the_core_first_and_restart_it_only_when_asked():
    iss = (PKG / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    prepare = iss.split("function PrepareToInstall", 1)[1].split("procedure RestoreProgramPart", 1)[0]
    assert '" stop' in prepare
    assert prepare.index('" stop') < prepare.index("ExtractTemporaryFile")
    assert "CloseApplications=yes" in iss
    assert "Check: RestartRequested" in iss and "/RESTARTJARVES=1" in iss
    assert "if WizardSilent() then exit;" in iss.split("procedure OpenSetup", 1)[1].split("procedure InitializeWizard", 1)[0]
    assert updates.installer_args("x.exe", silent=True, restart=True)[1:] == [
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTJARVES=1"]


def test_ci_runs_the_clean_install_upgrade_and_budget_steps():
    wf = (ROOT / ".github" / "workflows" / "jarves-checks.yml").read_text(encoding="utf-8")
    for needle in ("-DefaultTasks", "-MetricsOut dist/installer-metrics.json", "installer_metrics.py check",
                   "upgrade_matrix.ps1"):
        assert needle in wf, needle
    smoke = (PKG / "smoke_windows.ps1").read_text(encoding="utf-8-sig")
    for needle in ("$UpgradeFrom", "$Rollback", "update\", \"--rollback\", \"--yes\"", "install_audit.py", "seed_legacy.py",
                   "silent_upgrade_s", "silent_rollback_s"):
        assert needle in smoke, needle
    fixtures = json.loads((PKG / "upgrade_fixtures.json").read_text(encoding="utf-8"))
    assert isinstance(fixtures["fixtures"], list)
    for f in fixtures["fixtures"]:                                               # when pinned: complete and HTTPS
        assert f["url"].startswith("https://") and len(f["sha256"]) == 64 and updates.version_tuple(f["version"])


@pytest.mark.windows_only
def test_every_packaging_powershell_script_parses():
    import shutil
    import subprocess
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh, "PowerShell is part of every Windows runner"
    for name in ("smoke_windows.ps1", "build_windows.ps1", "upgrade_matrix.ps1"):
        cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
               f"'{PKG / name}',[ref]$t,[ref]$e); if ($e.Count) {{ $e | % {{ $_.Message }}; exit 1 }}")
        r = subprocess.run([pwsh, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, (name, r.stdout[-400:])
