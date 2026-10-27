"""OpenCode adapter, READ-ONLY (V7 slice D1, issue #404). A client of OpenCode's headless HTTP server
(`opencode serve`; docs https://opencode.ai/docs/server/ and /sdk/): `GET /global/health`, `GET /session`,
`GET /session/status`, `GET /event` (SSE). D2 (#405) adds exactly three writes, used only by the Orchestrator after
the owner approved: `POST /session` (create), `POST /session/:id/prompt_async` (send a prompt), `POST /session/:id/abort`.
Any other method or path is refused here (no delete, no share, no config, no shell). The server must be on loopback and the owner's server password (env var `OPENCODE_SERVER_PASSWORD`,
HTTP basic auth, user `opencode`) is required: no password -> AUTH_REQUIRED without a request. The password is
never logged, never stored in the config (only the NAME of the env var) and never put in an exception text.
Sessions map onto the Orchestrator's registry (provider "opencode", observe only): no second session controller."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import re
import socket
import time
from typing import Any, Iterator
from urllib.parse import quote, urlsplit

from .envelope import clean

FEATURE = "agent_opencode"
DEFAULT_URL = "http://127.0.0.1:4096"
DEFAULT_PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD"
DEFAULT_USER = "opencode"
TIMEOUT_S = 10.0
MAX_BODY = 2_000_000
MAX_EVENTS = 100
MAX_LINES = 1000                    # SSE lines read per call, data or keepalive
STATE_MAP = {"idle": "IDLE", "busy": "RUNNING", "retry": "RUNNING"}     # SessionStatus.type (docs: /session/status)


_ID = r"[A-Za-z0-9_-]{1,64}"
# the only writes (method, path pattern); everything else is refused before a connection is made
WRITE_ALLOWED = (("POST", re.compile(r"/session")), ("POST", re.compile(rf"/session/{_ID}/prompt_async")),
                 ("POST", re.compile(rf"/session/{_ID}/abort")))
MAX_PROMPT = 20_000


class OpenCodeError(Exception):
    """`code` is one of the explicit result codes (envelope.CODES); `detail` never holds a secret."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def check_loopback(url: str) -> tuple[str, int]:
    """(host, port) of a plain-http loopback URL, else OpenCodeError(NOT_LOOPBACK): never a LAN or public host."""
    u = urlsplit(url or DEFAULT_URL)
    host = (u.hostname or "").lower()
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if u.scheme != "http" or not ok or u.username or u.password:
        raise OpenCodeError("NOT_LOOPBACK", "the OpenCode server must be plain http on 127.0.0.1 / localhost / ::1")
    return ("127.0.0.1" if host == "localhost" else host.strip("[]")), u.port or 4096   # never resolve a name


class Client:
    def __init__(self, url: str = DEFAULT_URL, *, password_env: str = DEFAULT_PASSWORD_ENV, username: str = DEFAULT_USER,
                 timeout: float = TIMEOUT_S) -> None:
        self.host, self.port = check_loopback(url)
        self.timeout = timeout
        secret = os.environ.get(password_env or DEFAULT_PASSWORD_ENV, "")
        if not secret:
            raise OpenCodeError("AUTH_REQUIRED", f"set the server password in the environment variable {password_env}")
        tok = base64.b64encode(f"{username}:{secret}".encode()).decode()
        self._headers = {"Authorization": "Basic " + tok, "Accept": "application/json"}

    def _open(self, path: str, timeout: float | None = None, method: str = "GET", body: bytes | None = None
              ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        bare = path.split("?", 1)[0]
        if method != "GET" and not any(m == method and rx.fullmatch(bare) for m, rx in WRITE_ALLOWED):
            raise OpenCodeError("SERVER_ERROR", f"{method} {bare} is not an allowed OpenCode call")
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)
        try:
            conn.connect()
            peer = ipaddress.ip_address(conn.sock.getpeername()[0].split("%")[0])
            if not peer.is_loopback:                                  # re-verify the connected peer
                raise OpenCodeError("NOT_LOOPBACK", "the connected address is not loopback")
            headers = dict(self._headers)
            if body is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=body, headers=headers)    # GET, or one of the three allowed writes
            r = conn.getresponse()
        except OpenCodeError:
            conn.close()
            raise
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            raise OpenCodeError("PROVIDER_MISSING", f"no OpenCode server answers on {self.host}:{self.port}"
                                f" ({type(e).__name__})") from None
        if r.status in (401, 403):
            conn.close()
            raise OpenCodeError("AUTH_REQUIRED", "the server refused the password")
        if not 200 <= r.status < 300:                     # no redirect is followed; a 3xx is not a success
            conn.close()
            raise OpenCodeError("SERVER_ERROR", f"HTTP {r.status} on {bare}")
        return conn, r

    def get_json(self, path: str) -> Any:
        conn, r = self._open(path)
        try:
            raw = r.read(MAX_BODY + 1)
        except (OSError, http.client.HTTPException) as e:
            raise OpenCodeError("SERVER_ERROR", f"read failed ({type(e).__name__})") from None
        finally:
            conn.close()
        if len(raw) > MAX_BODY:
            raise OpenCodeError("SERVER_ERROR", "answer too large")
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise OpenCodeError("SERVER_ERROR", f"{path}: not JSON") from None

    def _write(self, path: str, payload: dict) -> Any:
        conn, r = self._open(path, method="POST", body=json.dumps(payload).encode("utf-8"))
        try:
            raw = r.read(MAX_BODY + 1)
        except (OSError, http.client.HTTPException) as e:
            raise OpenCodeError("SERVER_ERROR", f"read failed ({type(e).__name__})") from None
        finally:
            conn.close()
        if not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return {}

    @staticmethod
    def _dir(directory: str) -> str:
        if not directory or len(directory) > 400 or "\x00" in directory:
            raise OpenCodeError("SERVER_ERROR", "bad directory")
        return "?directory=" + quote(directory, safe="")

    @staticmethod
    def _sid(session_id: str) -> str:
        if not re.fullmatch(_ID, str(session_id or "")):
            raise OpenCodeError("SERVER_ERROR", "bad session id")
        return str(session_id)

    def create_session(self, directory: str, title: str = "") -> str:
        """POST /session in `directory` (the worktree): returns the new session id."""
        d = self._write("/session" + self._dir(directory), {"title": clean(title, 120)})
        sid = d.get("id") if isinstance(d, dict) else None
        if not isinstance(sid, str) or not re.fullmatch(_ID, sid):
            raise OpenCodeError("SERVER_ERROR", "the server did not return a session id")
        got = str(d.get("directory") or "")
        if _norm_dir(got) != _norm_dir(directory):         # the session must live in the worktree we asked for
            try:
                self.abort(sid, got or directory)          # the folder the SERVER put it in is where the session lives
            except OpenCodeError:
                pass
            raise OpenCodeError("SERVER_ERROR", "the session was created in another folder than the worktree")
        return sid

    def prompt(self, session_id: str, text: str, directory: str) -> None:
        """POST /session/:id/prompt_async: ONE text part, asynchronous (answers 204). The caller has the owner's approval."""
        text = str(text or "")
        if not text.strip() or len(text) > MAX_PROMPT:
            raise OpenCodeError("SERVER_ERROR", "empty or too long prompt")
        self._write(f"/session/{self._sid(session_id)}/prompt_async" + self._dir(directory),
                    {"parts": [{"type": "text", "text": text}]})

    def abort(self, session_id: str, directory: str) -> None:
        """POST /session/:id/abort: stops the running turn."""
        self._write(f"/session/{self._sid(session_id)}/abort" + self._dir(directory), {})

    def health(self) -> dict:
        d = self.get_json("/global/health")
        if not isinstance(d, dict) or not d.get("healthy"):
            raise OpenCodeError("SERVER_ERROR", "server reports unhealthy")
        return {"healthy": True, "version": clean(d.get("version"), 40)}

    def sessions(self) -> list[dict]:
        """Session views merged with `GET /session/status` ({id: {type: idle|busy|retry}}); titles are untrusted."""
        rows, status = self.get_json("/session"), self.get_json("/session/status")
        if not isinstance(rows, list) or not isinstance(status, dict):
            raise OpenCodeError("SERVER_ERROR", "unexpected session shape")
        return [session_view(r, status.get(r.get("id"))) for r in rows if isinstance(r, dict) and r.get("id")]

    def events(self, limit: int = 20, timeout: float | None = None) -> list[dict]:
        """Up to `limit` events of the SSE stream (`GET /event`), then closes; stops at `timeout` seconds."""
        out: list[dict] = []
        limit = max(1, min(int(limit), MAX_EVENTS))
        try:
            for ev in self.stream(timeout=timeout or self.timeout):
                out.append(ev)
                if len(out) >= limit:
                    break
        except (socket.timeout, TimeoutError):
            pass
        return out

    def stream(self, timeout: float) -> Iterator[dict]:
        conn, r = self._open("/event", timeout)
        deadline = time.monotonic() + timeout            # overall: keepalive lines cannot hold the stream open
        lines = 0
        try:
            while time.monotonic() < deadline and lines < MAX_LINES:
                lines += 1
                line = r.readline(65536)
                if not line:
                    return
                line = line.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    try:
                        d = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(d, dict):
                        yield event_view(d)
        except (OSError, http.client.HTTPException) as e:
            if isinstance(e, (socket.timeout, TimeoutError)):
                raise
            raise OpenCodeError("SERVER_ERROR", f"stream failed ({type(e).__name__})") from None
        finally:
            conn.close()


def _norm_dir(p: str) -> str:
    return str(p or "").replace("\\", "/").rstrip("/").lower()


def _ms(t: Any) -> float | None:
    return float(t) / 1000.0 if isinstance(t, (int, float)) and t else None


def session_view(s: dict, status: Any = None) -> dict:
    """Session (+ SessionStatus) -> the fields the Orchestrator registry keeps (the codex.thread_view shape)."""
    t = s.get("time") if isinstance(s.get("time"), dict) else {}
    kind = str((status or {}).get("type") or "") if isinstance(status, dict) else ""
    return {"provider_session_id": str(s["id"])[:120], "cwd": str(s.get("directory") or "")[:400],
            "name": clean(s.get("title"), 120), "kind": "subagent" if s.get("parentID") else "main",
            "state": STATE_MAP.get(kind, "IDLE" if status is None else "UNKNOWN"), "branch": "",
            "started_at": _ms(t.get("created")), "updated_at": _ms(t.get("updated"))}


def event_view(d: dict) -> dict:
    """An SSE event `{type, properties}` -> type + the session id only (event text is never passed on)."""
    p = d.get("properties") if isinstance(d.get("properties"), dict) else {}
    info = p.get("info") if isinstance(p.get("info"), dict) else {}
    sid = p.get("sessionID") or info.get("sessionID") or info.get("id")
    return {"type": clean(d.get("type"), 60), "session": clean(sid, 120)}
