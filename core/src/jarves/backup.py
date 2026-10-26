"""Local backups, verification and restore (SW-V2 M5, spec section 12, decision D17).

What a backup holds (one zip in <home>/backups/, written to a temp name and renamed, so a crash or a full disk
never leaves a half archive that looks valid):
- every SQLite database in the data dir (memory.db, store.db, future jobs/notes dbs) copied with the SQLite ONLINE
  BACKUP API: a consistent snapshot even while the Core writes (WAL);
- settings and user data: config.toml, features.json and other *.json/*.toml/*.md at the top level, and the folders
  (workspace/, notes/, reports/, files/ ... everything except EXCLUDE_DIRS);
- manifest.json: version, created, platform, kind, every file's size + SHA-256, what was left out and why.
Never in a backup: credentials (secrets.env, chatgpt/ and google/ token folders, the link token, Pear pairing id,
any file whose name says token/secret/key/auth/cookie/credential/password, or whose text contains an API key / JWT /
refresh token), logs, caches, health snapshots and the backups folder itself. Face-recognition data (faces.db) is
biometric: left out unless backup.include_faces is on. So an archive can be shared without leaking a key; after a
restore on a new machine the user signs in again (`jarvis key`, `jarvis chatgpt login`, `jarvis google login`).

Retention (adjustable, memory.db settings `backup.*`): automatic backups keep the newest per day for the last
7 days that have one and the newest per ISO week for the last 4 weeks; manual ones the last 10; pre-migration /
pre-restore checkpoints the last 5 each. Only automatic backups are thinned by the day/week rule.

Restore: verify first (zip CRC, every SHA-256, PRAGMA integrity_check of every db); refuse while the Core runs on
that data dir; take a pre-restore checkpoint; stage into a temp dir on the same disk, then swap file by file; any
failure puts the checkpoint back, so the previous user data survives. restore_test() restores into an isolated
throw-away profile and opens the stores there (the restore drill). Files not in the archive are left alone.

Interface: create(home, kind) -> Path; verify(path) -> dict; restore(path, target); restore_test(path);
checkpoint(home, reason) (before a migration/update: raises if it cannot be verified); startup_checkpoint(home)
(Core start when the version changed); start_auto(home) (daily, in a daemon thread); dashboard_routes; add_cli.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import __version__, locales, logs

BACKUP_DIR = "backups"
KINDS = ("auto", "manual", "pre-migration", "pre-restore")
EXCLUDE_DIRS = {BACKUP_DIR, "logs", "chatgpt", "google", "cache", "caches", "models", "tmp", "temp", "__pycache__",
                "voice_cache", ".restore-staging",
                "whatsapp_bridge"}     # SW-V4 F: the WhatsApp session (= the whole account) + messages: never archived
EXCLUDE_FILES = {"secrets.env", "link_token", "pear_client_id", "pear_client_id.paired", "health.json",
                 "startup_timing.json", "setup_done",
                 "answer.key",                  # R-01 approval key: private, rotated on every Core start
                 "dashboard_devices.json"}      # paired-phone token HASHES + per-device CSRF secrets: never archived
_SECRET_NAME = re.compile(r"(?i)(token|secret|api[_-]?key|auth|cookie|credential|password|\.env$|\.pem$|\.key$)")
_SECRET_TEXT = re.compile(r"sk-[A-Za-z0-9_\-]{16,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.|\"refresh_token\"\s*:")
_SKIP_SUFFIX = (".tmp", ".lock", "-wal", "-shm", "-journal", ".partial")
MAX_FILE_BYTES = 512 * 1024 * 1024
SQLITE_MAGIC = b"SQLite format 3\x00"
DEFAULTS = {"daily": 7, "weekly": 4, "manual": 10, "checkpoints": 5, "include_faces": False, "auto": True}
STATE_FILE = "backup_state.json"
_lock = threading.Lock()


class BackupError(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


# ---------------------------------------------------------------------- settings (memory.db `backup.*`)
def get_settings(home: Path) -> dict:
    out = dict(DEFAULTS)
    p = Path(home) / "memory.db"
    if not p.is_file():
        return out
    try:
        db = sqlite3.connect(str(p), timeout=10)
        try:
            for k, v in db.execute("SELECT key, value FROM settings WHERE key LIKE 'backup.%'"):
                name = k.removeprefix("backup.")
                if name in out:
                    out[name] = json.loads(v)
        finally:
            db.close()
    except (sqlite3.Error, ValueError):
        pass
    return out


def set_settings(home: Path, changes: dict) -> dict:
    clean = {}
    for k, v in (changes or {}).items():
        if k in ("daily", "weekly", "manual", "checkpoints"):
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a whole number")
            if not 0 <= n <= 365 or (k == "daily" and n < 1):
                raise ValueError(f"{k} must be between {1 if k == 'daily' else 0} and 365")
            clean[k] = n
        elif k in ("include_faces", "auto"):
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
            clean[k] = v
        else:
            raise ValueError(f"unknown backup setting {k}")
    db = sqlite3.connect(str(Path(home) / "memory.db"), timeout=10)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        for k, v in clean.items():
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = "
                       "excluded.value", (f"backup.{k}", json.dumps(v)))
        db.commit()
    finally:
        db.close()
    logs.info("backup", "BACKUP_SETTINGS", "backup settings changed", keys=sorted(clean))
    return get_settings(home)


# ---------------------------------------------------------------------- what goes in
def _is_sqlite(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _secret_reason(p: Path, rel: str) -> str | None:
    if p.name in EXCLUDE_FILES:
        return "credential or volatile file"
    if _SECRET_NAME.search(p.name):
        return "credential-like file name"
    if p.suffix.lower() in (".json", ".toml", ".md", ".txt", ".env", ".ini", ".yaml", ".yml") and p.stat().st_size < 2_000_000:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            if _SECRET_TEXT.search(text) or logs.has_secret(text):        # SW-V4 B: every token shape
                return "contains a credential"
        except OSError:
            return "unreadable"
    return None


def plan(home: Path, settings: dict | None = None) -> tuple[list[tuple[Path, str]], list[dict]]:
    """([(path, archive name)], [excluded {name, reason}]) for the data dir."""
    home = Path(home)
    settings = settings or get_settings(home)
    take, skipped = [], []
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home).as_posix()
        dirs = rel.split("/")[:-1]
        if any(part in EXCLUDE_DIRS or part.startswith(".restore-staging") for part in dirs):
            continue
        if (dirs[:1] == ["profiles"] or rel == "profiles.json") and not (home / "profile.json").is_file():
            continue            # the default profile's folder holds the OTHER profiles: never in its backup (#17)
        if p.is_symlink():
            skipped.append({"name": rel, "reason": "symlink"})
            continue
        if not p.is_file():
            continue
        if p.name.endswith(_SKIP_SUFFIX) or p.name.endswith(".bad.json"):
            continue
        if p.name == "faces.db" and not settings.get("include_faces"):
            skipped.append({"name": rel, "reason": "face data (biometric): backup.include_faces is off"})
            continue
        why = _secret_reason(p, rel)
        if why:
            skipped.append({"name": rel, "reason": why})
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            skipped.append({"name": rel, "reason": "larger than 512 MB"})
            continue
        take.append((p, rel))
    return take, skipped


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_copy(src: Path, dst: Path) -> None:
    """SQLite online backup API: a consistent copy even while another connection writes."""
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
        d.execute("PRAGMA journal_mode=DELETE")     # a self-contained file (no -wal sidecar) inside the archive
    finally:
        d.close()
        s.close()


# ---------------------------------------------------------------------- create
def backup_dir(home: Path) -> Path:
    return Path(home) / BACKUP_DIR


def create(home: Path, kind: str = "manual", *, note: str = "", now: float | None = None) -> Path:
    """Write and verify one archive; returns its path. Raises BackupError (nothing half-written is left)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    home = Path(home)
    settings = get_settings(home)
    now = time.time() if now is None else now
    out_dir = backup_dir(home)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    final = out_dir / f"jarves-backup-{stamp}-{kind}.zip"
    n = 1
    while final.exists():
        final = out_dir / f"jarves-backup-{stamp}-{kind}-{n}.zip"
        n += 1
    tmp = final.with_name(final.name + ".partial")
    take, skipped = plan(home, settings)
    files = []
    with _lock, tempfile.TemporaryDirectory(prefix="jarves-bk-") as work:
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for p, rel in take:
                    try:
                        if _is_sqlite(p):
                            snap = Path(work) / f"{len(files)}.db"
                            _sqlite_copy(p, snap)
                            src, typ = snap, "sqlite"
                        else:
                            src, typ = p, "file"
                        files.append({"name": rel, "type": typ, "size": src.stat().st_size, "sha256": _sha256(src)})
                        z.write(src, "data/" + rel)
                    except (OSError, sqlite3.Error) as e:
                        if isinstance(e, OSError) and getattr(e, "errno", None) == 28:     # ENOSPC
                            raise
                        skipped.append({"name": rel, "reason": f"unreadable ({type(e).__name__})"})
                        files = [f for f in files if f["name"] != rel]
                manifest = {"format": 1, "app": "jarves", "version": __version__, "created": now,
                            "created_text": datetime.fromtimestamp(now).isoformat(timespec="seconds"), "kind": kind,
                            "note": note[:200], "platform": sys.platform, "machine": platform.machine(),
                            "files": files, "excluded": skipped, "contains_credentials": False}
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            res = verify(tmp)
            if not res["ok"]:
                raise BackupError("BACKUP_VERIFY_FAILED", f"new backup failed verification: {res['errors'][:3]}",
                                  "check free disk space and the data folder; try `jarvis backup` again")
            os.replace(tmp, final)
        except BackupError:
            _unlink(tmp)
            raise
        except (OSError, sqlite3.Error, zipfile.BadZipFile) as e:
            _unlink(tmp)
            logs.error("backup", "BACKUP_FAILED", "backup could not be written", error_type=type(e).__name__)
            raise BackupError("BACKUP_FAILED", f"backup could not be written ({type(e).__name__}: {str(e)[:120]})",
                              "free some disk space or check folder permissions") from e
    _save_state(home, {"last": {"path": final.name, "ts": now, "kind": kind, "ok": True, "files": len(files)}},
                kind=kind)
    logs.info("backup", "BACKUP_DONE", "backup written", backup=final.name, kind=kind, files=len(files), excluded=len(skipped),
              bytes=final.stat().st_size)
    prune(home)
    return final


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def _state_path(home: Path) -> Path:
    return backup_dir(home) / STATE_FILE


def read_state(home: Path) -> dict:
    try:
        return json.loads(_state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(home: Path, changes: dict, kind: str | None = None) -> None:
    st = read_state(home)
    st.update(changes)
    if kind == "auto":
        st["last_auto"] = changes.get("last", {}).get("ts")
    p = _state_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------- verify
def read_manifest(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("manifest.json"))


def verify(path: Path) -> dict:
    """{"ok", "errors", "files", "manifest"}: zip CRCs, manifest SHA-256s, db integrity_check, no credentials."""
    path = Path(path)
    errors: list[str] = []
    man: dict = {}
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                errors.append(f"CRC error in {bad}")
            man = json.loads(z.read("manifest.json"))
            names = set(z.namelist())
            with tempfile.TemporaryDirectory(prefix="jarves-vf-") as work:
                for f in man.get("files", []):
                    member = "data/" + f["name"]
                    if member not in names:
                        errors.append(f"missing {f['name']}")
                        continue
                    h = hashlib.sha256()
                    with z.open(member) as src:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            h.update(chunk)
                    if h.hexdigest() != f["sha256"]:
                        errors.append(f"checksum mismatch {f['name']}")
                        continue
                    if f.get("type") == "sqlite":
                        p = Path(work) / "check.db"
                        p.write_bytes(z.read(member))
                        try:
                            db = sqlite3.connect(str(p))
                            try:
                                r = db.execute("PRAGMA integrity_check").fetchone()[0]
                            finally:
                                db.close()
                            if r != "ok":
                                errors.append(f"database damaged {f['name']}: {r[:80]}")
                        except sqlite3.Error as e:
                            errors.append(f"database unreadable {f['name']}: {type(e).__name__}")
                        _unlink(p)
                    elif Path(f["name"]).name in EXCLUDE_FILES or _SECRET_NAME.search(Path(f["name"]).name):
                        errors.append(f"credential file inside the archive: {f['name']}")
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as e:
        errors.append(f"not a readable JARVES backup ({type(e).__name__})")
    return {"ok": not errors, "errors": errors, "files": len(man.get("files", [])), "manifest": man,
            "name": path.name}


# ---------------------------------------------------------------------- list / retention
def list_backups(home: Path) -> list[dict]:
    out = []
    for p in sorted(backup_dir(home).glob("jarves-backup-*.zip"), reverse=True):
        m = re.match(r"jarves-backup-(\d{8}-\d{6})-([a-z-]+?)(?:-\d+)?\.zip$", p.name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
        except ValueError:
            continue
        out.append({"name": p.name, "kind": m.group(2), "ts": ts, "size": p.stat().st_size})
    return out


def prune(home: Path) -> list[str]:
    """Apply retention; returns the deleted names. Never deletes the newest backup of a kind."""
    s = get_settings(home)
    items = list_backups(home)
    keep: set[str] = set()
    autos = [b for b in items if b["kind"] == "auto"]
    days, weeks = {}, {}
    for b in autos:                                           # newest first
        d = datetime.fromtimestamp(b["ts"])
        days.setdefault(d.date().isoformat(), b["name"])
        weeks.setdefault("%d-W%02d" % d.isocalendar()[:2], b["name"])
    keep |= set(list(days.values())[:s["daily"]]) | set(list(weeks.values())[:s["weekly"]])
    for kind, n in (("manual", s["manual"]), ("pre-migration", s["checkpoints"]), ("pre-restore", s["checkpoints"])):
        keep |= {b["name"] for b in [x for x in items if x["kind"] == kind][:max(n, 1)]}
    if autos:
        keep.add(autos[0]["name"])
    gone = []
    for b in items:
        if b["name"] not in keep:
            try:
                (backup_dir(home) / b["name"]).unlink()
                gone.append(b["name"])
            except OSError:
                pass
    for p in backup_dir(home).glob("*.partial"):             # leftovers of a crash mid-write
        if time.time() - p.stat().st_mtime > 3600:
            _unlink(p)
    if gone:
        logs.info("backup", "BACKUP_PRUNED", "old backups removed", removed=len(gone))
    return gone


# ---------------------------------------------------------------------- restore
def core_running(home: Path, port: int | None = None) -> bool:
    """The last health snapshot says a Core runs on this data dir AND its link port answers (a crashed Core leaves
    a stale "ready" snapshot behind; that one must not block a restore)."""
    try:
        snap = json.loads((Path(home) / "health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if snap.get("core") in (None, "stopped"):
        return False
    import socket
    if port is None:
        from .config import Config
        port = Config().link.port
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _extract(path: Path, target: Path, man: dict) -> list[str]:
    """Stage every member next to the target, then swap file by file. Raises on any failure (caller rolls back)."""
    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-staging-", dir=target))
    done = []
    try:
        with zipfile.ZipFile(path) as z:
            for f in man["files"]:
                rel = f["name"]
                if rel.startswith(("/", "\\")) or ".." in Path(rel).parts or ":" in rel:
                    raise BackupError("RESTORE_BAD_PATH", f"unsafe path in archive: {rel}")
                dst = staging / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open("data/" + rel) as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
        for f in man["files"]:
            rel = f["name"]
            final = target / rel
            final.parent.mkdir(parents=True, exist_ok=True)
            if f.get("type") == "sqlite":
                for side in ("-wal", "-shm", "-journal"):
                    _unlink(final.with_name(final.name + side))
            os.replace(staging / rel, final)
            done.append(rel)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return done


def restore(path: Path, target: Path, *, force: bool = False, checkpoint_first: bool = True,
            port: int | None = None) -> dict:
    """Restore an archive into `target` (a data dir). Verified first; the previous data is checkpointed and put
    back if anything fails. Refuses while a Core runs on `target` (stop it first) unless force."""
    path, target = Path(path), Path(target)
    res = verify(path)
    if not res["ok"]:
        raise BackupError("RESTORE_VERIFY_FAILED", f"backup failed verification: {res['errors'][:3]}",
                          "choose another backup (`jarvis backup list`)")
    if checkpoint_first and core_running(target, port) and not force:
        raise BackupError("RESTORE_CORE_RUNNING", "JARVES is running on this data folder",
                          "stop it first (`jarvis stop`), then restore again")
    pre = None
    if checkpoint_first and target.is_dir() and any(target.glob("*.db")):
        pre = create(target, "pre-restore", note=f"before restoring {path.name}")
    man = res["manifest"]
    try:
        done = _extract(path, target, man)
    except Exception as e:
        logs.error("backup", "RESTORE_FAILED", "restore failed; putting the previous data back",
                   error_type=type(e).__name__)
        if pre is not None:
            _extract(pre, target, read_manifest(pre))
        raise BackupError("RESTORE_FAILED", f"restore failed ({type(e).__name__}); previous data kept",
                          f"your data before the restore is in {pre.name if pre else '(no checkpoint)'}") from e
    logs.info("backup", "RESTORE_DONE", "backup restored", backup=path.name, files=len(done), checkpoint=pre.name if pre else None)
    return {"ok": True, "restored": done, "checkpoint": pre.name if pre else None, "from": path.name,
            "sign_in_again": _sign_in_hints(target)}


def _sign_in_hints(target: Path) -> list[str]:
    hints = []
    if not (target / "secrets.env").is_file():
        hints.append("jarvis key  (OpenAI API key; never stored in backups)")
    if not (target / "chatgpt").is_dir():
        hints.append("jarvis chatgpt login  (if you use the ChatGPT plan)")
    if not (target / "google").is_dir():
        hints.append("jarvis google login  (if you use Google Calendar / Gmail)")
    return hints


def restore_test(path: Path) -> dict:
    """The restore drill: restore into a throw-away isolated profile and open the stores there."""
    path = Path(path)
    report: dict = {"name": path.name, "ok": False, "checks": []}
    with tempfile.TemporaryDirectory(prefix="jarves-restore-test-") as d:
        prof = Path(d) / "profile"
        try:
            r = restore(path, prof, checkpoint_first=False)
        except BackupError as e:
            report["error"] = e.message
            return report
        report["files"] = len(r["restored"])
        for db in sorted(prof.rglob("*.db")):
            try:
                c = sqlite3.connect(str(db))
                try:
                    ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    tables = [t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    rows = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables
                            if not t.startswith("sqlite_")}
                finally:
                    c.close()
                report["checks"].append({"db": db.relative_to(prof).as_posix(), "ok": ok, "rows": rows})
            except sqlite3.Error as e:
                report["checks"].append({"db": db.name, "ok": False, "error": type(e).__name__})
        try:                                                    # the real stores open the restored files
            from .memory import MemoryStore
            if (prof / "memory.db").is_file():
                m = MemoryStore(prof / "memory.db")
                report["memories"] = m.count()
                m.close()
            if (prof / "store.db").is_file():
                from .store import Store
                st = Store(prof / "store.db")
                report["conversations"] = st.rows("SELECT COUNT(*) n FROM sessions")[0]["n"]
                st.close()
        except Exception as e:
            report["checks"].append({"db": "open stores", "ok": False, "error": type(e).__name__})
        report["ok"] = all(c["ok"] for c in report["checks"])
    if path.parent.name == BACKUP_DIR:
        _save_state(path.parent.parent, {"last_restore_test": {"name": path.name, "ok": report["ok"],
                                                               "ts": time.time()}})
    logs.info("backup", "RESTORE_TEST", "restore test finished", ok=report["ok"], backup=path.name)
    return report


# ---------------------------------------------------------------------- checkpoints and automatic backups
def checkpoint(home: Path, reason: str = "migration") -> Path:
    """Before a database migration or update: a verified pre-migration backup. Raises BackupError on failure, so
    the caller can refuse to migrate and show recovery guidance."""
    return create(home, "pre-migration", note=reason)


def startup_checkpoint(home: Path, health=None) -> Path | None:
    """Core start: when the app version differs from the last run's, checkpoint before stores migrate."""
    home = Path(home)
    st = read_state(home)
    if st.get("version") == __version__:
        return None
    if not any(home.glob("*.db")):                  # first run: nothing to protect yet
        _save_state(home, {"version": __version__})
        return None
    try:
        p = checkpoint(home, f"version {st.get('version') or 'unknown'} -> {__version__}")
    except BackupError as e:
        logs.error("backup", e.code, "pre-migration checkpoint failed", detail=e.message, action=e.action)
        if health is not None:
            health.set("backup", "degraded", "pre-migration checkpoint failed", e.action)
        return None
    _save_state(home, {"version": __version__})
    return p


def due(home: Path, now: float | None = None) -> bool:
    s = get_settings(home)
    if not s.get("auto", True):
        return False
    last = read_state(home).get("last_auto")
    return last is None or (now or time.time()) - float(last) >= 20 * 3600


def maybe_auto(home: Path, health=None) -> Path | None:
    try:
        from . import features
        if "backup" in features.CATALOG and not features.current().enabled("backup"):
            return None
    except Exception:
        pass
    if not due(home) or not any(Path(home).glob("*.db")):
        return None
    try:
        p = create(home, "auto")
        if health is not None:
            health.set("backup", "ok", f"last backup {datetime.now():%Y-%m-%d %H:%M}")
        return p
    except BackupError as e:
        logs.error("backup", e.code, "backup operation failed", detail=e.message, action=e.action)
        if health is not None:
            health.set("backup", "degraded", e.message, e.action)
        return None


def start_auto(home: Path, health=None, *, first_delay_s: float = 120.0, every_s: float = 3600.0) -> threading.Thread:
    """Daily automatic backup in a daemon thread (first check after the Core settled, then hourly)."""
    stop = threading.Event()

    def run() -> None:
        if stop.wait(first_delay_s):
            return
        while True:
            maybe_auto(home, health)
            if stop.wait(every_s):
                return
    t = threading.Thread(target=run, name="jarves-backup", daemon=True)
    t.stop = stop                                               # type: ignore[attr-defined]
    t.start()
    return t


GUIDANCE = [
    "Backups are in <data folder>/backups. They never contain your API key or sign-in tokens.",
    "To check one: `jarvis backup verify NAME` or the dashboard's Test restore (restores into a throw-away profile).",
    "To restore: stop JARVES (`jarvis stop`), then `jarvis restore NAME`. Your current data is saved first as a "
    "pre-restore backup and put back automatically if anything fails.",
    "After restoring on another computer sign in again: `jarvis key`, `jarvis chatgpt login`, `jarvis google login`.",
    "If JARVES does not start after an update, restore the newest pre-migration backup the same way.",
]


# ---------------------------------------------------------------------- feature
def register_feature() -> None:
    from . import features
    f = features.register("backup", "גיבוי אוטומטי", "Automatic backup", True, stage="stable",
                          about="daily local backup of settings, memory, notes and history (no keys); restore drill")
    f.targets = {"windows": {"status": "implemented_unverified",
                             "evidence": "core/tests/test_backup.py (fault injection, isolated restore); real-profile "
                                         "restore drill by the user pending"},
                 "linux_arm64_pi": {"status": "implemented_unverified",
                                    "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3",
                                    "blocker": "stdlib zip + SQLite backup API only; SD/SSD restore drill on a Pi "
                                               "not done"}}


register_feature()


# ---------------------------------------------------------------------- dashboard
def _name(home: Path, body) -> Path | None:
    name = str((body or {}).get("name") or "")
    if not re.fullmatch(r"jarves-backup-[0-9A-Za-z-]+\.zip", name):
        return None
    p = backup_dir(home) / name
    return p if p.is_file() else None


def api_backups(ctx, m, body):
    return 200, {"backups": list_backups(ctx.home), "settings": get_settings(ctx.home), "state": read_state(ctx.home),
                 "guidance": GUIDANCE,
                 **locales.list_siblings("guidance", GUIDANCE), "core_running": core_running(ctx.home), "folder": str(backup_dir(ctx.home))}


def api_backup_now(ctx, m, body):
    try:
        p = create(ctx.home, "manual")
    except BackupError as e:
        return 500, {"error": e.message, "action": e.action}
    return 200, {"ok": True, "name": p.name}


def api_backup_verify(ctx, m, body):
    p = _name(ctx.home, body)
    if p is None:
        return 404, {"error": "no such backup"}
    r = verify(p)
    return 200, {k: r[k] for k in ("ok", "errors", "files", "name")}


def api_backup_test(ctx, m, body):
    p = _name(ctx.home, body)
    if p is None:
        return 404, {"error": "no such backup"}
    return 200, restore_test(p)


def api_backup_settings(ctx, m, body):
    try:
        return 200, {"settings": set_settings(ctx.home, body or {})}
    except ValueError as e:
        return 400, {"error": str(e)}


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/backup", api_backups)
    register_route("POST", r"/api/backup", api_backup_now)
    register_route("POST", r"/api/backup/verify", api_backup_verify)
    register_route("POST", r"/api/backup/test", api_backup_test)
    register_route("POST", r"/api/backup/settings", api_backup_settings)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    b = sub.add_parser("backup", help="local backups: now / list / verify NAME / test NAME / prune / settings")
    b.add_argument("action", nargs="?", default="now", choices=["now", "list", "verify", "test", "prune", "settings"])
    b.add_argument("name", nargs="?")
    b.add_argument("--daily", type=int)
    b.add_argument("--weekly", type=int)
    b.add_argument("--include-faces", choices=["on", "off"])
    b.add_argument("--auto", choices=["on", "off"])
    r = sub.add_parser("restore", help="restore a backup (stop JARVES first); --test = restore drill only")
    r.add_argument("name", help="backup file name (see `jarvis backup list`) or path")
    r.add_argument("--test", action="store_true", help="restore into a throw-away profile and check it")
    r.add_argument("--target", type=Path, help="restore into another data folder")
    r.add_argument("--yes", action="store_true")
    r.add_argument("--force", action="store_true", help="even if the Core seems to run (not recommended)")


def _resolve(home: Path, name: str | None) -> Path | None:
    if not name:
        items = list_backups(home)
        return backup_dir(home) / items[0]["name"] if items else None
    p = Path(name)
    return p if p.is_file() else (backup_dir(home) / name if (backup_dir(home) / name).is_file() else None)


def cmd_backup(a, cfg, home) -> int:
    try:
        if a.action == "now":
            p = create(home, "manual")
            print(f"backup written and verified: {p}")
        elif a.action == "list":
            items = list_backups(home)
            for b in items:
                print(f"{b['name']}  {b['kind']:13} {b['size'] / 1024:8.1f} KB")
            print(f"{len(items)} backup(s) in {backup_dir(home)}")
        elif a.action in ("verify", "test"):
            p = _resolve(home, a.name)
            if p is None:
                print("no such backup (see `jarvis backup list`)")
                return 1
            if a.action == "verify":
                r = verify(p)
                print(f"{p.name}: {'OK' if r['ok'] else 'FAILED'} ({r['files']} files)")
                for e in r["errors"]:
                    print("  -", e)
                return 0 if r["ok"] else 1
            r = restore_test(p)
            print(f"restore test {p.name}: {'PASSED' if r['ok'] else 'FAILED'}; {r.get('files', 0)} files, "
                  f"{r.get('memories', '?')} memories, {r.get('conversations', '?')} conversations")
            return 0 if r["ok"] else 1
        elif a.action == "prune":
            print(f"removed {len(prune(home))} old backup(s)")
        elif a.action == "settings":
            ch = {k: v for k, v in (("daily", a.daily), ("weekly", a.weekly)) if v is not None}
            for k in ("include_faces", "auto"):
                v = getattr(a, k)
                if v is not None:
                    ch[k] = v == "on"
            print(json.dumps(set_settings(home, ch) if ch else get_settings(home), indent=1))
    except (BackupError, ValueError) as e:
        print(f"error: {getattr(e, 'message', e)}" + (f" — {e.action}" if getattr(e, "action", "") else ""))
        return 1
    return 0


def cmd_restore(a, cfg, home) -> int:
    p = _resolve(home, a.name)
    if p is None:
        print("no such backup (see `jarvis backup list`)")
        return 1
    if a.test:
        a.action, a.name = "test", str(p)
        return cmd_backup(a, cfg, home)
    target = a.target or home
    if not a.yes:
        ans = input(f"Restore {p.name} into {target}? Current data is saved first. [y/N] ").strip().lower()
        if ans not in ("y", "yes", "כן"):
            print("cancelled")
            return 1
    try:
        r = restore(p, target, force=a.force, port=cfg.link.port)
    except BackupError as e:
        print(f"error: {e.message} — {e.action}")
        return 1
    print(f"restored {len(r['restored'])} files from {r['from']}" +
          (f"; previous data saved as {r['checkpoint']}" if r["checkpoint"] else ""))
    for h in r["sign_in_again"]:
        print("  sign in again:", h)
    return 0
