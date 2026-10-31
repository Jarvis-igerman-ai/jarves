"""Loopback-only authenticated UI, JSON API and live revision stream (stdlib).

No endpoint starts a process, approves a tool, merges code or publishes a release.
Worker mutations use the local CLI; the web surface contains owner planning only.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import Store

STATIC = Path(__file__).with_name("static")
BODY_LIMIT = 64 * 1024


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def access_token(home: Path) -> str:
    """Persist only in the per-user runtime, never the checkout or event log."""
    private_directory(home)
    path = home / "access.token"
    try:
        token = path.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{64}", token):
            return token
        raise ValueError("workflow access token is malformed; move it aside before restarting")
    except FileNotFoundError:
        token = secrets.token_hex(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return access_token(home)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(token)
        return token


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, home: Path, port: int = 8188, *, store: Store | None = None, profile_home: Path | None = None):
        self.home = Path(home)
        self.profile_home = profile_home or self.home.parent
        self.token = access_token(self.home)
        self.store = store or Store(self.home / "workflow.db")
        self.sessions: dict[str, float] = {}
        self.session_lock = threading.Lock()
        self.attempts: list[float] = []
        self.streams = threading.BoundedSemaphore(8)
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.cookie = f"jarves_workflow_{self.server_port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/#token={self.token}"

    def server_close(self) -> None:
        self.stopping.set()
        super().server_close()
        self.store.close()


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return  # access logs must never retain URL fragments, tasks or answers

    def _host_ok(self) -> bool:
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return (not origin or origin == self.server.origin) and self.headers.get("Sec-Fetch-Site", "same-origin") in (
            "same-origin", "none")

    def _authenticated(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        value = cookie.get(self.server.cookie)
        now = time.time()
        with self.server.session_lock:
            return bool(value and self.server.sessions.get(value.value, 0) > now)

    def _headers(self, status: int, kind: str, length: int | None, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, status: int, data: dict, extra: dict | None = None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body), extra)
        self.wfile.write(body)

    def _request_ok(self, mutation: bool = False) -> bool:
        if not self._host_ok() or not self._origin_ok():
            self._json(403, {"error": "same-origin loopback access only"})
            return False
        if mutation and self.headers.get("X-Workflow-Request") != "1":
            self._json(403, {"error": "missing request marker"})
            return False
        return True

    def do_GET(self):
        if not self._request_ok():
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self._json(200, {"ok": True, "service": "jarves-workflow"})
            return
        files = {"/": ("index.html", "text/html"), "/index.html": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                 "/style.css": ("style.css", "text/css")}
        if path in files:
            name, mime = files[path]
            try:
                body = (STATIC / name).read_bytes()
            except OSError:
                self._json(503, {"error": "workflow UI is not packaged"})
                return
            self._headers(200, mime + "; charset=utf-8", len(body))
            self.wfile.write(body)
            return
        if not self._authenticated():
            self._json(401, {"error": "open the private link printed by jarvis workflow open"})
            return
        if path == "/api/state":
            from .autopilot import agent_retention_s, view
            from .store import agent_roster
            state = self.server.store.snapshot(retention_s=agent_retention_s(self.server.home))
            state["autopilot"] = view(state, self.server.home)
            # The console list leaves out archived agents (#730); agent_counts keeps the totals.
            state["agents"] = agent_roster(state["agents"], state["tasks"], state["observed_at"],
                                           state["agent_counts"]["retention_s"])[0]
            from .autopilot_watch import autopilot_alerts
            state["alerts"] = list(state.get("alerts") or []) + [
                {"kind": a["kind"], "summary": a["summary"], "task_id": a.get("task_id"), "agent_id": a.get("agent_id")}
                for a in autopilot_alerts(self.server.store)]
            self._json(200, state)
        elif path == "/api/trace":
            from .trace import history
            query = parse_qs(urlsplit(self.path).query)
            try:
                filters = {k: query[k][0] for k in ("agent_id", "task_id", "since", "until", "before") if k in query}
                self._json(200, history(self.server.store, limit=100, **filters))
            except (ValueError, TypeError):
                self._json(400, {"error": "invalid trace filters"})
        elif path == "/api/events":
            self._stream()
        else:
            self._json(404, {"error": "not found"})

    def _stream(self):
        if not self.server.streams.acquire(blocking=False):
            self._json(429, {"error": "too many live views; close an unused tab"})
            return
        try:
            self.connection.settimeout(10)
            self._headers(200, "text/event-stream; charset=utf-8", None, {"Connection": "close"})
            self.close_connection = True
            last = None
            # Finite streams re-authenticate on reconnect; no immortal session after expiry.
            for _ in range(60):
                if self.server.stopping.is_set() or not self._authenticated():
                    break
                revision = self.server.store.snapshot()["revision"]
                if revision != last:
                    self.wfile.write(f"event: change\ndata: {revision}\n\n".encode())
                    last = revision
                else:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                if self.server.stopping.wait(1):
                    break
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return
        finally:
            self.server.streams.release()

    def do_POST(self):
        if not self._request_ok(mutation=True):
            self.close_connection = True
            return
        path = urlsplit(self.path).path
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json(415, {"error": "JSON required"})
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= BODY_LIMIT:
                self._json(413, {"error": "request too large or empty"})
                self.close_connection = True
                return
            self.connection.settimeout(10)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("request must be an object")
            if path == "/api/session":
                self._login(data)
                return
            if not self._authenticated():
                self._json(401, {"error": "private session required"})
                return
            store = self.server.store
            if path == "/api/owner":
                result = store.set_owner(data.get("mode", ""))
            elif path == "/api/tasks":
                result = store.create_task(data)
            elif path == "/api/trace/settings":
                from .trace import settings
                result = settings(store, data.get("mode"))
            elif path == "/api/trace/retro":
                from .trace import retro_bundle
                if set(data) - {"agent_id", "task_id", "since", "until"}:
                    raise ValueError("unsupported retrospective scope")
                result = retro_bundle(store, **data)
            elif m := re.fullmatch(r"/api/questions/([A-Za-z0-9_.:-]+)/answer", path):
                result = store.answer(m[1], data.get("answer", ""))
            elif m := re.fullmatch(r"/api/questions/([A-Za-z0-9_.:-]+)/defer", path):
                result = store.defer(m[1])
            elif m := re.fullmatch(r"/api/tasks/([A-Za-z0-9_.:-]+)/transition", path):
                # No web input can clear a stale-worker resource lock or grant execution permission.
                if set(data) - {"state", "implementation", "validation", "delivery", "blocker"}:
                    raise ValueError("unsupported task change")
                result = store.transition(m[1], data)
            elif path == "/api/dispatch":
                from .control import dispatch
                result = {"alerts": store.tick(), "assignments": dispatch(store, self.server.profile_home)}
            else:
                self._json(404, {"error": "not found"})
                return
            self._json(200, {"ok": True, "result": result})
        except (ValueError, TypeError, KeyError) as exc:
            self._json(400, {"error": str(exc)[:240]})
        except Exception:
            self._json(500, {"error": "workflow operation failed; no approval or execution was performed"})

    def _login(self, data):
        now = time.time()
        with self.server.session_lock:
            self.server.attempts = [t for t in self.server.attempts if now - t < 60]
            if len(self.server.attempts) >= 10:
                self._json(429, {"error": "wait a minute before trying again"})
                return
            self.server.attempts.append(now)
            value = data.get("token")
            if not isinstance(value, str) or not hmac.compare_digest(value, self.server.token):
                self._json(401, {"error": "invalid private link"})
                return
            self.server.sessions = {s: end for s, end in self.server.sessions.items() if end > now}
            if len(self.server.sessions) >= 32:
                self._json(429, {"error": "too many active sessions; restart the local console"})
                return
            session = secrets.token_hex(32)
            self.server.sessions[session] = now + 12 * 3600
        self._json(200, {"ok": True}, {"Set-Cookie": f"{self.server.cookie}={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"})


def serve(home: Path, port: int = 8188, *, ready=None, profile_home: Path | None = None) -> None:
    server = Server(home, port, profile_home=profile_home)
    if ready:
        ready(server)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
