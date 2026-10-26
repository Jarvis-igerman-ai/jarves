"""OpenCode adapter, READ-ONLY (V7 slice D1, issue #404). A client of OpenCode's headless HTTP server
(`opencode serve`; docs https://opencode.ai/docs/server/ and /sdk/): `GET /global/health`, `GET /session`,
`GET /session/status`, `GET /event` (SSE). Only these four GET calls exist here: no start, prompt, abort, delete or
any other write. The server must be on loopback and the owner's server password (env var `OPENCODE_SERVER_PASSWORD`,
HTTP basic auth, user `opencode`) is required: no password -> AUTH_REQUIRED without a request. The password is
never logged, never stored in the config (only the NAME of the env var) and never put in an exception text.
Sessions map onto the Orchestrator's registry (provider "opencode", observe only): no second session controller."""
from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import os
import socket
from typing import Any, Iterator
from urllib.parse import urlsplit

from .envelope import clean

FEATURE = "agent_opencode"
DEFAULT_URL = "http://127.0.0.1:4096"
DEFAULT_PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD"
DEFAULT_USER = "opencode"
TIMEOUT_S = 10.0
MAX_BODY = 2_000_000
STATE_MAP = {"idle": "IDLE", "busy": "RUNNING", "retry": "RUNNING"}     # SessionStatus.type (docs: /session/status)


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
    return host, u.port or 4096


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

    def _open(self, path: str, timeout: float | None = None) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)
        try:
            conn.request("GET", path, headers=self._headers)          # GET only: this adapter never writes
            r = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            raise OpenCodeError("PROVIDER_MISSING", f"no OpenCode server answers on {self.host}:{self.port}"
                                f" ({type(e).__name__})") from None
        if r.status in (401, 403):
            conn.close()
            raise OpenCodeError("AUTH_REQUIRED", "the server refused the password")
        if r.status >= 400:
            conn.close()
            raise OpenCodeError("SERVER_ERROR", f"HTTP {r.status} on {path}")
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
        try:
            while True:
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
