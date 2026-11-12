"""JAR-274: reports to Google Drive. Folder copy (no sign-in) and the drive.file API upload with a fake client."""
from __future__ import annotations

import json

from jarves import cli, reports_drive as rd
from jarves.features import CATALOG


class FakeDrive:
    def __init__(self):
        self.sent = []

    def upload(self, name, data, mime, target_mime=None):
        self.sent.append((name, data, mime, target_mime))
        return {"id": "f1", "name": name}


def test_off_by_default_beta_and_narrow_scope():
    f = CATALOG[rd.FEATURE]
    assert f.default_enabled is False
    assert rd.DRIVE_FILE.endswith("/auth/drive.file")


def test_off_copies_nothing(tmp_path):
    dest = tmp_path / "gdrive"
    dest.mkdir()
    rd.save(tmp_path, folder=str(dest))
    rep = tmp_path / "reports" / "wa.csv"
    rep.parent.mkdir()
    rep.write_bytes(b"a,b\n")
    assert rd.after_report(tmp_path, rep, active=False) is None
    assert not list(dest.iterdir())


def test_folder_copy_and_api_upload_converts_csv_to_sheet(tmp_path):
    rep = tmp_path / "reports" / "wa.csv"
    rep.parent.mkdir()
    rep.write_bytes(b"a,b\n")
    dest = tmp_path / "gdrive"
    dest.mkdir()
    rd.save(tmp_path, folder=str(dest))
    out = rd.after_report(tmp_path, rep, active=True)
    assert out["mode"] == "folder" and (dest / "wa.csv").read_bytes() == b"a,b\n"
    rd.save(tmp_path, mode="api")
    fake = FakeDrive()
    out = rd.upload(tmp_path, rep, client=fake)
    assert out["mode"] == "api" and fake.sent == [("wa", b"a,b\n", "text/csv", rd.SHEET)]
    md = tmp_path / "reports" / "x.md"
    md.write_bytes(b"hi")
    rd.upload(tmp_path, md, client=fake)
    assert fake.sent[-1] == ("x.md", b"hi", "text/markdown", None)


def test_folder_must_exist_and_cli_status_folder_upload(home, capsys):
    tmp_path = home
    assert cli.main(["reports", "drive", "folder", str(tmp_path / "missing")]) == 1
    dest = tmp_path / "gdrive"
    dest.mkdir()
    assert cli.main(["reports", "drive", "folder", str(dest)]) == 0
    capsys.readouterr()
    assert cli.main(["reports", "drive", "status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["folder"] == str(dest) and st["mode"] == "folder" and st["enabled"] is False
    rep = tmp_path / "r.txt"
    rep.write_text("x", encoding="utf-8")
    assert cli.main(["reports", "drive", "upload", str(rep)]) == 0
    assert (dest / "r.txt").exists()


def test_sync_sends_each_new_report_once_and_the_scheduler_hook_respects_the_switch(tmp_path, monkeypatch):
    dest = tmp_path / "gdrive"
    dest.mkdir()
    rd.save(tmp_path, folder=str(dest))
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "a.md").write_bytes(b"1")
    assert len(rd.sync(tmp_path)) == 1 and rd.sync(tmp_path) == []
    assert (dest / "a.md").read_bytes() == b"1" and rd.load(tmp_path)["folder"] == str(dest)
    calls = []

    class Now:
        def __init__(self, target, args, **kw):
            self.run = lambda: calls.append(args)

        def start(self):
            self.run()
    monkeypatch.setattr(rd.threading, "Thread", Now)
    feats = type("F", (), {"home": tmp_path, "on": False, "active": lambda self, fid: self.on})()
    rd.on_job_finished(feats)
    assert calls == []
    feats.on = True
    rd.on_job_finished(feats)
    assert calls == [(tmp_path,)]
