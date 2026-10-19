"""Google sign-in for the Calendar and Gmail Skills (SW-V2 WP-G, Issue #17 items 12-13).

Protocol checked 2026-10-19 against Google's own pages (links in docs/GOOGLE_SETUP.md):
- "OAuth 2.0 for iOS & Desktop Apps": authorization code + PKCE (S256) in the system browser, redirect to a
  loopback listener `http://127.0.0.1:<random port>`; custom schemes and the copy-paste (OOB) flow are no longer
  supported, the limited-input device flow does not allow Gmail/Calendar scopes.
- Desktop OAuth client (`client_secret_*.json`, "installed" type), `access_type=offline` + `prompt=consent` so a
  refresh token is issued. In Testing mode Google issues refresh tokens that expire after 7 days: the refresh then
  fails with `invalid_grant` and JARVES says "צריך לחבר מחדש את גוגל" (status `needs_reconnect`).
- Revocation: POST https://oauth2.googleapis.com/revoke.

We call the REST endpoints with httpx (already a Core dependency) instead of google-api-python-client (in
maintenance mode, large discovery layer) and google-auth-oauthlib: the flow is small, fully testable with
httpx.MockTransport, and light on the Pi.

Minimal scopes (user decision 19 Oct: all calendars, read; Gmail read + send only after read-back + approval):
- calendar.events.readonly       read events on all the user's calendars
- calendar.calendarlist.readonly list the calendars (names, which to include)
- gmail.readonly                 search/read mail (restricted scope; fine for a personal app in Testing mode)
- gmail.compose                  create drafts and send them (no delete, no label changes)

Storage: <state_dir>/google/token.json (atomic, owner-only), the OAuth client in <state_dir>/google/client.json.
Never inside the repo, never in logs (logs carry only codes and scope names). Google refresh tokens do not rotate,
so concurrent refreshes by the Core and the CLI are harmless; writes are atomic and serialized in-process.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import logs
from .chatgpt_auth import _read_json, _write_private, pkce_pair

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_G = "https://www.googleapis.com/auth/"
CALENDAR_SCOPES = (_G + "calendar.events.readonly", _G + "calendar.calendarlist.readonly")
GMAIL_READ = _G + "gmail.readonly"
GMAIL_COMPOSE = _G + "gmail.compose"
ALL_SCOPES = CALENDAR_SCOPES + (GMAIL_READ, GMAIL_COMPOSE)
# Calendar WRITE (SW-V2 D05, WP-M4): asked for ONLY when the calendar_write switch is on (`jarvis google login
# --calendar-write`). Google has no "create/update but not delete" scope (checked 2026-10-19 at
# developers.google.com/workspace/calendar/api/auth: calendar.events = "View and edit events on all your
# calendars"), so the no-delete rule is enforced in JARVES itself: skills/google.py `calendar_guard` (every request),
# guardrails.py (no delete-like tool can be registered) and no delete route in the dashboard.
CALENDAR_WRITE = _G + "calendar.events"
REFRESH_MARGIN_S = 120
START_COMMAND = "jarvis google start"           # WP-K: the one command for first connect AND the weekly reconnect
RECONNECT_HE = f"צריך לחבר מחדש את גוגל: תריץ במחשב {START_COMMAND}, או לחץ Reconnect בדף הניהול."
NOT_CONNECTED_HE = f"גוגל עוד לא מחובר. כדי לחבר, תריץ במחשב {START_COMMAND}, ואני אדריך אותך צעד אחר צעד."


class GoogleAuthError(Exception):
    def __init__(self, code: str, message: str, action: str = "", spoken: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action
        self.spoken = spoken or message           # short Hebrew sentence for the voice


def short_scope(s: str) -> str:
    return s.removeprefix(_G)


class GoogleAuth:
    def __init__(self, home: Path, *, transport: httpx.AsyncBaseTransport | None = None,
                 open_browser=webbrowser.open) -> None:
        self.dir = home / "google"
        self.file = self.dir / "token.json"
        self.client_file = self.dir / "client.json"
        self._transport = transport
        self._open_browser = open_browser
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ storage
    def load(self) -> dict:
        return _read_json(self.file)

    def _save(self, d: dict) -> None:
        _write_private(self.file, d)

    def client(self) -> dict:
        """The Desktop OAuth client ({client_id, client_secret}); {} when not configured yet."""
        d = _read_json(self.client_file)
        c = d.get("installed") or d
        if not isinstance(c, dict) or not c.get("client_id"):
            return {}
        return {"client_id": c["client_id"], "client_secret": c.get("client_secret", "")}

    def import_client(self, path: Path) -> None:
        """Copy the client_secret_*.json the user downloaded from Google Cloud (Desktop app type only)."""
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise GoogleAuthError("GOOGLE_CLIENT_INVALID", f"cannot read {Path(path).name}",
                                  "download the JSON again from Google Cloud > Credentials") from e
        if not isinstance(d, dict) or "installed" not in d or not d["installed"].get("client_id"):
            raise GoogleAuthError("GOOGLE_CLIENT_INVALID", "this is not a Desktop-app OAuth client file",
                                  "create the OAuth client with application type 'Desktop app'")
        _write_private(self.client_file, {"installed": {k: d["installed"][k] for k in
                                                        ("client_id", "client_secret") if k in d["installed"]}})
        logs.info("google", "GOOGLE_CLIENT_SAVED", "Google OAuth client saved")

    def find_client_download(self) -> Path | None:
        """A client_secret_*.json the user dropped into <state_dir>/google or <state_dir> (setup convenience)."""
        for d in (self.dir, self.dir.parent):
            hits = sorted(d.glob("client_secret*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if hits:
                return hits[0]
        return None

    def status(self) -> dict:
        """Safe summary for the dashboard, CLI and health (no tokens, no e-mail addresses)."""
        t = self.load()
        scopes = t.get("scopes", []) if t.get("refresh_token") else []
        connected = bool(t.get("refresh_token"))
        st = {"connected": connected, "needs_reconnect": bool(t.get("needs_reconnect")) and not connected,
              "client_configured": bool(self.client()), "scopes": [short_scope(s) for s in scopes],
              "calendar": all(s in scopes for s in CALENDAR_SCOPES), "gmail_read": GMAIL_READ in scopes,
              "gmail_send": GMAIL_COMPOSE in scopes, "calendar_write": CALENDAR_WRITE in scopes,
              "last_error": t.get("last_error", ""),
              "last_error_at": t.get("last_error_at", 0), "connected_at": t.get("connected_at", 0)}
        st["message_he"] = ("" if connected else RECONNECT_HE if st["needs_reconnect"] else NOT_CONNECTED_HE)
        return st

    def known(self) -> bool:
        """The user ever connected (also true while a reconnect is needed): the Skills are registered."""
        t = self.load()
        return bool(t.get("refresh_token") or t.get("needs_reconnect"))

    async def _record_error(self, code: str, drop_tokens: bool = False) -> None:
        async with self._lock:
            t = self.load()
            if not t:
                return
            if drop_tokens:
                t = {k: v for k, v in t.items() if k not in ("access_token", "refresh_token", "expires_at")}
                t["needs_reconnect"] = True
            t.update(last_error=code, last_error_at=time.time())
            self._save(t)

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=20, transport=self._transport)

    # ------------------------------------------------------------------ sign-in
    def authorize_url(self, client_id: str, redirect_uri: str, scopes, state: str, challenge: str) -> str:
        q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
             "access_type": "offline", "prompt": "consent"}
        return f"{AUTH_URL}?{urlencode(q)}"

    async def _serve_callback(self) -> tuple[asyncio.AbstractServer, dict, asyncio.Event]:
        got: dict = {}
        done = asyncio.Event()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
                while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                    pass
                parts = line.split(" ")
                url = urlparse(parts[1]) if len(parts) > 1 else None
                q = {k: v[0] for k, v in parse_qs(url.query).items()} if url else {}
                if url is None or url.path != "/" or done.is_set() or not ("code" in q or "error" in q):
                    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    return
                got.update(q)
                body = ("<html><body><h2>JARVES: " + ("Google connected. You can close this tab."
                        if "code" in q else "Google sign-in was not completed.") + "</h2></body></html>").encode()
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
                if got:
                    done.set()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)     # random free port (Google: loopback IP)
        return server, got, done

    async def sign_in(self, scopes=ALL_SCOPES, timeout_s: float = 300) -> dict:
        """Interactive: the user's own consent in the browser. Returns status()."""
        client = self.client()
        if not client:
            dl = self.find_client_download()
            if dl is None:
                raise GoogleAuthError("GOOGLE_NO_CLIENT", "no Google OAuth client configured",
                                      "run `jarvis google start` (it guides you; docs/GOOGLE_SETUP.md)")
            self.import_client(dl)
            client = self.client()
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        server, got, done = await self._serve_callback()
        try:
            redirect_uri = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            url = self.authorize_url(client["client_id"], redirect_uri, scopes, state, challenge)
            if not self._open_browser(url):
                print(f"Open this link in your browser to connect Google:\n{url}")
            try:
                await asyncio.wait_for(done.wait(), timeout_s)
            except asyncio.TimeoutError as e:
                raise GoogleAuthError("GOOGLE_SIGNIN_TIMEOUT", "Google sign-in was not completed in time",
                                      "run `jarvis google start` again") from e
        finally:
            server.close()
        return await self.complete(got, state, verifier, redirect_uri, scopes)

    async def complete(self, p: dict, state: str, verifier: str, redirect_uri: str, scopes=ALL_SCOPES) -> dict:
        if not secrets.compare_digest(p.get("state", ""), state):
            raise GoogleAuthError("GOOGLE_BAD_STATE", "sign-in callback did not match this attempt", "try again")
        if p.get("error"):
            code = "GOOGLE_CONSENT_DENIED" if p["error"] == "access_denied" else "GOOGLE_SIGNIN_ERROR"
            raise GoogleAuthError(code, f"sign-in ended: {p['error']}", "run `jarvis google start` to try again")
        client = self.client()
        if not p.get("code") or not client:
            raise GoogleAuthError("GOOGLE_SIGNIN_ERROR", "sign-in callback had no code", "try again")
        tok = await self._token({"grant_type": "authorization_code", "code": p["code"], "code_verifier": verifier,
                                 "redirect_uri": redirect_uri, **client})
        if not tok.get("refresh_token"):
            raise GoogleAuthError("GOOGLE_NO_REFRESH_TOKEN", "Google returned no refresh token",
                                  "remove JARVES at myaccount.google.com > Security > third-party access, then log in again")
        granted = tok.get("scope", "").split() or list(scopes)
        t = {"access_token": tok["access_token"], "refresh_token": tok["refresh_token"], "scopes": granted,
             "expires_at": time.time() + float(tok.get("expires_in", 3600)), "connected_at": time.time()}
        async with self._lock:
            self._save(t)
        st = self.status()
        missing = [short_scope(s) for s in scopes if s not in granted]
        logs.info("google", "GOOGLE_AUTH_OK", "Google connected", scopes=st["scopes"], missing=missing)
        return st

    async def _token(self, form: dict) -> dict:
        try:
            async with self._http() as c:
                r = await c.post(TOKEN_URL, data=form)
        except httpx.HTTPError as e:
            raise GoogleAuthError("GOOGLE_OFFLINE", "cannot reach Google", "check the internet",
                                  "אין לי חיבור לגוגל כרגע.") from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code != 200:
            err = body.get("error", f"http_{r.status_code}") if isinstance(body, dict) else f"http_{r.status_code}"
            raise GoogleAuthError(f"oauth:{err}", f"token request failed ({err})")
        return body

    # ------------------------------------------------------------------ tokens for the Skills
    async def access_token(self, need=(), *, force_refresh: bool = False) -> str:
        """A valid access token that carries every scope in `need`; GoogleAuthError with a spoken message otherwise."""
        t = self.load()
        if not t.get("refresh_token"):
            if t.get("needs_reconnect"):
                raise GoogleAuthError("GOOGLE_AUTH_EXPIRED", "the Google connection expired",
                                      "run `jarvis google start`", RECONNECT_HE)
            raise GoogleAuthError("GOOGLE_NOT_CONNECTED", "Google is not connected", "run `jarvis google start`",
                                  NOT_CONNECTED_HE)
        missing = [s for s in need if s not in t.get("scopes", [])]
        if missing:
            logs.warn("google", "GOOGLE_SCOPE_MISSING", "a Google permission was not granted",
                      missing=[short_scope(s) for s in missing])
            raise GoogleAuthError("GOOGLE_SCOPE_MISSING", "a Google permission was not granted",
                                  "run `jarvis google start` and allow all requested permissions",
                                  "לא קיבלתי מגוגל הרשאה לזה. צריך לחבר מחדש את גוגל ולאשר את כל ההרשאות.")
        if not force_refresh and t.get("expires_at", 0) - time.time() > REFRESH_MARGIN_S:
            return t["access_token"]
        return await self._refresh()

    async def _refresh(self) -> str:
        client = self.client()
        t = self.load()
        if not client:
            raise GoogleAuthError("GOOGLE_NO_CLIENT", "Google OAuth client file is missing",
                                  "run `jarvis google start --client <file>`", RECONNECT_HE)
        try:
            tok = await self._token({"grant_type": "refresh_token", "refresh_token": t.get("refresh_token", ""),
                                     **client})
        except GoogleAuthError as e:
            if e.code in ("oauth:invalid_grant", "oauth:unauthorized_client", "oauth:invalid_client"):
                # Testing-mode apps: the refresh token dies after 7 days; also revoked or password changed
                await self._record_error("GOOGLE_AUTH_EXPIRED", drop_tokens=True)
                logs.warn("google", "GOOGLE_AUTH_EXPIRED", "Google connection expired; the user must reconnect",
                          reason=e.code.removeprefix("oauth:"))
                raise GoogleAuthError("GOOGLE_AUTH_EXPIRED", "the Google connection expired",
                                      "run `jarvis google start`", RECONNECT_HE) from e
            await self._record_error(e.code)
            raise
        async with self._lock:
            cur = self.load()
            if not cur.get("refresh_token"):         # signed out meanwhile
                raise GoogleAuthError("GOOGLE_NOT_CONNECTED", "Google is not connected", "run `jarvis google start`",
                                      NOT_CONNECTED_HE)
            cur.update(access_token=tok["access_token"], expires_at=time.time() + float(tok.get("expires_in", 3600)))
            if tok.get("scope"):
                cur["scopes"] = tok["scope"].split()
            if tok.get("refresh_token"):
                cur["refresh_token"] = tok["refresh_token"]
            cur.pop("last_error", None)
            self._save(cur)
        logs.info("google", "GOOGLE_REFRESHED", "Google access renewed")
        return cur["access_token"]

    async def sign_out(self) -> bool:
        """Delete locally first, then revoke remotely. True when Google confirmed the revocation."""
        async with self._lock:
            t = self.load()
            self.file.unlink(missing_ok=True)
        confirmed = False
        token = t.get("refresh_token") or t.get("access_token")
        if token:
            try:
                async with self._http() as c:
                    r = await c.post(REVOKE_URL, data={"token": token})
                confirmed = r.status_code == 200
            except httpx.HTTPError:
                pass
        logs.info("google", "GOOGLE_SIGNED_OUT", "Google disconnected", revoked=confirmed)
        return confirmed


def google_status(home: Path) -> dict:
    """Dashboard-facing: {connected, needs_reconnect, scopes, calendar, gmail_read, gmail_send, last_error,
    message_he, ...}. Reads only the local token file; never contacts Google."""
    return GoogleAuth(home).status()


def dashboard_routes(register) -> None:
    """Dashboard plugin (jarves.dashboard PLUGIN_MODULES): GET /api/google = status (with the Hebrew reconnect
    line), POST /api/google/disconnect = revoke + delete. Connecting stays `jarvis google start` (browser consent)."""
    def get(ctx, _m, _body):
        return 200, {**google_status(ctx.home), "connect_command": "jarvis google start"}

    def disconnect(ctx, _m, _body):
        return 200, {"revoked": asyncio.run(GoogleAuth(ctx.home).sign_out()), **google_status(ctx.home)}
    register("GET", r"/api/google", get)
    register("POST", r"/api/google/disconnect", disconnect)
    calendar_routes(register)


def calendar_routes(register) -> None:
    """Dashboard Calendar page (spec 6.6): status, calendars, upcoming events; create/update as preview (the exact
    read-back) -> commit (the user's click is the approval). There is NO delete route (D05)."""
    import secrets as _secrets

    def writer(ctx):
        w = getattr(ctx, "_calendar_writer", None)
        if w is None:
            from .skills.google import CalendarWriter, GoogleApi
            t = getattr(ctx, "google_transport", None)
            w = CalendarWriter(GoogleApi(GoogleAuth(ctx.home, transport=t), t), **getattr(ctx, "calendar_kw", {}))
            ctx._calendar_writer, ctx._calendar_plans = w, {}
        return w

    def switch_on(ctx) -> bool:
        from .features import Features
        f = getattr(ctx, "_features", None) or Features(ctx.cfg, ctx.home)
        return f.enabled("calendar_write")

    def overview(ctx, _m, _b):
        st = google_status(ctx.home)
        out = {"google": st, "write_enabled": switch_on(ctx) and st["calendar_write"],
               "write_switch": switch_on(ctx), "deletion": "not possible: JARVES never deletes calendar events",
               "calendars": [], "events": []}
        if not st["calendar"]:
            return 200, out
        from .skills.google import FindArgs
        try:
            w = writer(ctx)
            out["calendars"] = asyncio.run(w.calendars())
            out["events"] = asyncio.run(w.find(FindArgs(day="week")))
        except GoogleAuthError as e:
            out["error"] = e.spoken
        return 200, out

    def preview(ctx, _m, body):
        from pydantic import ValidationError

        from .skills.google import CreateArgs, UpdateArgs
        if not switch_on(ctx):
            return 403, {"error": "calendar writing is off (Features: calendar_write)"}
        b = dict(body or {})
        op = b.pop("op", "create")
        if op not in ("create", "update"):
            return 400, {"error": "op must be create or update"}       # there is no delete
        w = writer(ctx)
        try:
            args = CreateArgs.model_validate(b) if op == "create" else UpdateArgs.model_validate(b)
            plan = asyncio.run(w.plan_create(args) if op == "create" else w.plan_update(args))
        except ValidationError as e:
            return 400, {"error": "invalid: " + ", ".join(".".join(map(str, x["loc"])) for x in e.errors())}
        except GoogleAuthError as e:
            return 400, {"error": e.spoken, "code": e.code}
        except ValueError:
            return 400, {"error": "day or time not understood"}
        token = _secrets.token_urlsafe(12)
        ctx._calendar_plans = {k: v for k, v in ctx._calendar_plans.items() if time.time() - v["t"] < 300}
        ctx._calendar_plans[token] = {**plan, "t": time.time()}
        return 200, {"readback": plan["readback"], "preview_id": token, "expires_s": 300}

    def commit(ctx, _m, body):
        writer(ctx)
        plan = ctx._calendar_plans.pop(str((body or {}).get("preview_id", "")), None)
        if plan is None or time.time() - plan["t"] > 300:
            return 409, {"error": "preview expired or unknown: preview again"}
        if not switch_on(ctx):
            return 403, {"error": "calendar writing is off (Features: calendar_write)"}
        try:
            return 200, asyncio.run(ctx._calendar_writer.commit(plan))
        except GoogleAuthError as e:
            return 400, {"error": e.spoken, "code": e.code}
    register("GET", r"/api/calendar", overview)
    register("POST", r"/api/calendar/preview", preview)
    register("POST", r"/api/calendar/commit", commit)


# ---------------------------------------------------------------------- CLI (`jarvis google ...`)
def add_cli(sub) -> None:
    g = sub.add_parser("google", help="connect Google Calendar + Gmail: `jarvis google start` guides you step by step "
                                      "(also the weekly reconnect)")
    g.add_argument("action", choices=["start", "login", "status", "test", "logout"],
                   help="start = guided setup / reconnect (recommended); login = sign-in only; test = live check")
    g.add_argument("--client", help="the client_secret_*.json downloaded from Google Cloud (Desktop app)")
    g.add_argument("--calendar-only", action="store_true", help="ask only for the Calendar permissions")
    g.add_argument("--send", action="store_true", help="start: also switch on sending mail (off by default)")
    g.add_argument("--downloads", help="start: the folder your browser downloads to (default: ~/Downloads)")
    g.add_argument("--restart", action="store_true", help="start: forget the saved progress and begin again")
    g.add_argument("--calendar-write", action="store_true",
                   help="login: also allow adding/changing events (needs `jarvis features on calendar_write`)")


def cmd_google(a, cfg, home) -> int:
    if a.action in ("start", "test"):
        from . import google_setup                     # WP-K: the guided wizard and the live self-check
        if a.action == "start":
            return google_setup.cmd_start(a, cfg, home)
        logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
        if not GoogleAuth(home).status()["connected"]:
            print(NOT_CONNECTED_HE)
            return 1
        chk = google_setup.run_check(home, cfg)
        print(json.dumps({k: chk[k] for k in ("calendar", "gmail", "error")}, ensure_ascii=False))
        if not chk["ok"]:
            print(chk["fix"]["he"] + "\n" + chk["fix"]["en"])
        return 0 if chk["ok"] else 1
    logs.setup(home / "logs", cfg.log.level, console=False, privacy=cfg.log.privacy)
    auth = GoogleAuth(home)

    async def go() -> int:
        try:
            if a.action == "login":
                if a.client:
                    auth.import_client(Path(a.client))
                print("Opening your browser to connect JARVES to Google (Calendar read, Gmail read + drafts).")
                print("Google will warn that the app is unverified: it is your own app, choose Continue.")
                need = CALENDAR_SCOPES if a.calendar_only else ALL_SCOPES
                if getattr(a, "calendar_write", False):
                    from .features import Features
                    if not Features(cfg, home).enabled("calendar_write"):
                        print("Calendar writing is switched off. Turn it on first: jarvis features on calendar_write")
                        return 1
                    need = need + (CALENDAR_WRITE,)
                    print("Also asking to ADD and CHANGE events. JARVES never deletes events (enforced in JARVES; "
                          "Google's permission itself would allow it).")
                st = await auth.sign_in(need)
                print("connected: " + ", ".join(st["scopes"]))
                if any(short_scope(s) not in st["scopes"] for s in need):
                    print("Some permissions were NOT granted; run login again and tick every box.")
                    return 1
                print("Restart JARVES (jarvis stop, then jarvis) so it picks up the new connection.")
            elif a.action == "logout":
                ok = await auth.sign_out()
                print("disconnected" + ("" if ok else " locally (revocation not confirmed: also remove JARVES at"
                                        " myaccount.google.com > Security > Your connections to third-party apps)"))
            else:
                st = auth.status()
                print(json.dumps(st, ensure_ascii=False, indent=1))
                return 0 if st["connected"] else 1
            return 0
        except GoogleAuthError as e:
            print(f"{e.code}: {e.message}. {e.action}")
            return 1
    return asyncio.run(go())
