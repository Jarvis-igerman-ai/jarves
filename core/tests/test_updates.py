"""M6 update check: read-only, notify once, install only after approval, SHA256 + backup first. Fake GitHub, no network."""
import hashlib
import json
import sqlite3
import sys
import zipfile

import pytest

from jarves import updates
from jarves.updates import UpdateChecker, UpdateError, is_newer, pick_release, version_tuple

EXE = b"MZ fake installer bytes"
SHA = hashlib.sha256(EXE).hexdigest()


def release(v, *, tag=None, draft=False, pre=False, sha_file=True, digest=True, exe_name=None):
    name = exe_name or f"JARVES-Setup-{v}.exe"
    assets = [{"name": name, "url": f"https://api.github.com/assets/{v}", "size": len(EXE),
               "browser_download_url": f"https://github.com/dl/{name}", "digest": f"sha256:{SHA}" if digest else None}]
    if sha_file:
        assets.append({"name": name + ".sha256", "url": f"https://api.github.com/assets/{v}.sha256"})
    return {"tag_name": tag or f"win-v{v}", "draft": draft, "prerelease": pre, "assets": assets,
            "html_url": f"https://github.com/r/{v}", "published_at": "2026-10-19T10:00:00Z", "body": "notes"}


class FakeGitHub:
    def __init__(self, releases, status=200, exe=EXE, sha_text=None):
        self.releases, self.status, self.exe = releases, status, exe
        self.sha_text = sha_text if sha_text is not None else f"{SHA}  JARVES-Setup.exe\n"
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if "/releases" in url:
            return self.status, self.releases if self.status == 200 else None
        if url.endswith(".sha256"):
            return 200, self.sha_text.encode()
        return 200, self.exe


def test_versions():
    assert version_tuple("win-v0.1.0.57") == (0, 1, 0, 57)
    assert version_tuple("0.2") == (0, 2, 0, 0)
    assert version_tuple("latest") is None
    assert is_newer("0.1.0.58", "0.1.0.57") and not is_newer("0.1.0.57", "0.1.0.57")
    assert is_newer("0.1.0.1", "0.1.0+dev")          # a dev checkout sees any stable build as newer
    assert not is_newer("0.1.0.9", "0.2.0.1")


def test_pick_release_skips_drafts_prereleases_other_tags_and_assets():
    rels = [release("0.1.0.90", draft=True), release("0.1.0.80", pre=True), release("0.1.0.70", tag="pi-v0.1.0.70"),
            release("0.1.0.60", exe_name="notes.txt"), release("0.1.0.50"), release("0.1.0.40")]
    r = pick_release(rels, "0.1.0.10")
    assert r["version"] == "0.1.0.50" and r["asset"]["name"] == "JARVES-Setup-0.1.0.50.exe"
    assert pick_release(rels, "0.1.0.50") is None     # nothing newer than what is installed


def test_check_is_cached_for_a_day_and_notifies_once(tmp_path):
    gh = FakeGitHub([release("0.1.0.5")])
    clock = [1000.0]
    u = UpdateChecker(tmp_path, http=gh, now=lambda: clock[0], current="0.1.0.4")
    s = u.check()
    assert s["available"] and s["latest"]["version"] == "0.1.0.5" and s["policy"] == "notify_and_ask"
    u.check()
    assert len(gh.calls) == 1                          # no polling: cached
    clock[0] += updates.CHECK_INTERVAL_S + 1
    u.check()
    assert len(gh.calls) == 2
    assert "0.1.0.5" in u.notice("he") and "approve" in u.notice("en")
    u.mark_notified()
    assert u.notice("he") == ""                        # said once per version
    assert gh.calls[0][0].startswith("https://api.github.com/repos/")
    assert "Authorization" not in gh.calls[0][1]       # anonymous unless the user set a token


def test_private_repo_and_offline_are_unavailable_not_up_to_date(tmp_path, monkeypatch):
    s = UpdateChecker(tmp_path, http=FakeGitHub([], status=404), current="0.1.0.1").check(force=True)
    assert not s["available"] and "jarvis-releases" in s["error"] and "igalgerman/jarves" in s["error"]

    def offline(url, headers):
        raise OSError("no network")
    s = UpdateChecker(tmp_path, http=offline, current="0.1.0.1").check(force=True)
    assert not s["available"] and "no connection" in s["error"]
    monkeypatch.setenv("JARVES_UPDATE_TOKEN", "github_pat_test")
    gh = FakeGitHub([])
    UpdateChecker(tmp_path, http=gh, current="0.1.0.1").check(force=True)
    assert gh.calls[0][1]["Authorization"] == "Bearer github_pat_test"


def _home_with_data(tmp_path):
    db = sqlite3.connect(tmp_path / "memory.db")
    db.execute("create table m (t text)")
    db.execute("insert into m values ('likes tea')")
    db.commit()
    db.close()
    (tmp_path / "config.toml").write_text("[ai]\nconnection = \"chatgpt\"\n", encoding="utf-8")
    (tmp_path / "secrets.env").write_text("OPENAI_API_KEY=sk-test-never-copied\n", encoding="utf-8")
    (tmp_path / "link_token").write_text("tok", encoding="utf-8")
    return tmp_path


def test_install_needs_approval_checksum_and_backs_up_first(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    home = _home_with_data(tmp_path)
    u = UpdateChecker(home, http=FakeGitHub([release("0.1.0.9")]), current="0.1.0.8")
    u.check()
    with pytest.raises(UpdateError) as e:
        u.prepare_install(approved=False)
    assert e.value.code == "UPDATE_NEEDS_APPROVAL"
    assert not (home / "updates").exists()             # nothing downloaded without approval
    r = u.prepare_install(approved=True)
    assert open(r["installer"], "rb").read() == EXE
    from jarves import backup
    rep = backup.verify(r["backup"])                   # WP-M5's verified pre-migration archive
    assert rep["ok"], rep["errors"]
    with zipfile.ZipFile(r["backup"]) as z:
        names = {n.split("/")[-1] for n in z.namelist()}
    assert {"memory.db", "config.toml"} <= names
    assert "secrets.env" not in names and "link_token" not in names   # credentials never copied


@pytest.mark.parametrize("gh,code", [
    (FakeGitHub([release("0.1.0.9")], exe=b"tampered"), "UPDATE_CHECKSUM_MISMATCH"),
    (FakeGitHub([release("0.1.0.9")], sha_text="0" * 64), "UPDATE_CHECKSUM_MISMATCH"),
    (FakeGitHub([release("0.1.0.9", sha_file=False, digest=False)]), "UPDATE_NO_CHECKSUM"),
])
def test_bad_or_missing_checksum_refuses(tmp_path, monkeypatch, gh, code):
    monkeypatch.setattr(sys, "platform", "win32")
    u = UpdateChecker(tmp_path, http=gh, current="0.1.0.8")
    u.check()
    with pytest.raises(UpdateError) as e:
        u.prepare_install(approved=True)
    assert e.value.code == code
    assert not list((tmp_path / "updates").glob("*.exe")) if (tmp_path / "updates").exists() else True


def test_no_installer_on_pi_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    u = UpdateChecker(tmp_path, http=FakeGitHub([release("0.1.0.9")]), current="0.1.0.8")
    u.check()
    with pytest.raises(UpdateError) as e:
        u.prepare_install(approved=True)
    assert e.value.code == "UPDATE_NOT_APPLICABLE"


def test_build_info_and_version_line(tmp_path, monkeypatch):
    assert updates.build_info()["channel"] in ("dev", "main", "pr")
    f = tmp_path / "_build_info.json"
    f.write_text(json.dumps({"version": "0.1.0.57", "commit": "abcdef1234567890", "built": "2026-10-19T10:00:00Z",
                             "channel": "main"}), encoding="utf-8")
    monkeypatch.setattr(updates, "BUILD_INFO", f)
    assert updates.version_line() == "JARVES 0.1.0.57 (commit abcdef123456, built 2026-10-19T10:00:00Z, channel main)"
    f.write_text("{broken", encoding="utf-8")
    assert updates.build_info()["channel"] == "dev"


def test_feature_registered_with_platform_status():
    from jarves import features
    assert features.CATALOG["update_check"].risk == "low"
    pi = updates.PLATFORMS["windows_installer"]["linux_arm64_pi"]
    assert pi["status"] == "planned" and pi["roadmap_issue"].startswith("https://github.com/igalgerman/jarves/issues/")


def test_dashboard_install_route_requires_approve(tmp_path, monkeypatch):
    class Ctx:
        home, cfg = tmp_path, None
    monkeypatch.setattr(updates, "_enabled", lambda ctx: True)
    code, body = updates.api_updates_install(Ctx, None, {})
    assert code == 409 and body["code"] in ("UPDATE_NEEDS_APPROVAL", "UPDATE_NOT_APPLICABLE")


def test_cli_version_flag(capsys):
    from jarves import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.startswith("JARVES ")


def test_repo_move_versions_only_ever_increase():
    """The old private repo built 0.1.0.<run>; the public repo restarted its counter but builds 3.0.0.<run>."""
    assert version_tuple("win-v3.0.0.5") == (3, 0, 0, 5)
    assert is_newer("3.0.0.5", "0.1.0.82") and not is_newer("0.1.0.82", "3.0.0.5")
    assert is_newer("3.0.0.6", "3.0.0.5") and not is_newer("3.0.0.5", "3.0.0.5")


def test_installed_old_build_sees_new_series_and_a_new_build_never_sees_a_downgrade():
    rels = [release("0.1.0.82"), release("3.0.0.5"), release("0.1.0.9")]
    assert pick_release(rels, "0.1.0.82")["version"] == "3.0.0.5"
    assert pick_release(rels, "3.0.0.5") is None                       # 0.1.0.82 is never offered as an update
    assert pick_release([release("0.1.0.82")], "3.0.0.5") is None


def test_package_version_is_in_the_new_series():
    from jarves import __version__
    assert version_tuple(__version__) >= (3, 0, 0, 0) and is_newer(f"{__version__}.5", "0.1.0.82")
