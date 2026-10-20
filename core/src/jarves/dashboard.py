"""Local web dashboard (`jarvis dashboard`, or the `open_dashboard` tool / face button inside the Core):
conversations, logs, costs, background tasks, memory, workspace files, settings, skills and issues.

Python stdlib only (http.server). Security: binds 127.0.0.1 by default; a random per-run token in the printed URL
is exchanged for an HttpOnly SameSite=Strict cookie; every request needs it; the Host header must be loopback (DNS
rebinding); writes need a custom header (no cross-site form posts) and a same-origin Origin. Opt-in home-LAN mode
with device pairing, CSRF tokens, rate limiting and an audit log: dashboard_auth.py (WP-M4). No secrets are shown: config holds none by
design, and everything served passes logs.scrub plus e-mail masking. Reads store.db / memory.db while the Core runs
(SQLite WAL), so the current conversation shows live.

Editing (SW-V2 WP-H): workspace files through jarves.workspace (diff preview, history, restore, undo), memory facts
(edit, delete, forget one conversation's auto-learned facts, undo), the runtime overrides of skills.settings
(apply at once) and an allowlist of config.toml values (config_edit.py: validated, backed up, next start).
Opened from inside the Core, the dashboard shares the Core's Workspace / MemoryStore, so a persona edit also
reaches a running conversation (workspace listeners).

Plugin point: a module may define `dashboard_routes(register)` and call
`register("POST", r"/api/thing/(\\d+)", handler)`; handler(ctx, match, body: dict | None) -> (status, obj).
Modules probed at start: jarves.workspace, jarves.curator, jarves.memory, jarves.features.
"""
from __future__ import annotations

import importlib
import json
import re
import secrets
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, config_edit, diagnose, hebrew, logs, logview
from .dashboard_auth import (DEVICE_COOKIE, device_body_ok, DEVICE_IDLE_S, LAN_PORT, PUBLIC_PATHS, Access, client_allowed,
                             is_loopback, lan_ip, parse_cookies)
from .health import read_snapshot
from .skills import Skill, Tool, ToolResult
from .store import TYPED_KINDS, Store, daily_costs, session_cost

STATIC = Path(__file__).with_name("dashboard_static")
COOKIE = "jarves_dash"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ROUTES: list[tuple[str, re.Pattern, object]] = []
PLUGIN_MODULES = ("jarves.workspace", "jarves.curator", "jarves.memory", "jarves.features", "jarves.google_auth",
                  "jarves.google_setup", "jarves.privacy", "jarves.overview",
                  "jarves.usage", "jarves.backup", "jarves.appearance", "jarves.skills.generated",
                  "jarves.operations", "jarves.runtime", "jarves.updates",
                  "jarves.identity", "jarves.dashboard_auth", "jarves.guides",     # WP-L: Guides pages
                  "jarves.setup_wizard",                                         # SW-V3: Setup & add-ons page
                  "jarves.context_trace")                                          # his context per conversation
MEMORY_UNDO_KEEP = 30
MEMORY_UNDO_TTL_S = 24 * 3600                        # the undo button works for 24 h, then the entry is purged
UNDO_PURGE_EVERY_S = 3600


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
    """What route handlers get: the stores, config, home dir, workspace, settings and the log reader."""

    def __init__(self, home: Path, cfg, *, workspace=None, memory=None, settings=None, config_path=None) -> None:
        self.home, self.cfg = home, cfg
        self.config_path = Path(config_path) if config_path else home / "config.toml"
        self.store = Store(home / "store.db", tier=cfg.ai.voice_service_tier, backend_model=cfg.ai.text_model)
        self._memory, self._own_memory = memory, memory is None
        self._workspace, self._settings = workspace, settings
        self.logs = logview.LogReader(home)
        self.lock = threading.Lock()                  # memory undo journal
        self.clock = time.time                        # tests inject a fake clock
        self.access = None                 # dashboard_auth.Access (set by Dashboard)
        self.port = 0
        self._tl = threading.local()       # per-request principal (the server is threaded)

    @property
    def principal(self) -> dict | None:
        return getattr(self._tl, "who", None)

    @property
    def memory(self):
        if self._memory is None:
            from .memory import MemoryStore
            self._memory = MemoryStore(self.home / "memory.db")
        return self._memory

    @property
    def workspace(self):
        if self._workspace is None:
            from .workspace import Workspace
            self._workspace = Workspace(self.home / "workspace")
        return self._workspace

    @property
    def settings(self):
        if self._settings is None:
            from .skills.settings import Settings
            self._settings = Settings(self.memory, {"voice.voice": self.cfg.voice.voice})
            try:                                      # curator.* are feature switches (WP-I features.py)
                from .features import _ctx_features
                self._settings.features = _ctx_features(self)
            except ImportError:
                pass
        return self._settings

    def close(self) -> None:
        self.store.close()
        if self._own_memory and self._memory is not None:
            self._memory.close()
        if getattr(self, "_userdata", None) is not None:     # WP-M3 plugin connection (userdata.ctx_db)
            self._userdata.close()


# ---------------------------------------------------------------------- conversations
def _turn_counts(st: Store, sid: str) -> dict:
    r = st.rows("SELECT SUM(role='user') u, SUM(role='assistant') a FROM turns WHERE session_id = ?", (sid,))[0]
    t = st.rows("SELECT COUNT(*) n, SUM(ok = 0) f FROM tool_calls WHERE session_id = ?", (sid,))[0]
    i = st.rows("SELECT COUNT(*) n FROM issue_hits WHERE session_id = ?", (sid,))[0]
    e = st.rows("SELECT COUNT(*) n FROM events WHERE session_id = ? AND level = 'ERROR'", (sid,))[0]
    return {"user_turns": r["u"] or 0, "assistant_turns": r["a"] or 0, "tools": t["n"] or 0,
            "tool_failures": t["f"] or 0, "issues": i["n"] or 0, "errors": e["n"] or 0}


def _session_summary(ctx: Ctx, s: dict) -> dict:
    st = ctx.store
    first = st.rows("SELECT text, chars FROM turns WHERE session_id = ? AND role = 'user' ORDER BY ts LIMIT 1",
                    (s["id"],))
    live = s.get("ended") is None and (s.get("last_event") or 0) > time.time() - 600
    return {**s, **_turn_counts(st, s["id"]), "cost": session_cost(st, s, ctx.cfg.pricing), "live": live,
            "typed": s.get("kind") in TYPED_KINDS,
            "duration_s": round((s.get("ended") or s.get("last_event") or s["started"]) - s["started"], 1),
            "first_user": (first[0]["text"] if first[0]["text"] is not None else f"<{first[0]['chars']} chars>")
            if first else None}


def _q1(q: dict | None, k: str, default: str = "") -> str:
    return str(((q or {}).get(k) or [default])[0]).strip()


def _day_ts(s: str, end: bool = False) -> float | None:
    try:
        return datetime.fromisoformat(s).timestamp() + (86400 if end and len(s) <= 10 else 0)
    except ValueError:
        return None


SORTS = {"started": lambda s: s["started"], "cost": lambda s: s["cost"]["total"],
         "duration": lambda s: s["duration_s"], "turns": lambda s: s["user_turns"] + s["assistant_turns"],
         "issues": lambda s: s["issues"], "tools": lambda s: s["tools"]}


def api_sessions(ctx: Ctx, m, body, q=None):
    """Conversations with filters (q text, from/to day, tool, issues=1, errors=1, kind=voice|typed, min_cost,
    min_dur/max_dur seconds), sort (started|cost|duration|turns|issues|tools, dir) and pages (page, per).
    Without paging parameters the old plain list (newest first, `limit`) is returned."""
    st = ctx.store
    limit = min(int(_q1(q, "limit", "100") or 100), 5000)
    paged = any(k in (q or {}) for k in ("page", "per", "sort", "q", "tool", "kind", "issues", "errors", "from", "to",
                                          "min_cost", "min_dur", "max_dur"))
    where, args = [], []
    if _q1(q, "from") and _day_ts(_q1(q, "from")) is not None:
        where.append("started >= ?"); args.append(_day_ts(_q1(q, "from")))
    if _q1(q, "to") and _day_ts(_q1(q, "to"), True) is not None:
        where.append("started < ?"); args.append(_day_ts(_q1(q, "to"), True))
    kind = _q1(q, "kind")
    if kind == "typed":
        where.append(f"kind IN ({','.join('?' * len(TYPED_KINDS))})"); args += list(TYPED_KINDS)
    elif kind == "voice":
        where.append(f"kind NOT IN ({','.join('?' * len(TYPED_KINDS))})"); args += list(TYPED_KINDS)
    text = _q1(q, "q")
    if text:
        where.append("id IN (SELECT session_id FROM turns WHERE text LIKE ?)"); args.append(f"%{text}%")
    if _q1(q, "tool"):
        where.append("id IN (SELECT session_id FROM tool_calls WHERE name = ?)"); args.append(_q1(q, "tool"))
    if _q1(q, "issues") in ("1", "true"):
        where.append("id IN (SELECT session_id FROM issue_hits)")
    if _q1(q, "errors") in ("1", "true"):
        where.append("(id IN (SELECT session_id FROM events WHERE level = 'ERROR') OR "
                     "id IN (SELECT session_id FROM tool_calls WHERE ok = 0))")
    sql = "SELECT * FROM sessions" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY started DESC"
    rows = st.rows(sql + " LIMIT ?", (*args, 5000 if paged else limit))
    out = [_session_summary(ctx, s) for s in rows]
    if not paged:
        return 200, out
    try:
        mc, mn, mx = (float(_q1(q, k) or d) for k, d in (("min_cost", 0), ("min_dur", 0), ("max_dur", 1e12)))
    except ValueError:
        return 400, {"error": "min_cost / min_dur / max_dur must be numbers"}
    out = [s for s in out if s["cost"]["total"] >= mc and mn <= s["duration_s"] <= mx]
    key = SORTS.get(_q1(q, "sort", "started"), SORTS["started"])
    out.sort(key=key, reverse=_q1(q, "dir", "desc") != "asc")
    per = max(1, min(int(_q1(q, "per", "50") or 50), 500))
    page = max(1, int(_q1(q, "page", "1") or 1))
    tools = [r["name"] for r in st.rows("SELECT DISTINCT name FROM tool_calls ORDER BY name")]
    return 200, {"items": out[(page - 1) * per: page * per], "total": len(out), "page": page, "per": per,
                 "pages": max(1, -(-len(out) // per)), "tools": tools,
                 "totals": {"cost": round(sum(s["cost"]["total"] for s in out), 4),
                            "duration_s": round(sum(s["duration_s"] for s in out), 1)}}


def api_session(ctx: Ctx, m, body):
    sid = unquote(m.group(1))
    s = ctx.store.session(sid)
    if s is None:
        return 404, {"error": "no such conversation"}
    tl = ctx.store.timeline(sid)
    hits = {r["fingerprint"] for r in ctx.store.rows("SELECT fingerprint FROM issue_hits WHERE session_id = ?", (sid,))}
    issues = [i for i in ctx.store.issues(None) if i["fingerprint"] in hits]
    tasks = [t for t in _tasks(ctx, 200) if s["started"] - 1 <= _ts(t.get("created")) <=
             (s.get("ended") or s.get("last_event") or time.time()) + 1]
    return 200, {"session": _session_summary(ctx, s), **tl, "issues": issues, "tasks": tasks,
                 "review": {"skipped": diagnose.review_skip_reason(ctx.store, ctx.cfg, sid),
                            "done": bool(ctx.store.meta(f"llm_reviewed:{sid}"))},
                 "thresholds": {"dead_air_s": ctx.cfg.history.dead_air_s, "stall_s": ctx.cfg.history.stall_s}}


def api_session_raw(ctx: Ctx, m, body):
    """Everything stored for one conversation, as rows (the raw-JSON view)."""
    sid = unquote(m.group(1))
    s = ctx.store.session(sid)
    if s is None:
        return 404, {"error": "no such conversation"}
    return 200, {"session": s, **ctx.store.timeline(sid),
                 "issue_hits": ctx.store.rows("SELECT * FROM issue_hits WHERE session_id = ?", (sid,))}


def api_live(ctx: Ctx, m, body):
    s = ctx.store.live_session()
    if s is None:
        return 200, {"session": None}
    return api_session(ctx, re.match(r"(.*)", s["id"]), body)


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
                 "sessions": st.rows("SELECT COUNT(*) n FROM sessions")[0]["n"],
                 "features_page": _has_module("jarves.features")}


def _has_module(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------------- costs, tasks, issues, skills
def api_costs(ctx: Ctx, m, body, q=None):
    days = min(int(_q1(q, "days", "30") or 30), 365)
    p = ctx.cfg.pricing
    sess = [{"id": s["id"], "started": s["started"], "kind": s["kind"], "model": s["model"],
             "backend_model": s["backend_model"], "tier": s["tier"], **session_cost(ctx.store, s, p)}
            for s in ctx.store.rows("SELECT * FROM sessions WHERE started > ? ORDER BY started DESC",
                                    (time.time() - days * 86400,))]
    by_kind = ctx.store.rows("SELECT kind, COUNT(*) n, SUM(COALESCE(web_searches, 0)) searches, "
                             "SUM(input_tokens IS NULL) estimated FROM delegations WHERE ts > ? AND billed = 1 "
                             "GROUP BY kind", (time.time() - days * 86400,))
    return 200, {"days": daily_costs(ctx.store, p, days), "sessions": sess, "backend_by_kind": by_kind,
                 "pricing": p.model_dump(), "estimate": True,
                 "notes": ["Voice = GPT-Live minutes x price. Backend = the exact model, service tier and token "
                           "usage each response reports, plus web-search calls at "
                           f"${p.web_search_per_call * 1000:g} per 1,000. Prices from [pricing] (checked "
                           "2026-10-19, developers.openai.com/api/docs/pricing). Your OpenAI bill is the truth.",
                           f"Only responses logged before this version have no usage: they use {p.est_input_tokens} "
                           f"input / {p.est_output_tokens} output tokens each (marked *).",
                           "Typed answers on the ChatGPT plan cost $0 API. AI reviews of conversations are "
                           "included in their conversation."]}


def _ts(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _tasks(ctx: Ctx, limit: int = 100) -> list[dict]:
    from .tasks import read_history
    out = read_history(ctx.home / "tasks.jsonl", limit)
    if ctx.cfg.log.privacy:                           # what was asked / found is content: lengths only
        out = [{**t, **{k: logs._mask(t[k]) for k in ("description", "result", "queries", "steps") if t.get(k)}}
               for t in out]
    return out


def api_tasks(ctx: Ctx, m, body, q=None):
    items = _tasks(ctx, min(int(_q1(q, "limit", "100") or 100), 500))
    return 200, {"items": items, "file": "tasks.jsonl", "counts": {s: sum(t.get("status") == s for t in items)
                                                                    for s in ("done", "failed", "timeout", "cancelled")}}


def api_issues(ctx: Ctx, m, body, q=None):
    status = _q1(q, "status", "open")
    return 200, [hebrew.issue(dict(i)) for i in ctx.store.issues(None if status == "all" else status)]


def api_issue_set(ctx: Ctx, m, body):
    try:
        ok = ctx.store.set_issue_status(int(m.group(1)), str((body or {}).get("status", "")))
    except ValueError as e:
        return 400, {"error": str(e)}
    return (200, {"ok": True}) if ok else (404, {"error": "no such issue"})


def api_diagnose(ctx: Ctx, m, body):
    found = diagnose.run(ctx.store, ctx.cfg)
    return 200, {"found": len(found), "open": len(ctx.store.issues("open"))}


def api_review(ctx: Ctx, m, body):
    """Run the AI review for one conversation now (same budget, daily maximum and privacy rules)."""
    sid = unquote(m.group(1))
    if ctx.store.session(sid) is None:
        return 404, {"error": "no such conversation"}
    why = diagnose.review_skip_reason(ctx.store, ctx.cfg, sid)
    if why:
        return 409, {"error": f"AI review not run: {why}"}
    return 200, {"found": len(diagnose.llm_review(ctx.store, ctx.cfg, sid))}


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


# ---------------------------------------------------------------------- logs
def _conv_windows(ctx: Ctx) -> dict[str, list[tuple[float, float, str]]]:
    out: dict[str, list[tuple[float, float, str]]] = {}
    for s in ctx.store.rows("SELECT id, core_session, started, ended, last_event FROM sessions "
                            "WHERE core_session IS NOT NULL"):
        out.setdefault(s["core_session"], []).append((s["started"] - 1, (s["ended"] or s["last_event"] or
                                                                        s["started"]) + 1, s["id"]))
    return out


def _log_filters(q) -> dict:
    return {k: _q1(q, k) for k in ("level", "module", "code", "exclude", "since", "until", "q", "session", "file",
                                    "noisy", "limit", "before")}


def _log_query(ctx: Ctx, q, export: bool = False) -> dict:
    f = _log_filters(q)
    f["export"] = export
    window = None
    conv = _q1(q, "conv")
    if conv:
        s = ctx.store.session(conv)
        if s is None:
            raise LookupError("no such conversation")
        window = (s.get("core_session"), s["started"], s.get("ended") or s.get("last_event") or time.time())
    res = logview.query(ctx.logs, f, privacy=ctx.cfg.log.privacy, conv_window=window)
    wins = _conv_windows(ctx)
    for line in res["lines"]:
        line["conv"] = next((sid for a, b, sid in wins.get(str(line.get("session")), []) if a <= line["ts"] <= b), None)
    return res


def api_logs(ctx: Ctx, m, body, q=None):
    try:
        res = _log_query(ctx, q)
    except LookupError as e:
        return 404, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, {**res, "files": ctx.logs.files(), "privacy": ctx.cfg.log.privacy}


def api_log_facets(ctx: Ctx, m, body):
    return 200, logview.facets(ctx.logs)


def export_logs(ctx: Ctx, q) -> bytes:
    res = _log_query(ctx, q, export=True)
    return "".join(json.dumps(clean(line), ensure_ascii=False, default=str) + "\n"
                   for line in reversed(res["lines"])).encode("utf-8")


# ---------------------------------------------------------------------- memory (edit, delete, forget, undo)
def _undo_log(ctx: Ctx) -> list[dict]:
    """The journal minus entries older than MEMORY_UNDO_TTL_S (an expired entry can never be undone, even before
    the purge has rewritten the row)."""
    try:
        log = json.loads(ctx.store.meta("memory_undo") or "[]")
    except ValueError:
        return []
    now = ctx.clock()
    return [e for e in log if isinstance(e, dict) and now - float(e.get("at") or 0) < MEMORY_UNDO_TTL_S]


def purge_memory_undo(ctx: Ctx) -> int:
    """Owner decision (B): deleted facts must not sit in the undo journal forever. Drops every entry older than 24 h
    from store.db (meta memory_undo); runs on dashboard start, hourly while it runs, and on every journal write."""
    with ctx.lock:
        try:
            raw = json.loads(ctx.store.meta("memory_undo") or "[]")
        except ValueError:
            raw = []
        keep = _undo_log(ctx)
        if len(keep) != len(raw):
            with ctx.store._lock:
                ctx.store._set_meta("memory_undo", json.dumps(keep, ensure_ascii=False))
                ctx.store.commit()
        return len(raw) - len(keep)


def _push_undo(ctx: Ctx, entry: dict) -> None:
    with ctx.store._lock:
        log = (_undo_log(ctx) + [{**entry, "at": ctx.clock()}])[-MEMORY_UNDO_KEEP:]
        ctx.store._set_meta("memory_undo", json.dumps(log, ensure_ascii=False))
        ctx.store.commit()


def _describe_undo(e: dict | None) -> str | None:
    if not e:
        return None
    return {"edit": "edit of #{id}", "delete": "deleting {n} fact(s)", "add": "adding #{id}"}[e["op"]].format(
        id=e.get("id"), n=len(e.get("rows") or []))


def api_memory(ctx: Ctx, m, body):
    try:
        items = ctx.memory.list(500)
    except Exception as e:
        return 200, {"items": [], "error": f"memory not readable: {type(e).__name__}", "editable": False}
    sessions: dict[str, dict] = {}
    for it in items:
        if it.get("source") == "auto" and it.get("session"):
            g = sessions.setdefault(it["session"], {"session": it["session"], "facts": 0, "first": it["created"]})
            g["facts"] += 1
    log = _undo_log(ctx)
    return 200, {"items": items, "editable": True, "auto_sessions": list(sessions.values()),
                 "undo": _describe_undo(log[-1] if log else None), "curator": ctx.settings.view()["settings"]}


def api_memory_add(ctx: Ctx, m, body):
    text = str((body or {}).get("text", "")).strip()
    try:
        mid = ctx.memory.add(text, source="user")
    except ValueError as e:
        return 400, {"error": str(e)}
    _push_undo(ctx, {"op": "add", "id": mid})
    return 200, {"ok": True, "id": mid}


def api_memory_edit(ctx: Ctx, m, body):
    mid = int(m.group(1))
    text = str((body or {}).get("text", "")).strip()
    old = ctx.memory.get(mid)
    if old is None:
        return 404, {"error": "no such memory"}
    if not text or len(text) > 500:
        return 400, {"error": "a memory is 1 to 500 characters"}
    ctx.memory.update(mid, text)
    _push_undo(ctx, {"op": "edit", "id": mid, "old": old["text"]})
    return 200, {"ok": True}


def api_memory_delete(ctx: Ctx, m, body):
    mid = int(m.group(1))
    old = ctx.memory.get(mid)
    if old is None:
        return 404, {"error": "no such memory"}
    ctx.memory.delete(mid)
    _push_undo(ctx, {"op": "delete", "rows": [old]})
    return 200, {"ok": True}


def api_memory_forget_session(ctx: Ctx, m, body):
    """Forget everything the curator learned from one conversation (Curator.forget_session semantics)."""
    session = str((body or {}).get("session", "")).strip()
    if not session:
        return 400, {"error": "session is required"}
    rows = [r for r in ctx.memory.list(5000) if r.get("session") == session and r.get("source") == "auto"]
    n = ctx.memory.delete_session(session)
    if n:
        _push_undo(ctx, {"op": "delete", "rows": rows})
    return 200, {"ok": True, "deleted": n}


def api_memory_undo(ctx: Ctx, m, body):
    with ctx.lock:
        log = _undo_log(ctx)
        if not log:
            return 409, {"error": "nothing to undo"}
        e = log.pop()
        mem = ctx.memory
        if e["op"] == "edit":
            mem.update(int(e["id"]), e["old"])
        elif e["op"] == "add":
            mem.delete(int(e["id"]))
        else:
            for r in e.get("rows") or []:
                mem._x("INSERT OR IGNORE INTO memories (id, text, created, updated, source, session, confidence, "
                       "kind) VALUES (?,?,?,?,?,?,?,?)", (r["id"], r["text"], r["created"], r["updated"],
                                                          r.get("source", "user"), r.get("session"),
                                                          r.get("confidence", 1.0), r.get("kind", "fact")))
        with ctx.store._lock:
            ctx.store._set_meta("memory_undo", json.dumps(log, ensure_ascii=False))
            ctx.store.commit()
    return 200, {"ok": True, "undone": _describe_undo(e)}


# ---------------------------------------------------------------------- workspace (WP-D jarves.workspace)
def _ws_call(fn):
    from .workspace import WorkspaceError
    try:
        return 200, fn()
    except WorkspaceError as e:
        return 400, {"error": str(e)}


def api_workspace(ctx: Ctx, m, body):
    ws = ctx.workspace
    return 200, {"files": ws.files(), "writable": True, "provider": "jarves.workspace",
                 "notes": ws.daily_notes(30), "root": str(ws.root)}


def api_workspace_file(ctx: Ctx, m, body):
    name = unquote(m.group(1))

    def go():
        ws = ctx.workspace
        n = ws.path(name).name
        spec = ws.spec(n)
        return {"name": n, "text": ws.read(n), "writable": not spec.generated, "title": spec.title,
                "agent_edit": spec.agent_edit, "inject": list(spec.inject), "history": ws.history(n)}
    status, obj = _ws_call(go)
    return (404, obj) if status == 400 else (status, obj)


def api_workspace_write(ctx: Ctx, m, body):
    name, text = unquote(m.group(1)), str((body or {}).get("text", ""))
    return _ws_call(lambda: ctx.workspace.write(name, text, by="user", note="dashboard"))


def api_workspace_preview(ctx: Ctx, m, body):
    name, text = unquote(m.group(1)), str((body or {}).get("text", ""))

    def go():
        ctx.workspace.validate(name, text, "user")
        return {"diff": ctx.workspace.preview(name, text)}
    return _ws_call(go)


def api_workspace_version(ctx: Ctx, m, body):
    name, version = unquote(m.group(1)), unquote(m.group(2))

    def go():
        ws = ctx.workspace
        n = ws.path(name).name
        old = ws.read_version(n, version)
        return {"name": n, "version": version, "text": old, "diff": ws.diff(n, ws.read(n), old)}
    return _ws_call(go)


def api_workspace_restore(ctx: Ctx, m, body):
    name, version = unquote(m.group(1)), str((body or {}).get("version", ""))
    return _ws_call(lambda: ctx.workspace.restore(name, version, by="user"))


def api_workspace_undo(ctx: Ctx, m, body):
    return _ws_call(lambda: ctx.workspace.undo(unquote(m.group(1)), by="user"))


def api_workspace_note(ctx: Ctx, m, body):
    day = unquote(m.group(1))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return 400, {"error": "bad day"}
    p = ctx.workspace.root / "memory" / f"{day}.md"
    if not p.is_file():
        return 404, {"error": "no note for that day"}
    text = p.read_text(encoding="utf-8", errors="replace")
    return 200, {"day": day, "text": logs._mask(text) if ctx.cfg.log.privacy else text}


# ---------------------------------------------------------------------- settings
def api_settings(ctx: Ctx, m, body):
    try:
        editable, err = config_edit.view(ctx.config_path, ctx.cfg), None
    except Exception as e:                           # an unreadable config.toml: show why, offer no editing
        editable, err = [], str(e)
    return 200, {"config": ctx.cfg.model_dump(), "home": str(ctx.home), "config_file": str(ctx.config_path),
                 "read_only": False, "editable": editable, "error": err,
                 "runtime": ctx.settings.view()["settings"],
                 "backups": [p.name for p in config_edit.backups(ctx.config_path)],
                 "features_page": _has_module("jarves.features"),
                 "hint": "config.toml values apply when the Core starts next; the runtime switches apply at once.",
                 "hint_he": hebrew.he("config.toml values apply when the Core starts next; the runtime switches apply at once.")}


def api_settings_write(ctx: Ctx, m, body):
    updates = (body or {}).get("values")
    if not isinstance(updates, dict) or not updates:
        return 400, {"error": "send {\"values\": {\"section.key\": value}}"}
    try:
        res = config_edit.apply(ctx.config_path, updates)
    except (ValueError, TypeError) as e:
        return 400, {"error": str(e)}
    except Exception as e:                           # ConfigError: validation; the file was not touched
        return 400, {"error": str(e)}
    logs.info("dashboard", "CONFIG_EDITED", "config.toml changed from the dashboard", keys=sorted(res["changed"]),
              backup=res["backup"])
    return 200, {"ok": True, **res, "applies": "next start"}


def api_settings_undo(ctx: Ctx, m, body):
    try:
        res = config_edit.undo(ctx.config_path)
    except Exception as e:
        return 409, {"error": str(e)}
    logs.info("dashboard", "CONFIG_RESTORED", "config.toml restored from a backup", backup=res["restored"])
    return 200, {"ok": True, **res}


def api_runtime_set(ctx: Ctx, m, body):
    """skills.settings overrides (voice.voice, curator.auto, curator.llm): stored in memory.db, used at once."""
    from .workspace import WorkspaceError
    key, value = str((body or {}).get("key", "")), (body or {}).get("value")
    try:
        if value is None or value == "":
            ctx.settings.reset(key)
            v = ctx.settings.get(key)
        else:
            v = ctx.settings.set(key, str(value))
    except (KeyError, WorkspaceError) as e:
        return 400, {"error": str(e).strip("'")}
    logs.info("dashboard", "SETTING_CHANGED", f"{key} set from the dashboard", key=key, value=v)
    return 200, {"ok": True, "key": key, "value": v}


for _m, _p, _h in [
        ("GET", r"/api/overview", api_overview), ("GET", r"/api/sessions", api_sessions),
        ("GET", r"/api/session/([^/]+)", api_session), ("GET", r"/api/session/([^/]+)/raw", api_session_raw),
        ("POST", r"/api/session/([^/]+)/review", api_review), ("GET", r"/api/live", api_live),
        ("GET", r"/api/costs", api_costs), ("GET", r"/api/tasks", api_tasks),
        ("GET", r"/api/logs", api_logs), ("GET", r"/api/logs/facets", api_log_facets),
        ("GET", r"/api/memory", api_memory), ("POST", r"/api/memory", api_memory_add),
        ("PUT", r"/api/memory/(\d+)", api_memory_edit), ("DELETE", r"/api/memory/(\d+)", api_memory_delete),
        ("POST", r"/api/memory/forget-session", api_memory_forget_session),
        ("POST", r"/api/memory/undo", api_memory_undo),
        ("GET", r"/api/skills", api_skills), ("GET", r"/api/issues", api_issues),
        ("POST", r"/api/issues/(\d+)", api_issue_set), ("POST", r"/api/diagnose", api_diagnose),
        ("GET", r"/api/workspace", api_workspace), ("GET", r"/api/workspace/([^/]+)", api_workspace_file),
        ("PUT", r"/api/workspace/([^/]+)", api_workspace_write),
        ("POST", r"/api/workspace/([^/]+)/preview", api_workspace_preview),
        ("GET", r"/api/workspace/([^/]+)/version/([^/]+)", api_workspace_version),
        ("POST", r"/api/workspace/([^/]+)/restore", api_workspace_restore),
        ("POST", r"/api/workspace/([^/]+)/undo", api_workspace_undo),
        ("GET", r"/api/workspace-note/([^/]+)", api_workspace_note),
        ("GET", r"/api/settings", api_settings), ("PUT", r"/api/settings", api_settings_write),
        ("POST", r"/api/settings/undo", api_settings_undo), ("POST", r"/api/settings/runtime", api_runtime_set)]:
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

    def _static(self, name: str) -> None:
        p = (STATIC / name).resolve()
        if STATIC.resolve() not in p.parents or not p.is_file():
            return self._send(404, b"not found", "text/plain")
        return self._send(200, p.read_bytes(), _TYPES.get(p.suffix, "application/octet-stream"))

    def _pair(self, ip: str) -> None:
        """POST /api/pair {code, name} from a phone on the LAN -> device cookie (dashboard_auth.Access.pair)."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > 4096:                            # rfile.read(-1) would block the thread until EOF
            return self._json(400, {"error": "bad Content-Length"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "invalid JSON"})
        got = self.access.pair(str(body.get("code", "")), str(body.get("name", "")), ip)
        if got is None:
            return self._json(403, {"error": "wrong or expired code: make a new one on the computer"})
        self.send_response(200)
        body = json.dumps({"ok": True, "device": got["device"]}).encode()
        for k, v in (("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                     ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff")):
            self.send_header(k, v)
        self.send_header("Set-Cookie", f"{DEVICE_COOKIE}={got['cookie']}; HttpOnly; SameSite=Strict; Path=/; "
                                       f"Max-Age={DEVICE_IDLE_S}")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        a, ip = self.access, self.client_address[0]
        if not client_allowed(ip) or (not a.lan and not is_loopback(ip)):
            return self._send(403, b"forbidden network", "text/plain")       # never answer the internet
        if not a.host_ok(self.headers.get("Host") or ""):
            return self._send(403, b"forbidden host", "text/plain")          # DNS rebinding / spoofed Host
        if not a.rate_ok(ip):
            return self._send(429, b"too many requests", "text/plain", {"Retry-After": "60"})
        if not a.origin_ok(self.headers, self.command):
            a.audit("refused_cross_origin", ip=ip, path=urlsplit(self.path).path)
            return self._json(403, {"error": "cross-origin request refused"})
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        if self.command == "GET" and url.path == "/" and "t" in q:
            if is_loopback(ip) and secrets.compare_digest(q["t"][0], self.token):
                return self._send(302, b"", "text/plain", {
                    "Location": "/", "Set-Cookie": f"{COOKIE}={self.token}; HttpOnly; SameSite=Strict; Path=/"})
            return self._send(403, b"wrong token: open the link printed by `jarvis dashboard`", "text/plain")
        if a.lan and (self.command, url.path) in PUBLIC_PATHS:
            if url.path == "/pair":
                return self._static("pair.html")
            if url.path == "/api/pair":
                if self.headers.get("X-Jarves") != "1":
                    return self._json(403, {"error": "missing X-Jarves header"})
                return self._pair(ip)
            return self._static(url.path.removeprefix("/static/"))
        who = a.principal(parse_cookies(self.headers.get("Cookie") or ""), ip)
        if who is None:
            msg = ("Open /pair on this device to pair it (code from the computer)." if a.lan and not is_loopback(ip)
                   else "Open the link printed by `jarvis dashboard` (it holds a one-time token).")
            return self._send(403, msg.encode(), "text/plain; charset=utf-8")
        if not a.allowed(who, self.command, url.path):
            a.audit("refused_privilege", principal=who["id"], method=self.command, path=url.path)
            return self._json(403, {"error": "only the computer itself can do this"})
        if who["kind"] == "device" and self.command not in ("GET", "HEAD") and not secrets.compare_digest(
                self.headers.get("X-CSRF-Token") or "", who["csrf"]):
            a.audit("refused_csrf", principal=who["id"], method=self.command, path=url.path)
            return self._json(403, {"error": "missing or wrong CSRF token (reload the page)"})
        self.ctx._tl.who = who
        if url.path == "/" or url.path.startswith("/static/"):
            if self.command not in ("GET", "HEAD"):
                return self._send(405, b"", "text/plain")
            return self._static("index.html" if url.path == "/" else url.path.removeprefix("/static/"))
        if url.path == "/export/logs.jsonl" and self.command == "GET":
            try:
                data = export_logs(self.ctx, q)
            except (LookupError, ValueError) as e:
                return self._json(400, {"error": str(e)})
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return self._send(200, data, "application/x-ndjson; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="jarves-log-{stamp}.jsonl"'})
        if self.command != "GET" and self.headers.get("X-Jarves") != "1":
            return self._json(403, {"error": "missing X-Jarves header"})
        body = None
        if self.command in ("POST", "PUT"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0:                                    # a negative length -> rfile.read(-1) blocks forever
                return self._json(400, {"error": "bad Content-Length"})
            if n > 1_000_000:
                return self._json(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "invalid JSON"})
        if who["kind"] == "device" and self.command not in ("GET", "HEAD") \
                and not device_body_ok(self.ctx, self.command, url.path, body):
            a.audit("refused_privilege", principal=who["id"], method=self.command, path=url.path)
            return self._json(403, {"error": "only the computer itself can do this"})
        for method, rx, fn in _ROUTES:
            mt = rx.match(url.path)
            if mt and method == self.command:
                try:
                    takes_q = getattr(getattr(fn, "__code__", None), "co_argcount", 3) >= 4
                    status, obj = fn(self.ctx, mt, body, q) if takes_q else fn(self.ctx, mt, body)
                except Exception as e:
                    status, obj = 500, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
                if method != "GET":           # audit: config changes, approvals (who, what, result; no bodies)
                    a.audit("write", principal=who["id"], method=method, path=url.path, status=status)
                return self._json(status, obj)
        return self._json(404, {"error": "not found"})

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle


class Dashboard:
    """Owns the HTTP server; `url` holds the one-time token. start() runs it in a daemon thread.
    workspace / memory / settings: the Core's own objects when it is opened from inside the Core.
    lan=True (opt-in, the dashboard_lan switch): also reachable from paired devices on the home network."""

    def __init__(self, home: Path, cfg, port: int = 0, *, workspace=None, memory=None, settings=None,
                 config_path=None, lan: bool = False, lan_host: str | None = None) -> None:
        self.ctx = Ctx(home, cfg, workspace=workspace, memory=memory, settings=settings, config_path=config_path)
        self.plugins = load_plugins()
        token = secrets.token_urlsafe(24)
        if lan:
            lan_host = lan_host or lan_ip()
            if lan_host is None:
                raise OSError("no private home-network address found (Wi-Fi/Ethernet connected?)")
        self.access = Access(home, token, lan=lan, lan_host=lan_host)
        self.ctx.access = self.access
        handler = type("H", (Handler,), {"ctx": self.ctx, "token": token, "access": self.access})
        self.httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port or (LAN_PORT if lan else 0)),
                                         handler)
        self.httpd.daemon_threads = True
        self.port = self.ctx.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?t={token}"
        self.lan_url = f"http://{lan_host}:{self.port}/pair" if lan else None
        if lan:
            self.access.audit("lan_mode_started", host=lan_host, port=self.port)
        self._thread: threading.Thread | None = None

    def _housekeeping(self) -> None:
        try:
            purge_memory_undo(self.ctx)
        except Exception as e:
            logs.warn("dashboard", "UNDO_PURGE_FAILED", "could not purge the memory undo journal",
                      error=type(e).__name__)

    def _housekeeping_loop(self) -> None:
        while not self._stop_evt.wait(UNDO_PURGE_EVERY_S):
            self._housekeeping()

    def start(self) -> "Dashboard":
        self._housekeeping()                         # on every dashboard start
        self._stop_evt = threading.Event()
        threading.Thread(target=self._housekeeping_loop, name="jarves-dashboard-housekeeping", daemon=True).start()
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="jarves-dashboard", daemon=True)
        self._thread.start()
        return self

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def stop(self) -> None:
        getattr(self, "_stop_evt", threading.Event()).set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.ctx.close()


# ---------------------------------------------------------------------- inside the Core: tool + face button
class DashboardSkill(Skill):
    """`open_dashboard`: start (once) the dashboard inside the Core, open it in the browser, return the local URL
    (without the one-time key: tool results reach the model and the log)."""
    name = "dashboard"
    description = "the local web dashboard (conversations, logs, costs, memory, settings)"

    def __init__(self, opener) -> None:
        self.opener = opener                         # async () -> url (Core.open_dashboard)

    def tools(self) -> list[Tool]:
        async def open_(a):
            try:
                url = await self.opener()
            except Exception as e:
                return ToolResult(False, "לא הצלחתי לפתוח את לוח הבקרה.", {"error": type(e).__name__},
                                  code="DASHBOARD_FAILED")
            local = url.split("?", 1)[0]
            return ToolResult(True, "פתחתי את לוח הבקרה בדפדפן.", {"url": local}, code="ok")
        return [Tool("open_dashboard", "Open JARVES's dashboard (conversations, logs, costs, memory, settings) in "
                     "the browser on this computer. Returns its local address.", open_, permission="action")]
