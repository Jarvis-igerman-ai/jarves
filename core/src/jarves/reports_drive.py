"""Reports to Google Drive (JAR-274). Off by default (beta). After a report lands in <home>/reports/:
- mode "folder" (default): copy it into a Google Drive for desktop folder the owner configured; no sign-in.
- mode "api": upload it with the narrow drive.file scope (JARVES sees only the files it created), CSV as a Google
  Sheet. The sign-in is an owner gate: `jarvis reports drive signin` opens the owner's browser, the owner consents.
Settings in <home>/reports_drive.json; no token here (google_auth keeps it)."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

from . import clilang, logs
from . import features as feat

FEATURE = "reports_drive"
FILE = "reports_drive.json"
DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
SHEET = "application/vnd.google-apps.spreadsheet"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
UPDATE_URL = "https://www.googleapis.com/upload/drive/v3/files/{}?uploadType=multipart"
_SYNC_LOCK = threading.Lock()                   # one sync at a time: no double send, no racing settings write
MIMES = {".csv": "text/csv", ".md": "text/markdown", ".txt": "text/plain", ".json": "application/json"}

feat.register(FEATURE, "דוחות לגוגל דרייב", "Reports to Google Drive", False, stage="beta",
              about="copy each new report to your Google Drive folder, or upload it with the drive.file permission",
              targets={"windows": {"status": "implemented_unverified", "blocker": "tested with a fake Drive client only"},
                       "linux_arm64_pi": {"status": "implemented_unverified", "blocker": "not run on a Pi",
                                          "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}},
              permissions=["Drive: only files JARVES created (drive.file)"], settings="jarvis reports drive status")


def _raw(home: Path) -> dict:
    try:
        d = json.loads((Path(home) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return d if isinstance(d, dict) else {}


def load(home: Path) -> dict:
    d = _raw(home)
    return {"folder": str(d.get("folder") or ""), "mode": d.get("mode") if d.get("mode") in ("folder", "api") else "folder"}


def save(home: Path, **kw) -> dict:
    d = {**_raw(home), **load(home), **kw}
    (Path(home) / FILE).write_text(json.dumps(d), encoding="utf-8")
    return d


class DriveApi:
    """The real client: one multipart POST with httpx and a drive.file access token."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def upload(self, name: str, data: bytes, mime: str, target_mime: str | None = None, file_id: str | None = None) -> dict:
        import httpx
        from .google_auth import GoogleAuth
        token = asyncio.run(GoogleAuth(self.home).access_token((DRIVE_FILE,)))
        meta = {"name": name, **({"mimeType": target_mime} if target_mime and not file_id else {})}
        b = "jarves-report-part"
        body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                f"--{b}\r\nContent-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{b}--".encode()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={b}"}
        if file_id:                             # a changed report replaces its own Drive file (drive.file allows it)
            r = httpx.patch(UPDATE_URL.format(file_id), content=body, timeout=60, headers=headers)
            if r.status_code != 404:            # the owner deleted it in Drive: create a new one below
                r.raise_for_status()
                return r.json()
        r = httpx.post(UPLOAD_URL, content=body, timeout=60, headers=headers)
        r.raise_for_status()
        return r.json()


def upload(home: Path, path: Path, *, client=None) -> dict:
    """Send one report the configured way. Raises FileNotFoundError / ValueError with a plain reason."""
    path, st = Path(path), load(home)
    if not path.is_file():
        raise FileNotFoundError(f"no such report: {path}")
    if st["mode"] == "api":
        mime = MIMES.get(path.suffix.lower(), "application/octet-stream")
        csv = path.suffix.lower() == ".csv"
        ids = dict(_raw(home).get("ids") or {})          # report name -> its Drive file id: update, not duplicate
        r = (client or DriveApi(home)).upload(path.stem if csv else path.name, path.read_bytes(), mime,
                                             SHEET if csv else None, file_id=ids.get(path.name))
        if r.get("id"):
            save(home, ids={**ids, path.name: r["id"]})
        return {"mode": "api", "id": r.get("id", "")}
    dest = Path(st["folder"])
    if not st["folder"] or not dest.is_dir():
        raise ValueError("no Google Drive folder set: run jarvis reports drive folder PATH")
    shutil.copy2(path, dest / path.name)
    return {"mode": "folder", "copied": str(dest / path.name)}


def after_report(home: Path, path: Path, *, active: bool, client=None) -> dict | None:
    """Hook for report writers (whatsapp_collect and others): send only when the switch is on; never raises."""
    if not active:
        return None
    try:
        return upload(home, path, client=client)
    except Exception as e:      # a report write must never fail because Drive is unavailable
        return {"error": str(e)}


def sync(home: Path, *, client=None) -> list[dict]:
    """Send every new or changed file in <home>/reports/ once (remembered by name and mtime in reports_drive.json)."""
    if not _SYNC_LOCK.acquire(blocking=False):
        return []                               # a sync is already running; the next job run picks up the rest
    try:
        return _sync(home, client)
    finally:
        _SYNC_LOCK.release()


def _sync(home: Path, client) -> list[dict]:
    sent = dict(_raw(home).get("sent") or {})
    out = []
    folder = Path(home) / "reports"
    for f in sorted(folder.iterdir()) if folder.is_dir() else []:
        if f.is_file() and sent.get(f.name) != f.stat().st_mtime:
            r = after_report(home, f, active=True, client=client)
            out.append(r)
            if r and "error" not in r:
                sent[f.name] = f.stat().st_mtime
            elif r:
                logs.warn("reports_drive", "SEND_FAILED", "report not sent to Google Drive", file=f.name, error=r["error"][:200])
    save(home, sent=sent)
    return out


def on_job_finished(features) -> None:
    """Scheduler hook: with the switch on, sync the reports folder off the event loop. Never raises."""
    try:
        if features is not None and getattr(features, "home", None) and features.active(FEATURE):
            threading.Thread(target=sync, args=(features.home,), daemon=True, name="reports-drive").start()
    except Exception:
        pass


def cmd_drive(a, cfg, home) -> int:
    from .cli import _cli_features
    if a.drive_action == "folder":
        if not Path(a.path).is_dir():
            print(clilang.fmt("התיקייה לא קיימת", "that folder does not exist"))
            return 1
        save(home, folder=str(Path(a.path)), mode="folder")
    elif a.drive_action == "signin":
        from .google_auth import GoogleAuth, GoogleAuthError
        print(clilang.fmt("נפתח הדפדפן: אתם מתחברים ומאשרים רק גישה לקבצים ש-JARVES יוצר.",
                          "Opening your browser: you sign in and allow access only to files JARVES creates."))
        try:
            asyncio.run(GoogleAuth(home).sign_in((DRIVE_FILE,)))
        except GoogleAuthError as e:
            print(e)
            return 1
        save(home, mode="api")
    elif a.drive_action == "upload":
        import httpx
        from .google_auth import GoogleAuthError
        try:
            print(json.dumps(upload(home, Path(a.file))))
        except (OSError, ValueError, GoogleAuthError, httpx.HTTPError) as e:   # not signed in, offline, Drive refused
            print(e)
            return 1
        return 0
    print(json.dumps({**load(home), "enabled": _cli_features(cfg, home).active(FEATURE)}))
    return 0
