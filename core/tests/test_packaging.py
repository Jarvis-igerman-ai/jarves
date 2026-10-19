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


def test_build_pins_every_download_by_sha256():
    ps = (PKG / "build_windows.ps1").read_text(encoding="utf-8")
    urls = [l for l in ps.splitlines() if "url = " in l]
    shas = [l for l in ps.splitlines() if "sha256 = " in l]
    assert len(urls) == len(shas) == 4
    assert all(len(l.split('"')[1]) == 64 for l in shas)
