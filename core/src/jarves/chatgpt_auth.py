"""Sign in with ChatGPT (official OAuth, token sharing for open-source local tools) — optional connection.

Lets JARVES use the user's ChatGPT plan for eligible text requests (`POST /v1/responses`) instead of an API key.
Spec checked 2026-10-18: developers.openai.com/siwc/token-sharing-open-source (sign-in, profiles-and-sessions,
token-reference, errors-and-recovery, preview-limitations).

- Authorization code + PKCE (S256) in the system browser, loopback redirect `http://127.0.0.1:<port>/auth/callback`.
- First sign-in registers a dynamic client (`client_id=dynamic_agent_client`); the issued client ID is kept.
- Stable opaque host ID (`urn:uuid:<uuid4>`), never derived from user data.
- ID token verified against OpenAI's JWKS (issuer, audience, expiry, nonce). Plan usage requires the granted
  `chatgpt.tokens.use.direct` scope.
- Tokens live only in <state_dir>/chatgpt/account.json (atomic write, owner-only), never in logs or config.
- Refresh rotates the refresh token; refreshes are serialized across processes with a lock file.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import logs

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
REVOKE_URL = f"{ISSUER}/api/accounts/oauth/revoke"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
DYNAMIC_CLIENT = "dynamic_agent_client"
CALLBACK_PATH = "/auth/callback"
DEFAULT_PORT = 1455
REFRESH_MARGIN_S = 120
TERMINAL_REFRESH_ERRORS = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
                           "refresh_token_invalidated", "refresh_token_reused"}


class ChatGPTAuthError(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)       # no-op on Windows; %LOCALAPPDATA% is already per-user
    except OSError:
        pass


@dataclass
class _Callback:
    params: dict | None = None


class ChatGPTAuth:
    def __init__(self, home: Path, *, transport: httpx.AsyncBaseTransport | None = None,
                 jwks_client=None, open_browser=webbrowser.open) -> None:
        self.dir = home / "chatgpt"
        self.file = self.dir / "account.json"
        self._transport = transport
        self._jwks = jwks_client
        self._open_browser = open_browser
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ storage
    def load(self) -> dict:
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, acct: dict) -> None:
        _write_private(self.file, acct)

    def host_id(self) -> str:
        p = self.dir / "host_id"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        hid = f"urn:uuid:{uuid.uuid4()}"
        self.dir.mkdir(parents=True, exist_ok=True)
        p.write_text(hid, encoding="utf-8")
        return hid

    def status(self) -> dict:
        """Safe summary for health/CLI (no tokens)."""
        a = self.load()
        if not a.get("refresh_token"):
            return {"signed_in": False, "email": a.get("email", ""), "plan_usage": False}
        return {"signed_in": True, "email": a.get("email", ""), "plan_usage": PLAN_SCOPE in a.get("scopes", []),
                "access_expires_in_s": int(a.get("expires_at", 0) - time.time())}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=20, transport=self._transport)

    # ------------------------------------------------------------------ sign-in
    def authorize_url(self, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
        a = self.load()
        q = {"response_type": "code", "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
             "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256",
             "ext_agent_host_id": self.host_id()}
        if a.get("client_id"):
            q["client_id"] = a["client_id"]
            if a.get("id_token"):
                q["id_token_hint"] = a["id_token"]
            if a.get("email"):
                q["login_hint"] = a["email"]
        else:
            q["client_id"] = DYNAMIC_CLIENT
            q["agent_name_hint"] = "JARVES"
        return f"{AUTHORIZE_URL}?{urlencode(q)}"

    async def _serve_callback(self, port: int) -> tuple[asyncio.AbstractServer, _Callback, asyncio.Event]:
        got, done = _Callback(), asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
                while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                    pass
                parts = line.split(" ")
                url = urlparse(parts[1]) if len(parts) > 1 else None
                if url is None or url.path != CALLBACK_PATH or done.is_set():
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    return
                got.params = {k: v[0] for k, v in parse_qs(url.query).items()}
                ok = "error" not in got.params
                body = ("<html><body><h2>JARVES: " + ("connected to ChatGPT. You can close this tab."
                        if ok else "sign-in was not completed.") + "</h2></body></html>").encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n"
                             + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            except (asyncio.TimeoutError, ConnectionError, IndexError):
                pass
            finally:
                try:
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                if got.params is not None:
                    done.set()             # only after the browser received the result page

        try:
            server = await asyncio.start_server(handle, "127.0.0.1", port)
        except OSError:
            server = await asyncio.start_server(handle, "127.0.0.1", 0)    # only the port may change
        return server, got, done

    async def sign_in(self, timeout_s: float = 300, port: int = DEFAULT_PORT) -> dict:
        """Interactive: opens the browser for the user's own consent. Returns status()."""
        verifier, challenge = pkce_pair()
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        server, got, done = await self._serve_callback(port)     # listener first, then the browser
        try:
            real_port = server.sockets[0].getsockname()[1]
            redirect_uri = f"http://127.0.0.1:{real_port}{CALLBACK_PATH}"
            url = self.authorize_url(redirect_uri, state, nonce, challenge)
            if not self._open_browser(url):
                print(f"Open this link in your browser to sign in:\n{url}")
            try:
                await asyncio.wait_for(done.wait(), timeout_s)
            except asyncio.TimeoutError as e:
                raise ChatGPTAuthError("CHATGPT_SIGNIN_TIMEOUT", "sign-in was not completed in time",
                                       "run `jarves chatgpt login` again") from e
        finally:
            server.close()
        return await self.complete(got.params or {}, state, nonce, verifier, redirect_uri)

    async def complete(self, p: dict, state: str, nonce: str, verifier: str, redirect_uri: str) -> dict:
        """Validate the callback, exchange the code, verify the ID token and store the account."""
        if not secrets.compare_digest(p.get("state", ""), state):
            raise ChatGPTAuthError("CHATGPT_BAD_STATE", "sign-in callback did not match this attempt", "try again")
        if p.get("error"):
            code = "CHATGPT_CONSENT_DENIED" if p["error"] == "access_denied" else "CHATGPT_SIGNIN_ERROR"
            raise ChatGPTAuthError(code, f"sign-in ended: {p['error']}", "run `jarves chatgpt login` to try again")
        prev = self.load()
        client_id = p.get("client_id") or prev.get("client_id", "")
        if prev.get("client_id") and client_id != prev["client_id"]:
            raise ChatGPTAuthError("CHATGPT_CLIENT_MISMATCH", "sign-in returned a different app registration",
                                   "try again; if it repeats, run `jarves chatgpt logout --forget`")
        if not client_id or not p.get("code"):
            raise ChatGPTAuthError("CHATGPT_SIGNIN_ERROR", "sign-in callback had no code", "try again")
        tok = await self._token({"grant_type": "authorization_code", "client_id": client_id, "code": p["code"],
                                 "code_verifier": verifier, "redirect_uri": redirect_uri, "resource": RESOURCE})
        claims = await self._verify_id_token(tok.get("id_token", ""), client_id, nonce)
        if prev.get("subject") and prev["subject"] != claims["sub"] and prev.get("refresh_token"):
            logs.info("chatgpt", "CHATGPT_ACCOUNT_SWITCH", "signed in with a different ChatGPT account")
        acct = {"issuer": ISSUER, "client_id": client_id, "ext_agent_host_id": self.host_id(),
                "subject": claims["sub"], "email": claims.get("email", ""), "id_token": tok["id_token"],
                "models": [], "saved_at": time.time()}
        acct.update(self._token_fields(tok))
        self._save(acct)
        st = self.status()
        logs.info("chatgpt", "CHATGPT_SIGNED_IN", "ChatGPT connected", plan_usage=st["plan_usage"])
        return st

    @staticmethod
    def _token_fields(tok: dict) -> dict:
        return {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
                "token_type": tok.get("token_type", "Bearer"), "scopes": tok.get("scope", "").split(),
                "expires_at": time.time() + float(tok.get("expires_in", 3600))}

    async def _token(self, form: dict) -> dict:
        try:
            async with self._client() as c:
                r = await c.post(TOKEN_URL, data=form)
        except httpx.HTTPError as e:
            raise ChatGPTAuthError("CHATGPT_OFFLINE", "cannot reach auth.openai.com", "check the internet") from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200:
            err = body.get("error", f"http_{r.status_code}") if isinstance(body, dict) else f"http_{r.status_code}"
            if isinstance(err, dict):
                err = err.get("code", "error")
            raise ChatGPTAuthError(f"oauth:{err}", f"token request failed ({err})", "")
        return body

    async def _verify_id_token(self, id_token: str, audience: str, nonce: str) -> dict:
        import jwt
        try:
            jwks = self._jwks or jwt.PyJWKClient(JWKS_URL, cache_keys=True)
            key = await asyncio.to_thread(jwks.get_signing_key_from_jwt, id_token)
            claims = jwt.decode(id_token, key.key, algorithms=["RS256"], audience=audience, issuer=ISSUER,
                                options={"require": ["exp", "iat", "sub"]})
        except jwt.PyJWTError as e:
            raise ChatGPTAuthError("CHATGPT_BAD_ID_TOKEN", f"ID token rejected: {type(e).__name__}", "try again") from e
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ChatGPTAuthError("CHATGPT_BAD_ID_TOKEN", "ID token nonce mismatch", "try again")
        return claims

    # ------------------------------------------------------------------ tokens for inference
    async def access_token(self) -> str:
        a = self.load()
        if not a.get("refresh_token"):
            raise ChatGPTAuthError("CHATGPT_SIGNED_OUT", "not connected to ChatGPT", "run `jarves chatgpt login`")
        if PLAN_SCOPE not in a.get("scopes", []):
            raise ChatGPTAuthError("CHATGPT_NO_PLAN_USAGE", "ChatGPT plan usage was not granted",
                                   "run `jarves chatgpt login` and allow plan usage, or use an API key")
        if a.get("expires_at", 0) - time.time() > REFRESH_MARGIN_S:
            return a["access_token"]
        async with self._lock:
            return await self._refresh_locked()

    async def _refresh_locked(self) -> str:
        lock = self.dir / "refresh.lock"
        deadline = time.time() + 15
        while True:                           # cross-process: the Core and the CLI share one rotating token
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 30:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise ChatGPTAuthError("CHATGPT_REFRESH_BUSY", "token refresh is locked", "try again")
                await asyncio.sleep(0.2)
        try:
            a = self.load()                   # another process may have refreshed already
            if a.get("expires_at", 0) - time.time() > REFRESH_MARGIN_S:
                return a["access_token"]
            try:
                tok = await self._token({"grant_type": "refresh_token", "client_id": a["client_id"],
                                         "refresh_token": a["refresh_token"], "resource": RESOURCE})
            except ChatGPTAuthError as e:
                err = e.code.removeprefix("oauth:")
                if err in TERMINAL_REFRESH_ERRORS:
                    self._clear_tokens(a)
                    logs.warn("chatgpt", "CHATGPT_SESSION_ENDED", f"refresh rejected ({err})")
                    raise ChatGPTAuthError("CHATGPT_SIGNED_OUT", "the ChatGPT connection ended",
                                           "run `jarves chatgpt login` to reconnect") from e
                if e.code == "CHATGPT_OFFLINE":
                    raise
                raise ChatGPTAuthError("CHATGPT_REFRESH_FAILED", e.message, "see logs") from e
            a.update(self._token_fields(tok))        # access, expiry, scopes and rotating refresh token together
            if not tok.get("refresh_token"):
                a["refresh_token"] = self.load().get("refresh_token", "")
            a["saved_at"] = time.time()
            self._save(a)
            logs.info("chatgpt", "CHATGPT_REFRESHED", "ChatGPT access renewed")
            return a["access_token"]
        finally:
            lock.unlink(missing_ok=True)

    def _clear_tokens(self, a: dict) -> None:
        """Keep client ID, host ID, account label and ID token (for id_token_hint); drop usable tokens."""
        for k in ("access_token", "refresh_token", "expires_at", "scopes"):
            a.pop(k, None)
        self._save(a)

    def mark_revoked(self) -> None:
        a = self.load()
        if a:
            self._clear_tokens(a)

    def save_models(self, models: list[dict]) -> None:
        a = self.load()
        if a:
            a["models"] = models
            self._save(a)

    async def sign_out(self, forget: bool = False) -> bool:
        """Revoke the refresh token remotely, then clear locally. Returns True if revocation was confirmed."""
        a = self.load()
        confirmed = False
        if a.get("refresh_token"):
            for attempt in range(3):
                try:
                    async with self._client() as c:
                        r = await c.post(REVOKE_URL, data={"token": a["refresh_token"],
                                                           "token_type_hint": "refresh_token",
                                                           "client_id": a["client_id"]})
                    if r.status_code == 200:
                        confirmed = True
                        break
                    if r.status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5 * (attempt + 1))
        if forget:
            self.file.unlink(missing_ok=True)
        elif a:
            self._clear_tokens(a)
        logs.info("chatgpt", "CHATGPT_SIGNED_OUT", "ChatGPT disconnected", revoked=confirmed)
        return confirmed
