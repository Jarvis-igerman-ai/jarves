"""Reports to Google Drive (JAR-274). Off by default (beta). After a report lands in <home>/reports/:
- mode "folder" (default): copy it into a Google Drive for desktop folder the owner configured; no sign-in.
- mode "api": upload it with the narrow drive.file scope (JARVES sees only the files it created), CSV as a Google
  Sheet. The sign-in is an owner gate: `jarvis reports drive signin` opens the owner's browser, the owner consents.
Settings in <home>/reports_drive.json; no token here (google_auth keeps it)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from . import clilang, logs
from . import features as feat

FEATURE = "reports_drive"
FILE = "reports_drive.json"
DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
SHEET = "application/vnd.google-apps.spreadsheet"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
UPDATE_URL = "https://www.googleapis.com/upload/drive/v3/files/{}?uploadType=multipart"
_WARNED: set[str] = set()                       # warnings already logged in the current outage: once each
_SYNC_LOCK = threading.Lock()                   # one sync at a time: no double send, no racing settings write
_RERUN = threading.Event()                      # a job finished while a sync ran: that sync goes round again
SKIP_SUFFIXES = (".tmp", ".part", ".partial", ".crdownload", "~")   # a writer may still be writing these
SUBDIR = "JARVES reports"                       # its own subfolder: never overwrites an owner file in Drive
MAX_UPLOAD = 5 * 1024 * 1024                    # Google documents uploadType=multipart for files up to 5 MB
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
    """Merge under a cross-process lock (CLI vs the sync thread) and replace the file atomically."""
    from .filelock import file_lock
    path = Path(home) / FILE
    with file_lock(path):
        raw = _raw(home)
        add = kw.pop("ids_add", None) or {}     # re-read under the lock: a concurrent writer's ids are kept
        prune = kw.pop("prune", False)
        d = {**raw, **load(home), **kw}
        if add:
            d["ids"] = {**(raw.get("ids") if isinstance(raw.get("ids"), dict) else {}), **add}
        if prune:                               # a deleted report leaves no entry behind: the file stays small
            for k in ("ids", "sent_folder", "sent_api"):
                if isinstance(d.get(k), dict):
                    d[k] = {n: v for n, v in d[k].items() if (Path(home) / "reports" / n).is_file()}
        fd, tmp = tempfile.mkstemp(dir=home, prefix=FILE, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(d))
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return d


def _first(code: str) -> bool:
    """True only the first time `code` happens in the current outage (bounded logs)."""
    if code in _WARNED:
        return False
    _WARNED.add(code)
    return True


class DriveApi:
    """The real client: one multipart POST with httpx and a drive.file access token."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def upload(self, name: str, data: bytes, mime: str, target_mime: str | None = None, file_id: str | None = None) -> dict:
        import httpx
        from .google_auth import GoogleAuth
        token = asyncio.run(GoogleAuth(self.home).access_token((DRIVE_FILE,)))
        b = f"jarves-{uuid.uuid4().hex}"        # random: a report line can never close a part early

        def body(meta: dict) -> bytes:
            return (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                    f"--{b}\r\nContent-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{b}--".encode()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={b}"}
        if file_id:                             # a changed report replaces its own Drive file (drive.file allows it)
            r = httpx.patch(UPDATE_URL.format(file_id), content=body({"name": name}), timeout=60, headers=headers)
            if r.status_code != 404:            # the owner deleted it in Drive: create a new one below
                r.raise_for_status()
                return r.json()
        new = {"name": name, **({"mimeType": target_mime} if target_mime else {})}   # a CSV is created as a Sheet
        r = httpx.post(UPLOAD_URL, content=body(new), timeout=60, headers=headers)
        r.raise_for_status()
        return r.json()


def _tracked(home: Path, path: Path) -> bool:
    """Only a file directly in <home>/reports/ owns its Drive file id and its copy; a manual upload from elsewhere
    never replaces it."""
    try:
        return path.resolve().parent == (Path(home) / "reports").resolve()
    except OSError:
        return False


def upload(home: Path, path: Path, *, client=None) -> dict:
    """Send one report the configured way. Raises FileNotFoundError / ValueError with a plain reason."""
    path, st = Path(path), load(home)
    if not path.is_file():
        raise FileNotFoundError(f"no such report: {path}")
    tracked = _tracked(home, path)
    if st["mode"] == "api":
        mime = MIMES.get(path.suffix.lower(), "application/octet-stream")
        csv = path.suffix.lower() == ".csv"
        if path.stat().st_size > MAX_UPLOAD:
            raise ValueError(clilang.fmt("הדוח גדול מדי להעלאה (מעל 5MB): השתמשו ב-jarvis reports drive folder PATH",
                                         "report too big for one Drive upload (over 5 MB): use jarvis reports drive folder PATH"))
        ids = _raw(home).get("ids") if tracked else None  # report name -> its Drive file id: update, not duplicate
        r = (client or DriveApi(home)).upload(path.stem if csv else path.name, path.read_bytes(), mime,
                                             SHEET if csv else None,
                                             file_id=ids.get(path.name) if isinstance(ids, dict) else None)
        if tracked and r.get("id"):
            save(home, ids_add={path.name: r["id"]})
        return {"mode": "api", "id": r.get("id", "")}
    dest = Path(st["folder"])
    if not st["folder"] or not dest.is_dir():
        raise ValueError("no Google Drive folder set: run jarvis reports drive folder PATH")
    dest = dest / SUBDIR
    dest.mkdir(exist_ok=True)
    if not tracked and (dest / path.name).exists():
        raise ValueError(clilang.fmt(f"כבר יש דוח בשם {path.name} בדרייב: שנו את שם הקובץ",
                                     f"a report named {path.name} is already in Drive: rename the file"))
    tmp = dest / f".{path.name}.jarves-{uuid.uuid4().hex[:8]}.part"   # a cut copy never shows as the report
    try:
        shutil.copy2(path, tmp)
        os.replace(tmp, dest / path.name)
    finally:
        tmp.unlink(missing_ok=True)
    return {"mode": "folder", "copied": str(dest / path.name)}


RECONNECT = ("GOOGLE_AUTH_EXPIRED", "GOOGLE_NOT_CONNECTED", "GOOGLE_SCOPE_MISSING", "GOOGLE_NO_REFRESH_TOKEN")


AUTH = ("authError", "insufficientPermissions")    # a 403 that a new sign-in fixes; a file-level refusal does not
BUSY = ("rateLimitExceeded", "userRateLimitExceeded", "storageQuotaExceeded", "dailyLimitExceeded")


def _drive_reasons(resp) -> set[str]:
    try:
        errs = resp.json().get("error", {}).get("errors", [])
        return {x.get("reason") for x in errs if isinstance(x, dict)}
    except (ValueError, AttributeError):        # not JSON, or not the Drive error shape
        return set()


def needs_reconnect(e: Exception) -> bool:
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status == 403:                           # only an auth or scope refusal stops the sync and asks to sign in
        return bool(_drive_reasons(resp) & set(AUTH))
    return status == 401 or getattr(e, "code", None) in RECONNECT


def reason(e: Exception) -> str:
    """One plain sentence for the owner: a missing or refused sign-in says how to reconnect."""
    if getattr(getattr(e, "response", None), "status_code", None) == 403 and _drive_reasons(e.response) & set(BUSY):
        return clilang.fmt("גוגל דרייב מלא או עמוס כרגע: הדוח יישלח בהמשך",
                           "Google Drive is full or busy right now: the report is sent later")
    if needs_reconnect(e):
        return clilang.fmt("צריך לחבר מחדש את גוגל: הריצו jarvis reports drive signin",
                           "Google needs to be reconnected: run jarvis reports drive signin")
    return str(e) or type(e).__name__


def _send(home: Path, path: Path, client=None) -> dict:
    try:
        return upload(home, path, client=client)
    except Exception as e:      # a report write must never fail because Drive is unavailable
        return {"error": reason(e), **({"reconnect": True} if needs_reconnect(e) else {})}


def after_report(home: Path, path: Path, *, features, client=None) -> dict | None:
    """Hook for report writers (whatsapp_collect and others): checks the switch itself; never raises.
    Blocking (file copy or HTTP + asyncio.run for the token): an async writer calls it via asyncio.to_thread."""
    if features is None or not features.active(FEATURE):
        return None
    if not _tracked(home, path):
        return _send(home, path, client)
    with _SYNC_LOCK:                            # a report in <home>/reports/ is recorded: the next sync skips it
        key, m = _sent_key(home), _mtime(path)
        r = _send(home, path, client)
        if "error" not in r and m is not None:
            save(home, **{key: {**_sent(home, key), path.name: m}})
    return r


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _sent_key(home: Path) -> str:
    """Sent reports are remembered per mode: after a switch to `signin`, folder copies still go through the API."""
    return "sent_" + load(home)["mode"]


def _sent(home: Path, key: str) -> dict:
    d = _raw(home).get(key)
    return dict(d) if isinstance(d, dict) else {}


def sync(home: Path, *, client=None) -> list[dict]:
    """Send every new or changed file in <home>/reports/ once (remembered by name and mtime in reports_drive.json)."""
    _RERUN.set()
    out: list[dict] = []
    while _RERUN.is_set():                      # re-check after release: a job that finished meanwhile is not lost
        if not _SYNC_LOCK.acquire(blocking=False):
            return out                          # the running sync sees _RERUN and goes round once more
        try:
            while _RERUN.is_set():
                _RERUN.clear()
                out += _sync(home, client)
        finally:
            _SYNC_LOCK.release()
    return out


def _folder_missing(home: Path) -> bool:
    """Folder mode with no usable folder: skip the sync and warn once per outage, not per file per job run."""
    st = load(home)
    if st["mode"] != "folder" or (st["folder"] and Path(st["folder"]).is_dir()):
        _WARNED.discard("NO_FOLDER")
        return False
    if _first("NO_FOLDER"):
        logs.warn("reports_drive", "NO_FOLDER", "no Google Drive folder set; reports are not sent")
    return True


def _sync(home: Path, client) -> list[dict]:
    if _folder_missing(home):
        return []
    key = _sent_key(home)
    before = _raw(home).get(key)
    folder = Path(home) / "reports"
    sent = {n: m for n, m in _sent(home, key).items() if (folder / n).is_file()}
    out = []
    for f in sorted(folder.iterdir()) if folder.is_dir() else []:
        if f.name.startswith(".") or f.name.lower().endswith(SKIP_SUFFIXES):
            continue
        m = _mtime(f)                           # read before the send: a rewrite during the upload is sent again
        if f.is_file() and m is not None and sent.get(f.name) != m:
            r = _send(home, f, client)
            out.append(r)
            if r and "error" not in r:
                sent[f.name] = m
                _WARNED.discard("RECONNECT")
                _WARNED.discard("SEND_FAILED:" + f.name)
            elif r.get("reconnect"):            # every other file would fail the same way: stop, warn once
                if _first("RECONNECT"):
                    logs.warn("reports_drive", "RECONNECT", "Google sign-in expired or refused; reports are not sent")
                break
            elif _first("SEND_FAILED:" + f.name):       # once per file per outage, not every job run
                logs.warn("reports_drive", "SEND_FAILED", "report not sent to Google Drive", file=f.name, error=r["error"][:200])
    if sent != before:                          # nothing new: the settings file is not rewritten
        save(home, prune=True, **{key: sent})
    return out


def on_job_finished(features) -> None:
    """Scheduler hook: with the switch on, sync the reports folder off the event loop. Never raises."""
    try:
        if features is not None and features.active(FEATURE):
            threading.Thread(target=sync, args=(features.home,), daemon=True, name="reports-drive").start()
            _WARNED.discard("HOOK_FAILED")
    except Exception as e:      # a finished job must not fail because of this hook; say so once, type only
        if _first("HOOK_FAILED"):
            logs.warn("reports_drive", "HOOK_FAILED", "reports to Drive hook failed", error=type(e).__name__)


def cmd_drive(a, cfg, home) -> int:
    from .cli import _cli_features
    on = _cli_features(cfg, home).active(FEATURE)
    if a.drive_action in ("signin", "upload") and not on:        # the switch is the gate for anything leaving the PC
        print(clilang.fmt("הדוחות לדרייב כבויים: jarvis features on reports_drive",
                          "Reports to Drive is off: jarvis features on reports_drive"))
        return 1
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
            print(reason(e))
            return 1
        return 0
    print(json.dumps({**load(home), "enabled": on}))
    return 0
