"""Fakes for the installer / update tests (SW-V4 package L): a local release server on 127.0.0.1 that serves a SIGNED
latest.json and installer files, legacy-layout user data, and a data snapshot to compare before/after. Temp dirs and
loopback only: no real install, PATH, registry or network."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarves import memory, store, updates, userdata

BASE = "https://releases.example.test"          # what the app is configured with; the adapter maps it to loopback


class Keys:
    def __init__(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        raw = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public = base64.b64encode(raw).decode()

    def sign(self, manifest: dict) -> dict:
        sig = self.private.sign(updates.canonical(manifest))
        return {"format": 1, "manifest": manifest,
                "signature": {"alg": "ed25519", "key_id": "test", "sig": base64.b64encode(sig).decode()}}


def installer_bytes(version: str, size: int = 4096) -> bytes:
    head = f"MZ fake JARVES installer {version}\n".encode()
    return (head + hashlib.sha256(head).digest() * (size // 32))[:size]


def manifest(version: str, exe: bytes, *, previous: dict | None = None, url: str | None = None, **over) -> dict:
    name = f"JARVES-Setup-{version}.exe"
    m = {"product": "jarves", "platform": "windows-x64", "channel": "main", "version": version, "commit": "abc",
         "built": "2026-10-20T10:00:00Z", "installer": name, "sha256": hashlib.sha256(exe).hexdigest(),
         "size": len(exe), "url": url or f"{BASE}/{name}", "published": "2026-10-20T10:00:00Z"}
    if previous:
        m["previous"] = previous
    m.update(over)
    return m


class FakeReleaseServer:
    """GET /latest.json and /<installer>. `files` maps a path to bytes; `status` overrides the answer per path
    (e.g. {"/JARVES-Setup-4.0.0.1.exe": 500}); `cut` truncates a download after N bytes."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.status: dict[str, int] = {}
        self.cut: dict[str, int] = {}
        self.hits: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):          # silent
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                code = outer.status.get(self.path, 200 if self.path in outer.files else 404)
                body = outer.files.get(self.path, b"") if code == 200 else b""
                if code == 200 and self.path in outer.cut:
                    body = body[:outer.cut[self.path]]
                self.send_response(code)
                self.send_header("Content-Type", "application/json" if self.path.endswith(".json") else
                                 "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)

    def publish(self, keys: Keys, version: str, *, previous: tuple[str, bytes] | None = None, tamper=None,
                exe: bytes | None = None, **over) -> bytes:
        """Serve version's installer and a signed latest.json. `tamper(manifest)` edits the manifest AFTER signing."""
        exe = exe if exe is not None else installer_bytes(version)
        prev = None
        if previous:
            pv, pexe = previous
            self.files[f"/JARVES-Setup-{pv}.exe"] = pexe
            prev = {"version": pv, "installer": f"JARVES-Setup-{pv}.exe", "sha256": hashlib.sha256(pexe).hexdigest(),
                    "size": len(pexe), "url": f"{BASE}/JARVES-Setup-{pv}.exe"}
        env = keys.sign(manifest(version, exe, previous=prev, **over))
        if tamper:
            tamper(env["manifest"])
        self.files["/latest.json"] = json.dumps(env).encode()
        self.files[f"/JARVES-Setup-{version}.exe"] = exe
        return exe

    def http(self):
        """The `http` callable of UpdateChecker: maps https://releases.example.test/x to the loopback server.
        Raises OSError when the server is closed (= offline)."""
        port = self.port

        def get(url: str, headers: dict):
            assert url.startswith(BASE + "/"), url          # the app only ever asks the configured HTTPS base
            req = urllib.request.Request(f"http://127.0.0.1:{port}{url[len(BASE):]}")
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    data = r.read()
                    if "json" in r.headers.get("Content-Type", ""):
                        return 200, json.loads(data)
                    return 200, data
            except urllib.error.HTTPError as e:
                return e.code, None
        return get


# ---------------------------------------------------------------------- user data
def seed_legacy(home: Path) -> None:
    """A data folder as the old 0.1.0.x series left it: no schema.json, store.db / memory.db without the later
    columns and tables, plus a conversation, a memory, a job and settings."""
    home.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(home / "store.db")
    db.executescript(store.SCHEMA)
    db.execute("INSERT INTO sessions (id, kind, started) VALUES ('s1', 'typed', 1000.0)")
    db.execute("INSERT INTO turns (session_id, ts, role, text, chars) VALUES ('s1', 1001.0, 'user', 'hello legacy', 12)")
    db.execute("INSERT INTO delegations (session_id, ts, kind, status) VALUES ('s1', 1002.0, 'voice', 'done')")
    db.commit()
    db.close()
    db = sqlite3.connect(home / "memory.db")
    db.executescript(memory.SCHEMA)
    db.executescript(userdata.SCHEMA)
    db.execute("INSERT INTO memories (text, created, updated) VALUES ('likes tea', '2026-01-01', '2026-01-01')")
    db.execute("INSERT INTO settings (key, value) VALUES ('override:voice.voice', 'marin')")
    db.execute("INSERT INTO jobs (id, name, status, kind, spec, tz, action, task, created, updated) VALUES "
               "('job1', 'morning', 'active', 'daily', '08:00', 'Asia/Jerusalem', 'reminder', 'stretch', 1.0, 1.0)")
    db.commit()
    db.close()
    (home / "config.toml").write_text('[voice]\nmode = "off"\n\n[link]\nport = 18777\n', encoding="utf-8")
    (home / "features.json").write_text(json.dumps({"chimes": {"enabled": False}}), encoding="utf-8")
    (home / "identity.json").write_text(json.dumps({"display_name": "Max"}), encoding="utf-8")
    (home / "secrets.env").write_text("OPENAI_API_KEY=sk-test-never-copied-0123456789\n", encoding="utf-8")


SKIP_DIRS = {"logs", "backups", "updates"}
SKIP_FILES = {"backup_state.json", "schema.json", "health.json", "updates.json"}


def snapshot(home: Path, *, include_marker: bool = False) -> dict:
    """What the user would notice: every SQLite table's rows (sorted) and every other file's bytes. Logs, backups,
    the updater's own folder and the schema marker are not user data."""
    out = {}
    for p in sorted(Path(home).rglob("*")):
        rel = p.relative_to(home).as_posix()
        if not p.is_file() or set(rel.split("/")[:-1]) & SKIP_DIRS or p.name.endswith(("-wal", "-shm")):
            continue
        if p.name in SKIP_FILES and not (include_marker and p.name == "schema.json"):
            continue
        if p.suffix == ".db":
            db = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
            tables = {}
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                      "AND name NOT LIKE 'recall_fts%' ORDER BY name"):
                cols = [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')]
                tables[name] = (cols, sorted(map(repr, db.execute(f'SELECT * FROM "{name}"'))))
            db.close()
            out[rel] = tables
        else:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def user_rows(home: Path) -> dict:
    """The seeded content only (not the schema): present before and after an upgrade."""
    s = sqlite3.connect(Path(home) / "store.db")
    m = sqlite3.connect(Path(home) / "memory.db")
    try:
        return {"turn": s.execute("SELECT text FROM turns").fetchall(), "session": s.execute("SELECT id FROM sessions").fetchall(),
                "memory": m.execute("SELECT text FROM memories").fetchall(),
                "setting": m.execute("SELECT value FROM settings WHERE key='override:voice.voice'").fetchall(),
                "job": m.execute("SELECT name, task FROM jobs").fetchall()}
    finally:
        s.close()
        m.close()
