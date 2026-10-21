"""SW-V4 L packaging checks: the pinned Go bridge in the payload, the `previous` block of the signed manifest, the installer
size/time budgets (hook for the M performance gate) and the compiled "never install over a newer version" guard."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "packaging"
sys.path.insert(0, str(ROOT / "core" / "tools"))


def _load(name):
    spec = importlib.util.spec_from_file_location(f"pkgl_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


payload = _load("payload_check")
release = _load("release")
metrics = _load("installer_metrics")

BRIDGE = b"MZ go whatsmeow bridge build 1"
SHA = hashlib.sha256(BRIDGE).hexdigest()


def stage_with_bridge(tmp_path, data=BRIDGE, name="jarves-whatsapp-bridge.exe"):
    stage = tmp_path / "stage"
    (stage / "bridge").mkdir(parents=True)
    if data is not None:
        (stage / "bridge" / name).write_bytes(data)
    return stage


def pin(tmp_path, sha=SHA, path="bridge/jarves-whatsapp-bridge.exe"):
    f = tmp_path / "bridge_pin.json"
    f.write_text(json.dumps({"path": path, "sha256": sha}), encoding="utf-8")
    return f


# ---------------------------------------------------------------------- the Go bridge in the payload
def test_no_pin_and_no_binary_means_the_bridge_is_simply_not_part_of_this_build(tmp_path):
    (tmp_path / "stage").mkdir()
    bad, status = payload.bridge_findings(tmp_path / "stage", tmp_path / "missing_pin.json")
    assert bad == [] and "not part of this build" in status
    assert Path(payload.BRIDGE_PIN).name == "bridge_pin.json"      # (main today: the file does not exist yet)


def test_a_bridge_binary_without_a_pin_is_a_finding(tmp_path):
    bad, status = payload.bridge_findings(stage_with_bridge(tmp_path), tmp_path / "missing_pin.json")
    assert bad and "without a pin" in bad[0] and status == "BRIDGE: unpinned"


def test_pinned_bridge_with_the_right_sha256_passes(tmp_path):
    bad, status = payload.bridge_findings(stage_with_bridge(tmp_path), pin(tmp_path))
    assert bad == [] and "verified" in status


@pytest.mark.parametrize("case", ["tampered", "missing", "extra_file", "bad_sha_in_pin", "path_escape", "wrong_dir"])
def test_bridge_pin_violations_fail_the_build(tmp_path, case):
    stage = stage_with_bridge(tmp_path, data=None if case == "missing" else (BRIDGE + b"!" if case == "tampered" else BRIDGE))
    p = pin(tmp_path)
    if case == "extra_file":
        (stage / "bridge" / "notes.txt").write_text("x")
    if case == "bad_sha_in_pin":
        p = pin(tmp_path, sha="zz")
    if case == "path_escape":
        p = pin(tmp_path, path="bridge/../python/python.exe")
    if case == "wrong_dir":
        p = pin(tmp_path, path="python/x.exe")
    bad, status = payload.bridge_findings(stage, p)
    assert bad, (case, status)


def test_payload_check_cli_reports_the_bridge_and_fails_on_a_mismatch(tmp_path, capsys):
    stage = stage_with_bridge(tmp_path)
    assert payload.main(["payload_check.py", str(stage), "--bridge-pin", str(pin(tmp_path))]) == 0
    assert "BRIDGE: bridge/jarves-whatsapp-bridge.exe sha256 verified" in capsys.readouterr().out
    assert payload.main(["payload_check.py", str(stage), "--bridge-pin", str(pin(tmp_path, sha="0" * 64))]) == 1
    assert "differs from the pin" in capsys.readouterr().out


def test_the_build_script_stages_the_pinned_bridge_and_the_check_runs_after_it():
    ps = (PKG / "build_windows.ps1").read_text(encoding="utf-8-sig")
    assert "bridge_pin.json" in ps and "JARVES_BRIDGE_EXE" in ps
    assert ps.index("JARVES_BRIDGE_EXE") < ps.rindex("payload_check.py")      # staged first, verified after
    assert "build_s" in ps


# ---------------------------------------------------------------------- previous version in the signed manifest
def release_info(tmp_path):
    exe = tmp_path / "JARVES-Setup-3.0.0.9.exe"
    exe.write_bytes(b"installer")
    return exe, {"version": "3.0.0.9", "channel": "main", "commit": "abc", "built": "2026-10-20T10:00:00Z"}


def gh_release(v, digest=True):
    name = f"JARVES-Setup-{v}.exe"
    return {"tag_name": f"win-v{v}", "draft": False, "prerelease": False,
            "assets": [{"name": name, "size": 1234, "digest": f"sha256:{'ab' * 32}" if digest else None},
                       {"name": name + ".sha256", "size": 90}]}


def test_previous_entry_is_the_newest_older_release_with_a_known_hash():
    rels = [gh_release("3.0.0.9"), gh_release("3.0.0.8"), gh_release("0.1.0.57"), gh_release("3.0.0.12")]
    p = release.previous_entry(rels, "win-v3.0.0.9", "o/r")
    assert p == {"version": "3.0.0.8", "installer": "JARVES-Setup-3.0.0.8.exe", "sha256": "ab" * 32, "size": 1234,
                 "url": "https://github.com/o/r/releases/download/win-v3.0.0.8/JARVES-Setup-3.0.0.8.exe"}
    assert release.previous_entry([gh_release("3.0.0.9")], "win-v3.0.0.9", "o/r") is None       # first release
    assert release.previous_entry([gh_release("3.0.0.8", digest=False)], "win-v3.0.0.9", "o/r") is None


def test_manifest_carries_previous_and_the_app_reads_it_only_when_well_formed(tmp_path):
    from jarves import updates
    exe, info = release_info(tmp_path)
    prev = release.previous_entry([gh_release("3.0.0.8")], "win-v3.0.0.9", "o/r")
    m = release.build_manifest(info, exe, "https://x/y.exe", previous=prev)
    assert m["previous"] == prev and "previous" not in release.build_manifest(info, exe, "https://x/y.exe")
    got = updates.release_from_manifest(m, "https://x", "0.1.0.57")
    assert got["previous"]["version"] == "3.0.0.8" and got["previous"]["url"].startswith("https://")
    for broken in ({**prev, "sha256": "short"}, {**prev, "url": "http://insecure/x.exe"}, {**prev, "size": 0},
                   {**prev, "installer": "evil.exe"}, "not a dict"):
        assert updates.release_from_manifest({**m, "previous": broken}, "https://x", "0.1.0.57")["previous"] is None
    # the signature covers it: changing `previous` after signing breaks verification
    from release_fakes import Keys
    keys = Keys()
    env = keys.sign(m)
    assert updates.verify_manifest(env, [keys.public])["previous"] == prev
    env["manifest"]["previous"]["sha256"] = "00" * 32
    with pytest.raises(updates.UpdateError):
        updates.verify_manifest(env, [keys.public])


# ---------------------------------------------------------------------- installer size / time budgets
def build_info(dist, size=198 * 1024 * 1024, **extra):
    dist.mkdir(exist_ok=True)
    (dist / "build-info.json").write_text(json.dumps({"installer": "JARVES-Setup-3.0.0.9.exe", "size": size, **extra}),
                                          encoding="utf-8")
    return dist


def test_measure_reads_size_and_the_timings_the_smoke_test_passes(tmp_path):
    dist = build_info(tmp_path / "dist", build_s=612.4)
    m = metrics.measure(dist, install_s=41.26, uninstall_s=9, upgrade_s=None)
    assert m == {"installer_size_mb": 198.0, "silent_install_s": 41.3, "silent_uninstall_s": 9.0, "build_s": 612.4}


def test_budget_file_has_the_installer_tables_with_strict_owner_and_loose_ci_ceilings():
    b = metrics.load_budgets()
    assert {"installer_size_mb", "silent_install_s", "silent_uninstall_s", "silent_upgrade_s", "silent_rollback_s",
            "build_s"} <= set(b)
    for name, v in b.items():
        assert v["owner_max"] <= v["ci_max"] and v["unit"] and v["note"], name
        assert "baseline" in v or v.get("baseline_pending") is True, name
    assert b["installer_size_mb"]["baseline"] == 198


def test_gate_passes_the_baseline_and_fails_when_the_installer_grows_or_gets_slow(tmp_path, capsys):
    good = {"installer_size_mb": 198.0, "silent_install_s": 60.0}
    rows = {r["metric"]: r["status"] for r in metrics.check(good, metrics.load_budgets(), "owner")}
    assert rows["installer.installer_size_mb"] == "ok" and rows["installer.silent_upgrade_s"] == "n/a"
    f = tmp_path / "m.json"
    f.write_text(json.dumps(good), encoding="utf-8")
    assert metrics.main(["check", "--metrics", str(f), "--class", "ci"]) == 0
    f.write_text(json.dumps({"installer_size_mb": 320.5}), encoding="utf-8")
    assert metrics.main(["check", "--metrics", str(f), "--class", "ci"]) == 1
    assert "INSTALLER BUDGET: FAIL installer.installer_size_mb" in capsys.readouterr().out
    f.write_text(json.dumps({"silent_install_s": 100.0}), encoding="utf-8")        # fine for CI (240), too slow for the owner PC (90)
    assert metrics.main(["check", "--metrics", str(f), "--class", "ci"]) == 0
    assert metrics.main(["check", "--metrics", str(f), "--class", "owner"]) == 1


def test_perf_gate_merges_the_installer_numbers(tmp_path, capsys):
    import perf_gate as pg
    base = {k: b["baseline"] for k, b in pg.load_budgets()["metrics"].items()}
    f, inst = tmp_path / "base.json", tmp_path / "inst.json"
    f.write_text(json.dumps(base), encoding="utf-8")
    inst.write_text(json.dumps({"installer_size_mb": 198.0}), encoding="utf-8")
    assert pg.main(["--from-json", str(f), "--class", "ci", "--installer-json", str(inst)]) == 0
    assert "installer.installer_size_mb" in capsys.readouterr().out
    inst.write_text(json.dumps({"installer_size_mb": 999.0}), encoding="utf-8")
    assert pg.main(["--from-json", str(f), "--class", "ci", "--installer-json", str(inst)]) == 1
    assert "PERF GATE FAILED: installer.installer_size_mb=999.0" in capsys.readouterr().out


# ---------------------------------------------------------------------- the compiled downgrade guard
HARNESS = r"""
[Setup]
AppName=JARVES downgrade guard test
AppVersion=1
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
OutputDir=@OUT@
OutputBaseFilename=downgrade_guard_test

[Code]
#include "@GUARD@"

function InitializeSetup(): Boolean;
var
  Cases, Res: TArrayOfString;
  I, P: Integer;
  Line: String;
begin
  LoadStringsFromFile(ExpandConstant('{param:CASES|}'), Cases);
  SetArrayLength(Res, GetArrayLength(Cases));
  for I := 0 to GetArrayLength(Cases) - 1 do
  begin
    Line := Cases[I];
    P := Pos('|', Line);
    if IsDowngrade(Copy(Line, 1, P - 1), Copy(Line, P + 1, 100)) then Res[I] := '1' else Res[I] := '0';
  end;
  SaveStringsToFile(ExpandConstant('{param:OUT|}'), Res, False);
  Result := False;      // never installs anything
end;
"""

CASES = {                      # "installed|candidate": is the candidate a downgrade?
    "3.0.0.1|0.1.0.57": "1", "4.0.0.1|3.0.0.9": "1", "0.1.0.10|0.1.0.9": "1", "3.0.0.12|3.0.0.9": "1",
    "0.1.0.57|3.0.0.1": "0", "3.0.0.9|4.0.0.1": "0", "0.1.0.9|0.1.0.10": "0", "3.0.0.9|3.0.0.12": "0",
    "3.0.0.9|3.0.0.9": "0",    # same version: a repair / reinstall is allowed
}


def test_downgrade_guard_script_is_wired_into_the_installer():
    guard = (PKG / "windows" / "downgrade_guard.iss").read_text(encoding="utf-8-sig")
    assert "function IsDowngrade(" in guard and "ComparePackedVersion" in guard and "StrToVersion" in guard
    iss = (PKG / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    code = iss.split("[Code]", 1)[1]
    assert code.index('#include "downgrade_guard.iss"') < code.index("function InitializeSetup")
    assert "{24F7B79A-973C-462D-BA26-896D8EA30913}_is1" in code and "DisplayVersion" in code
    assert "not ParamPresent('/ROLLBACK=1')" in code and "Result := False" in code


@pytest.mark.windows_only
def test_downgrade_guard_compares_versions_as_numbers_in_the_real_inno_compiler(tmp_path):
    """Compiles downgrade_guard.iss into a throw-away setup that only evaluates the cases (InitializeSetup returns False:
    nothing is installed)."""
    import subprocess
    sys.path.insert(0, str(ROOT / "core" / "tests"))
    import _tools
    (tmp_path / "cases.txt").write_text("\n".join(CASES), encoding="utf-8")
    src = tmp_path / "harness.iss"
    src.write_text(HARNESS.replace("@OUT@", str(tmp_path)).replace("@GUARD@", str(PKG / "windows" / "downgrade_guard.iss")),
                   encoding="utf-8")
    subprocess.run([str(_tools.iscc_exe()), "-q", str(src)], check=True, capture_output=True, timeout=120)
    subprocess.run([str(tmp_path / "downgrade_guard_test.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/CASES={tmp_path / 'cases.txt'}", f"/OUT={tmp_path / 'out.txt'}"], timeout=120)
    got = (tmp_path / "out.txt").read_text(encoding="utf-8").split()
    assert dict(zip(CASES, got)) == CASES
