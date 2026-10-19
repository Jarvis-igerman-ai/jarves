"""Local web dashboard (`jarvis dashboard`): conversations, costs, memory, skills, issues, workspace files, settings.

Python stdlib only (http.server). Security: binds 127.0.0.1 only; a random per-run token in the printed URL is
exchanged for an HttpOnly SameSite=Strict cookie; every request needs it; the Host header must be loopback (DNS
rebinding); writes need a custom header (no cross-site form posts). No secrets are shown: config holds none by
design, and everything served passes logs.scrub plus e-mail masking. Reads store.db / memory.db while the Core runs
(SQLite WAL), so the current conversation shows live.

Plugin point (WP-D memory / workspace editing): a module may define `dashboard_routes(register)` and call
`register("POST", r"/api/memory/(\\d+)", handler)`; handler(ctx, match, body: dict | None) -> (status, obj).
Modules probed at start: jarves.workspace, jarves.curator, jarves.memory.
"""
from __future__ import annotations

import importlib
import json
import re
import secrets
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, diagnose, logs
from .health import read_snapshot
from .store import Store, daily_costs, session_cost

STATIC = Path(__file__).with_name("dashboard_static")
COOKIE = "jarves_dash"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ROUTES: list[tuple[str, re.Pattern, object]] = []
PLUGIN_MODULES = ("jarves.workspace", "jarves.curator", "jarves.memory")
WORKSPACE_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "MEMORY.md", "TOOLS.md", "IDENTITY.md")


def register(method: str, pattern: str, handler) -> None:
    """Add (or replace) an API route. Later registrations win, so a plugin can override a built-in read route."""
    rx = re.compile(f"^{pattern}$")
    _ROUTES[:] = [r for r in _ROUTES if not (r[0] == method and r[1].pattern == rx.pattern)]
    _ROUTES.insert(0, (method, rx, handler))


def clean(obj, depth: int = 0):
    """No credentials (credential-named keys masked, secrets redacted in strings) and no e-mail addresses in
    anything served. Token COUNTS (input_tokens...) are numbers, not credentials, and stay."""
    if depth > 12:
        return "<nested>"
    if isinstance(obj, dict):
        return {str(k): ("***" if logs.is_credential(k) and not str(k).lower().endswith("tokens") and v not in
                         (None, "", 0) else clean(v, depth + 1)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return _EMAIL.sub("<email>", logs.redact(obj))
    return obj


class Ctx:
    """What route handlers get: the stores, config, home dir and the workspace adapter."""

    def __init__(self, home: Path, cfg) -> None:
        self.home, self.cfg = home, cfg
        self.store = Store(home / "store.db", tier=cfg.ai.voice_service_tier, backend_model=cfg.ai.text_model)
        self._memory = None
        self.workspace = Workspace(home)

    @property
    def memory(self):
        if self._memory is None:
            from .memory import MemoryStore
            self._memory = MemoryStore(self.home / "memory.db")
        return self._memory

    def close(self) -> None:
        self.store.close()
        if self._memory is not None:
            self._memory.close()


class Workspace:
    """Tiny interface to WP-D's jarves.workspace (list / read / write). Until it exists: read-only view of
    <home>/workspace/*.md and the known names."""

    def __init__(self, home: Path) -> None:
        self.home = home
        try:
            self.mod = importlib.import_module("jarves.workspace")
        except ImportError:
            self.mod = None

    def _fn(self, *names):
        for n in names:
            f = getattr(self.mod, n, None) if self.mod else None
            if callable(f):
                return f
        return None

    @property
    def writable(self) -> bool:
        return self._fn("write_file", "write") is not None

    def list(self) -> list[dict]:
        f = self._fn("list_files", "list")
        if f:
            out = []
            for x in f():
                out.append(x if isinstance(x, dict) else {"name": str(getattr(x, "name", x))})
            return out
        d = self.home / "workspace"
        names = sorted({*WORKSPACE_FILES, *(p.name for p in d.glob("*.md"))}) if d.is_dir() else list(WORKSPACE_FILES)
        return [{"name": n, "exists": (d / n).is_file(), "size": (d / n).stat().st_size if (d / n).is_file() else 0}
                for n in names]

    def read(self, name: str) -> str | None:
        f = self._fn("read_file", "read")
        if f:
            return f(name)
        if "/" in name or "\\" in name or not name.endswith(".md"):
            return None
        p = self.home / "workspace" / name
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def write(self, name: str, text: str):
        f = self._fn("write_file", "write")
        if f is None:
            raise PermissionError("workspace editing arrives with jarves.workspace (WP-D)")
        return f(name, text)


# ---------------------------------------------------------------------- built-in API
def _turn_counts(st: Store, sid: str) -> dict:
    r = st.rows("SELECT SUM(role='user') u, SUM(role='assistant') a FROM turns WHERE session_id = ?", (sid,))[0]
    t = st.rows("SELECT COUNT(*) n, SUM(ok = 0) f FROM tool_calls WHERE session_id = ?", (sid,))[0]
    i = st.rows("SELECT COUNT(*) n FROM issue_hits WHERE session_id = ?", (sid,))[0]
    return {"user_turns": r["u"] or 0, "assistant_turns": r["a"] or 0, "tools": t["n"] or 0,
            "tool_failures": t["f"] or 0, "issues": i["n"] or 0}


def _session_summary(ctx: Ctx, s: dict) -> dict:
    st = ctx.store
    first = st.rows("SELECT text, chars FROM turns WHERE session_id = ? AND role = 'user' ORDER BY ts LIMIT 1",
                    (s["id"],))
    live = s.get("ended") is None and (s.get("last_event") or 0) > time.time() - 600
    return {**s, **_turn_counts(st, s["id"]), "cost": session_cost(st, s, ctx.cfg.pricing), "live": live,
            "duration_s": round((s.get("ended") or s.get("last_event") or s["started"]) - s["started"], 1),
            "first_user": (first[0]["text"] if first[0]["text"] is not None else f"<{first[0]['chars']} chars>")
            if first else None}


def api_overview(ctx: Ctx, m, body):
    st = ctx.store
    today = datetime.now().date().isoformat()
    days = daily_costs(st, ctx.cfg.pricing, 7)
    live = st.live_session()
    snap = read_snapshot(ctx.home / "health.json") or {}
    issues = st.issues("open")
    return 200, {"version": __version__, "today": next((d for d in days if d["day"] == today), None), "week": days,
                 "live": _session_summary(ctx, live) if live else None,
                 "issues": {"open": len(issues), "high": sum(i["severity"] == "high" for i in issues)},
                 "core": {"state": snap.get("core"), "age_s": snap.get("age_s")}, "privacy": ctx.cfg.log.privacy,
                 "sessions": st.rows("SELECT COUNT(*) n FROM sessions")[0]["n"]}


def api_sessions(ctx: Ctx, m, body, q=None):
    limit = min(int((q or {}).get("limit", ["100"])[0]), 500)
    return 200, [_session_summary(ctx, s) for s in ctx.store.sessions(limit)]


def api_session(ctx: Ctx, m, body):
    sid = unquote(m.group(1))
    s = ctx.store.session(sid)
    if s is None:
        return 404, {"error": "no such conversation"}
    tl = ctx.store.timeline(sid)
    hits = {r["fingerprint"] for r in ctx.store.rows("SELECT fingerprint FROM issue_hits WHERE session_id = ?", (sid,))}
    issues = [i for i in ctx.store.issues(None) if i["fingerprint"] in hits]
    return 200, {"session": _session_summary(ctx, s), **tl, "issues": issues,
                 "thresholds": {"dead_air_s": ctx.cfg.history.dead_air_s, "stall_s": ctx.cfg.history.stall_s}}


def api_live(ctx: Ctx, m, body):
    s = ctx.store.live_session()
    if s is None:
        return 200, {"session": None}
    return api_session(ctx, re.match(r"(.*)", s["id"]), body)


def api_costs(ctx: Ctx, m, body, q=None):
    days = min(int((q or {}).get("days", ["30"])[0]), 365)
    p = ctx.cfg.pricing
    sess = [{"id": s["id"], "started": s["started"], "model": s["model"], "backend_model": s["backend_model"],
             "tier": s["tier"], **session_cost(ctx.store, s, p)}
            for s in ctx.store.rows("SELECT * FROM sessions WHERE started > ? ORDER BY started DESC",
                                    (time.time() - days * 86400,))]
    return 200, {"days": daily_costs(ctx.store, p, days), "sessions": sess,
                 "pricing": p.model_dump(), "estimate": True,
                 "notes": ["Estimates from [pricing] (checked 2026-10-19, developers.openai.com/api/docs/pricing). "
                           "Your OpenAI bill is the truth.",
                           f"Backend responses without logged token usage use {p.est_input_tokens} input / "
                           f"{p.est_output_tokens} output tokens each.",
                           "Web searches are counted only when the backend log reports them.",
                           "Typed answers on the ChatGPT plan cost $0 API."]}


def api_memory(ctx: Ctx, m, body):
    try:
        items = ctx.memory.list(500)
    except Exception as e:
        return 200, {"items": [], "error": f"memory not readable: {type(e).__name__}"}
    return 200, {"items": items, "editable": any(r[0] != "GET" and "/api/memory" in r[1].pattern for r in _ROUTES)}


def api_skills(ctx: Ctx, m, body):
    stats = ctx.store.tool_stats(30)
    skills = ctx.store.skills()
    snap = read_snapshot(ctx.home / "health.json") or {}
    comp = snap.get("components") or {}
    out = []
    for name, info in sorted(skills.items()):
        tools = [{"name": t, **{k: v for k, v in (stats.get(t) or {}).items() if k != "name"}}
                 for t in info.get("tools", [])]
        h = comp.get(name) or {}
        out.append({"skill": name, "tools": tools, "health": h.get("status"), "detail": h.get("detail", ""),
                    "action": h.get("action", "")})
    known = {t for s in skills.values() for t in s.get("tools", [])}
    other = [{"name": n, **{k: v for k, v in s.items() if k != "name"}} for n, s in stats.items() if n not in known]
    return 200, {"skills": out, "other_tools": other, "components": comp, "core": snap.get("core"),
                 "snapshot_age_s": snap.get("age_s")}


def api_issues(ctx: Ctx, m, body, q=None):
    status = (q or {}).get("status", ["open"])[0]
    return 200, ctx.store.issues(None if status == "all" else status)


def api_issue_set(ctx: Ctx, m, body):
    try:
        ok = ctx.store.set_issue_status(int(m.group(1)), str((body or {}).get("status", "")))
    except ValueError as e:
        return 400, {"error": str(e)}
    return (200, {"ok": True}) if ok else (404, {"error": "no such issue"})


def api_diagnose(ctx: Ctx, m, body):
    found = diagnose.run(ctx.store, ctx.cfg)
    return 200, {"found": len(found), "open": len(ctx.store.issues("open"))}


def api_workspace(ctx: Ctx, m, body):
    try:
        return 200, {"files": ctx.workspace.list(), "writable": ctx.workspace.writable,
                     "provider": "jarves.workspace" if ctx.workspace.mod else "read-only preview"}
    except Exception as e:
        return 500, {"error": f"workspace: {type(e).__name__}"}


def api_workspace_file(ctx: Ctx, m, body):
    name = unquote(m.group(1))
    text = ctx.workspace.read(name)
    if text is None:
        return 404, {"error": "no such file"}
    return 200, {"name": name, "text": text, "writable": ctx.workspace.writable}


def api_workspace_write(ctx: Ctx, m, body):
    try:
        ctx.workspace.write(unquote(m.group(1)), str((body or {}).get("text", "")))
    except PermissionError as e:
        return 403, {"error": str(e)}
    except (ValueError, OSError) as e:
        return 400, {"error": str(e)}
    return 200, {"ok": True}


def api_settings(ctx: Ctx, m, body):
    return 200, {"config": ctx.cfg.model_dump(), "home": str(ctx.home), "read_only": True,
                 "hint": "Edit config.toml (see core/config.example.toml); restart the Core to apply."}


for _m, _p, _h in [("GET", r"/api/overview", api_overview), ("GET", r"/api/sessions", api_sessions),
                   ("GET", r"/api/session/([^/]+)", api_session), ("GET", r"/api/live", api_live),
                   ("GET", r"/api/costs", api_costs), ("GET", r"/api/memory", api_memory),
                   ("GET", r"/api/skills", api_skills), ("GET", r"/api/issues", api_issues),
                   ("POST", r"/api/issues/(\d+)", api_issue_set), ("POST", r"/api/diagnose", api_diagnose),
                   ("GET", r"/api/workspace", api_workspace), ("GET", r"/api/workspace/([^/]+)", api_workspace_file),
                   ("PUT", r"/api/workspace/([^/]+)", api_workspace_write), ("GET", r"/api/settings", api_settings)]:
    _ROUTES.append((_m, re.compile(f"^{_p}$"), _h))


def load_plugins() -> list[str]:
    loaded = []
    for name in PLUGIN_MODULES:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        hook = getattr(mod, "dashboard_routes", None)
        if callable(hook):
            hook(register)
            loaded.append(name)
    return loaded


# ---------------------------------------------------------------------- HTTP server
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvesDashboard"
    sys_version = ""
    ctx: Ctx
    token: str

    def log_message(self, fmt, *args) -> None:       # quiet: no request log on the terminal
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(clean(obj), ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _authed(self) -> bool:
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and secrets.compare_digest(v, self.token):
                return True
        return False

    def _handle(self) -> None:
        if not self._host_ok():
            return self._send(403, b"forbidden host", "text/plain")
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        if self.command == "GET" and url.path == "/" and "t" in q:
            if secrets.compare_digest(q["t"][0], self.token):
                return self._send(302, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"{COOKIE}={self.token}; HttpOnly; SameSite=Strict; Path=/"})
            return self._send(403, b"wrong token: open the link printed by `jarvis dashboard`", "text/plain")
        if not self._authed():
            return self._send(403, "Open the link printed by `jarvis dashboard` (it holds a one-time token)."
                              .encode(), "text/plain; charset=utf-8")
        if url.path == "/" or url.path.startswith("/static/"):
            if self.command not in ("GET", "HEAD"):
                return self._send(405, b"", "text/plain")
            name = "index.html" if url.path == "/" else url.path.removeprefix("/static/")
            p = (STATIC / name).resolve()
            if STATIC.resolve() not in p.parents or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            return self._send(200, p.read_bytes(), _TYPES.get(p.suffix, "application/octet-stream"))
        if self.command != "GET" and self.headers.get("X-Jarves") != "1":
            return self._json(403, {"error": "missing X-Jarves header"})
        body = None
        if self.command in ("POST", "PUT"):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return self._json(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "invalid JSON"})
        for method, rx, fn in _ROUTES:
            mt = rx.match(url.path)
            if mt and method == self.command:
                try:
                    takes_q = getattr(getattr(fn, "__code__", None), "co_argcount", 3) >= 4
                    status, obj = fn(self.ctx, mt, body, q) if takes_q else fn(self.ctx, mt, body)
                except Exception as e:
                    status, obj = 500, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
                return self._json(status, obj)
        return self._json(404, {"error": "not found"})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle


class Dashboard:
    """Owns the HTTP server; `url` holds the one-time token. start() runs it in a daemon thread."""

    def __init__(self, home: Path, cfg, port: int = 0) -> None:
        self.ctx = Ctx(home, cfg)
        self.plugins = load_plugins()
        token = secrets.token_urlsafe(24)
        handler = type("H", (Handler,), {"ctx": self.ctx, "token": token})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?t={token}"
        self._thread: threading.Thread | None = None

    def start(self) -> "Dashboard":
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="jarves-dashboard", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.ctx.close()
