"""JAR-274: reports to Google Drive. Folder copy (no sign-in) and the drive.file API upload with a fake client."""
from __future__ import annotations

import asyncio
import json

from jarves import cli, reports_drive as rd
from jarves.features import CATALOG


def switch_on():
    assert cli.main(["features", "beta", "on"]) == 0 and cli.main(["features", "on", rd.FEATURE]) == 0


class FakeDrive:
    def __init__(self):
        self.sent = []

    def upload(self, name, data, mime, target_mime=None, file_id=None):
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
    assert cli.main(["reports", "drive", "upload", str(rep)]) == 1      # the switch is off: nothing is sent
    assert "jarvis features on reports_drive" in capsys.readouterr().out and not (dest / "r.txt").exists()
    switch_on()
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


def test_overlapping_sync_is_skipped_and_failed_signin_is_a_plain_error(home, tmp_path, monkeypatch, capsys):
    from jarves.google_auth import GoogleAuth, GoogleAuthError
    dest = tmp_path / "gdrive"
    dest.mkdir()
    rd.save(tmp_path, folder=str(dest))
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "a.md").write_bytes(b"1")
    with rd._SYNC_LOCK:                      # another sync is running: this one sends nothing
        assert rd.sync(tmp_path) == [] and not list(dest.iterdir())

    async def refuse(self, scopes=(), timeout_s=300):
        raise GoogleAuthError("no_client", "no Google client set up")
    monkeypatch.setattr(GoogleAuth, "sign_in", refuse)
    switch_on()
    assert cli.main(["reports", "drive", "signin"]) == 1
    assert "no Google client" in capsys.readouterr().out and rd.load(home)["mode"] == "folder"


def test_api_upload_before_signin_is_a_plain_error(home, monkeypatch, capsys):
    from jarves.google_auth import GoogleAuth, GoogleAuthError

    async def no_token(self, need=(), *, force_refresh=False):
        raise GoogleAuthError("GOOGLE_NOT_CONNECTED", "Google is not connected")
    monkeypatch.setattr(GoogleAuth, "access_token", no_token)
    switch_on()
    rd.save(home, mode="api")
    rep = home / "r.csv"
    rep.write_text("a,b\n", encoding="utf-8")
    assert cli.main(["reports", "drive", "upload", str(rep)]) == 1
    assert "run jarvis reports drive signin" in capsys.readouterr().out


def test_drive_refusing_the_token_says_reconnect_and_keeps_the_report(home, capsys):
    import httpx

    class Refused:
        def upload(self, *a, **kw):
            req = httpx.Request("POST", rd.UPLOAD_URL)
            raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
    rd.save(home, mode="api")
    rep = home / "r.csv"
    rep.write_text("a,b\n", encoding="utf-8")
    out = rd.after_report(home, rep, active=True, client=Refused())
    assert "run jarvis reports drive signin" in out["error"] and rep.read_text(encoding="utf-8") == "a,b\n"


def test_real_upload_asks_only_drive_file_and_sends_a_well_formed_multipart(tmp_path, monkeypatch):
    import httpx

    from jarves import google_auth
    scopes, sent = [], []

    async def token(self, need=()):
        scopes.append(tuple(need))
        return "tok"

    def post(url, content, timeout, headers):
        sent.append((url, content, headers))
        return httpx.Response(200, json={"id": "x"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(google_auth.GoogleAuth, "access_token", token)
    monkeypatch.setattr(httpx, "post", post)
    data = b"a,b\n--jarves-report-part\n"           # a report line that looks like a fixed boundary
    assert rd.DriveApi(tmp_path).upload("wa", data, "text/csv", rd.SHEET) == {"id": "x"}
    url, body, headers = sent[0]
    b = headers["Content-Type"].split("boundary=")[1]
    assert scopes == [(rd.DRIVE_FILE,)] and headers["Authorization"] == "Bearer tok" and url == rd.UPLOAD_URL
    assert f"--{b}".encode() not in data and body.endswith(f"--{b}--".encode())
    meta, part = body.split(f"--{b}".encode())[1:3]
    assert json.loads(meta.split(b"\r\n\r\n")[1]) == {"name": "wa", "mimeType": rd.SHEET}
    assert part == b"\r\nContent-Type: text/csv\r\n\r\n" + data + b"\r\n"


async def test_a_finished_scheduler_job_sends_new_reports_only_while_on_and_keeps_them_on_failure(home, monkeypatch):
    from jarves import scheduler as S
    from jarves.userdata import UserDB
    monkeypatch.setattr(rd.threading, "Thread", lambda target, args, **kw: type("T", (), {"start": lambda s: target(*args)})())
    dest = home / "gdrive"
    dest.mkdir()
    rd.save(home, folder=str(dest))
    (home / "reports").mkdir()
    (home / "reports" / "wa.csv").write_bytes(b"a,b\n")       # what whatsapp_collect leaves behind

    class Feats:
        def __init__(self, on):
            self.home, self.on = home, on

        def active(self, fid):
            return fid != rd.FEATURE or self.on

    async def run(on):
        db = UserDB(home / f"m{on}.db")
        t = [1_800_000_000.0]

        async def research(task, budget):
            from jarves.tasks import LookupResult
            return LookupResult("ok", queries=[])
        s = S.Scheduler(db, research=research, clock=lambda: t[0], sleep=lambda s: asyncio.sleep(0), features=Feats(on))
        s.approve(s.preview("כל בוקר ב-8", "חדשות")["id"], by="voice")
        t[0] += 2 * 86400
        await s.tick()
        await asyncio.gather(*[x for v in s._running.values() for x in v], return_exceptions=True)
        db.close()
    await run(False)
    assert not list(dest.iterdir())                          # switch off: nothing leaves the PC
    await run(True)
    assert (dest / "wa.csv").read_bytes() == b"a,b\n"
    (home / "reports" / "b.md").write_bytes(b"x")
    rd.save(home, folder=str(home / "gone"))                 # Drive folder unavailable
    await run(True)
    assert (home / "reports" / "b.md").read_bytes() == b"x" and not (dest / "b.md").exists()


def test_changed_report_updates_the_same_drive_file_not_a_duplicate(tmp_path):
    class Keep(FakeDrive):
        def upload(self, name, data, mime, target_mime=None, file_id=None):
            self.sent.append((name, data, file_id))
            return {"id": file_id or "f1"}
    rd.save(tmp_path, mode="api")
    rep = tmp_path / "reports" / "wa.csv"
    rep.parent.mkdir()
    rep.write_bytes(b"a\n")
    fake = Keep()
    rd.upload(tmp_path, rep, client=fake)
    rep.write_bytes(b"a\nb\n")
    rd.upload(tmp_path, rep, client=fake)
    assert fake.sent == [("wa", b"a\n", None), ("wa", b"a\nb\n", "f1")]


def test_csv_whose_sheet_was_deleted_in_drive_comes_back_as_a_sheet(tmp_path, monkeypatch):
    import httpx

    from jarves import google_auth

    async def token(self, need=()):
        return "t"

    calls = []

    def call(method):
        def f(url, content, timeout, headers):
            calls.append((method, content))
            return httpx.Response(404 if method == "patch" else 200, json={"id": "new"}, request=httpx.Request(method, url))
        return f

    monkeypatch.setattr(google_auth.GoogleAuth, "access_token", token)
    monkeypatch.setattr(httpx, "patch", call("patch"))
    monkeypatch.setattr(httpx, "post", call("post"))
    assert rd.DriveApi(tmp_path).upload("wa", b"a,b\n", "text/csv", rd.SHEET, file_id="gone") == {"id": "new"}
    assert [m for m, _ in calls] == ["patch", "post"]
    assert rd.SHEET.encode() in calls[1][1]       # re-created as a Google Sheet, not a plain CSV
