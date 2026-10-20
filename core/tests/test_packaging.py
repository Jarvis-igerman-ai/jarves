"""M6 packaging: retention keeps 3 (+ pinned), publish verification, payload secret scan, installer script policy.
The real build + silent install/uninstall runs in CI (jarves-checks `installer` job, packaging/smoke_windows.ps1)."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "packaging"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"pkg_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


release = _load("release")
payload = _load("payload_check")


def rel(v, **kw):
    return {"id": hash(v) & 0xffff, "tag_name": v if not v[0].isdigit() else f"win-v{v}", "name": f"JARVES {v}",
            "body": "", "draft": False, "created_at": f"2026-10-{10 + int(v.split('.')[-1]) % 20:02d}", **kw}


def test_keeps_newest_three_by_version_not_by_name_order():
    rs = [rel("0.1.0.9"), rel("0.1.0.10"), rel("0.1.0.8"), rel("0.1.0.11"), rel("0.2.0.1")]
    gone = [r["tag_name"] for r in release.releases_to_prune(rs, 3)]
    assert gone == ["win-v0.1.0.9", "win-v0.1.0.8"]


def test_pinned_drafts_and_other_releases_are_never_pruned_or_counted():
    rs = [rel("0.1.0.1", body="rollback [pinned]"), rel("0.1.0.2", draft=True), rel("pi-v0.1.0.3"),
          rel("v1.0"), rel("0.1.0.4"), rel("0.1.0.5"), rel("0.1.0.6"), rel("0.1.0.7")]
    gone = [r["tag_name"] for r in release.releases_to_prune(rs, 3)]
    assert gone == ["win-v0.1.0.4"]


def test_artifact_retention_only_touches_its_own_name():
    arts = [{"id": i, "name": release.ARTIFACT, "created_at": f"2026-10-1{i}", "expired": False} for i in range(5)]
    arts += [{"id": 99, "name": "jarves-windows-installer-pr", "created_at": "2026-10-01"},
             {"id": 98, "name": "coverage", "created_at": "2026-09-01"}]
    assert [a["id"] for a in release.artifacts_to_prune(arts, 3)] == [1, 0]


def _dist(tmp_path, commit="abc", channel="main", tamper=False):
    exe = tmp_path / "JARVES-Setup-0.1.0.7.exe"
    exe.write_bytes(b"installer")
    sha = hashlib.sha256(b"installer").hexdigest()
    (tmp_path / (exe.name + ".sha256")).write_text(f"{sha}  {exe.name}\n", encoding="ascii")
    (tmp_path / "build-info.json").write_text(json.dumps({
        "version": "0.1.0.7", "commit": commit, "channel": channel, "built": "2026-10-19T10:00:00Z",
        "installer": exe.name, "sha256": "0" * 64 if tamper else sha, "size": 9, "python": "3.12", "godot": "4.7.2"}))
    return tmp_path


def test_verify_accepts_only_the_tested_main_build(tmp_path):
    assert release.verify(_dist(tmp_path), "abc")["version"] == "0.1.0.7"
    with pytest.raises(SystemExit, match="tested"):
        release.verify(_dist(tmp_path), "other-commit")
    with pytest.raises(SystemExit, match="never published"):
        release.verify(_dist(tmp_path, channel="pr"), "abc")
    with pytest.raises(SystemExit, match="SHA256"):
        release.verify(_dist(tmp_path, tamper=True), "abc")


def test_release_notes_carry_provenance_and_unsigned_warning(tmp_path):
    info = release.verify(_dist(tmp_path), "abc")
    text = release.notes(info, "https://github.com/x/actions/runs/1")
    for needle in ("abc", "2026-10-19T10:00:00Z", info["sha256"], "Not code-signed", "SmartScreen", "[pinned]"):
        assert needle in text


def test_payload_check_finds_personal_files_and_keys(tmp_path):
    (tmp_path / "python/Lib/site-packages/jarves").mkdir(parents=True)
    (tmp_path / "python/Lib/site-packages/certifi").mkdir(parents=True)
    (tmp_path / "python/Lib/site-packages/certifi/cacert.pem").write_text("public CAs")
    (tmp_path / "jarvis.cmd").write_text("@echo off")
    assert payload.findings(tmp_path) == []
    (tmp_path / "secrets.env").write_text("x")
    (tmp_path / "memory.db").write_text("x")
    (tmp_path / "logs").mkdir()
    (tmp_path / "python/Lib/site-packages/jarves/leak.py").write_text("KEY = 'sk-proj-" + "a" * 30 + "'")
    found = "\n".join(payload.findings(tmp_path))
    for needle in ("secrets.env", "memory.db", "folder: logs", "secret-like content", "leak.py"):
        assert needle in found


def test_installer_script_policy():
    iss = (PKG / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    assert "PrivilegesRequired=lowest" in iss                 # per user, no admin
    assert "DefaultDirName={autopf}\\JARVES" in iss           # %LOCALAPPDATA%\Programs with lowest privileges
    assert "/PURGE=1" in iss and "MB_DEFBUTTON2" in iss       # data kept unless asked (default button = No)
    assert "skipifsilent" in iss                              # no wizard/browser during silent installs
    assert "{localappdata}\\jarves" in iss                    # the data dir is outside {app}
    assert "SignTool" not in iss                              # unsigned until a real certificate exists
    launcher = (PKG / "windows" / "jarvis.cmd").read_text(encoding="utf-8")
    assert "-I -X utf8 -m jarves" in launcher and "JARVES_FACE_EXE" in launcher


def test_uninstall_purge_is_guarded():
    """Review #11: /PURGE=1 ran DelTree on whatever JARVES_HOME said. Every DelTree now sits behind the guard."""
    iss = (PKG / "windows" / "jarves.iss").read_text(encoding="utf-8-sig")
    code = iss.split("[Code]", 1)[1]
    assert '#include "purge_guard.iss"' in code
    guard_at = code.index("if not IsDataDirSafeToPurge(Dir) then")
    assert code.count("DelTree(") == 2 and all(code.index("DelTree(", i) > guard_at
                                                for i in range(len(code)) if code.startswith("DelTree(", i))
    guard = (PKG / "windows" / "purge_guard.iss").read_text(encoding="utf-8-sig")
    for marker in ("config.toml", "store.db", "memory.db"):
        assert f"'{marker}'" in guard
    for protected in ("USERPROFILE", "{localappdata}", "{userdocs}", "{win}", "ExtractFileDrive"):
        assert protected in guard


def _iscc() -> Path:
    import _tools
    return _tools.iscc_exe()                  # finds it, or installs the pinned portable Inno Setup (Windows)


HARNESS = r"""
[Setup]
AppName=JARVES purge guard test
AppVersion=1
CreateAppDir=no
Uninstallable=no
PrivilegesRequired=lowest
OutputDir=@OUT@
OutputBaseFilename=purge_guard_test

[Code]
#include "@GUARD@"

function InitializeSetup(): Boolean;
var
  Cases, Res: TArrayOfString;
  I: Integer;
begin
  LoadStringsFromFile(ExpandConstant('{param:CASES|}'), Cases);
  SetArrayLength(Res, GetArrayLength(Cases));
  for I := 0 to GetArrayLength(Cases) - 1 do
    if IsDataDirSafeToPurge(Cases[I]) then Res[I] := '1' else Res[I] := '0';
  SaveStringsToFile(ExpandConstant('{param:OUT|}'), Res, False);
  Result := False;      // never installs anything
end;
"""


def test_purge_guard_script_is_self_contained_pascal():
    """Every OS: the guard has the three entry points the harness and the installer use (the compiled run is the
    Windows-only test below, defined only on Windows because Inno Setup does not run elsewhere)."""
    guard = (PKG / "windows" / "purge_guard.iss").read_text(encoding="utf-8-sig")
    assert "function IsDataDirSafeToPurge(" in guard


@pytest.mark.windows_only
def test_purge_guard_refuses_roots_profile_and_non_jarves_folders(tmp_path):
    """Compiles purge_guard.iss into a throw-away setup that only evaluates the guard (InitializeSetup returns False:
    nothing is installed or deleted), with USERPROFILE pointed at a fake profile that DOES hold a marker file."""
    import os
    import subprocess
    profile = tmp_path / "users" / "me"
    cases = {}
    for name, files, want in (("cfg", ["config.toml"], "1"), ("store", ["store.db"], "1"), ("mem", ["memory.db"], "1"),
                              ("empty", [], "0"), ("other", ["photo.jpg", "notes.txt"], "0")):
        d = tmp_path / "homes" / name
        d.mkdir(parents=True)
        for f in files:
            (d / f).write_text("x")
        cases[str(d)] = want
        cases[str(d) + "\\"] = want
    profile.mkdir(parents=True)
    (profile / "config.toml").write_text("x")
    (tmp_path / "users" / "memory.db").write_text("x")
    cases[str(profile)] = "0"                          # the profile itself, even with a marker in it
    cases[str(tmp_path / "users")] = "0"               # a parent of the profile
    drive = str(tmp_path)[:2]
    cases.update({drive + "\\": "0", drive: "0", "": "0", "   ": "0"})
    (tmp_path / "cases.txt").write_text("\n".join(cases), encoding="utf-8")
    src = tmp_path / "harness.iss"
    src.write_text(HARNESS.replace("@OUT@", str(tmp_path)).replace("@GUARD@", str(PKG / "windows" / "purge_guard.iss")),
                   encoding="utf-8")
    subprocess.run([str(_iscc()), "-q", str(src)], check=True, capture_output=True, timeout=120)
    env = {**os.environ, "USERPROFILE": str(profile)}
    subprocess.run([str(tmp_path / "purge_guard_test.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/CASES={tmp_path / 'cases.txt'}", f"/OUT={tmp_path / 'out.txt'}"], env=env, timeout=120)
    got = (tmp_path / "out.txt").read_text(encoding="utf-8").split()
    assert dict(zip(cases, got)) == cases


def test_build_pins_every_download_by_sha256():
    ps = (PKG / "build_windows.ps1").read_text(encoding="utf-8")
    urls = [l for l in ps.splitlines() if "url = " in l]
    shas = [l for l in ps.splitlines() if "sha256 = " in l]
    assert len(urls) == len(shas) == 4
    assert all(len(l.split('"')[1]) == 64 for l in shas)


def test_retention_across_the_repo_move_sorts_by_version_not_name():
    rs = [rel("0.1.0.82"), rel("3.0.0.5"), rel("0.1.0.9"), rel("3.0.0.6"), rel("0.1.0.100"), rel("3.0.0.10")]
    gone = [r["tag_name"] for r in release.releases_to_prune(rs, keep=3)]
    assert sorted(gone) == ["win-v0.1.0.100", "win-v0.1.0.82", "win-v0.1.0.9"]     # kept: 3.0.0.10, 3.0.0.6, 3.0.0.5
    assert release.version_key("win-v3.0.0.5") > release.version_key("win-v0.1.0.82")
    assert release.version_key("win-v3.0.0.10") > release.version_key("win-v3.0.0.9")
