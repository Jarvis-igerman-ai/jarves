"""What JARVES knew in each conversation (owner request, Issue #17: "in the dashboard, in each conversation, I need
a way to see his context").

CAPTURE (in the Core). When a GPT-Live session starts, the exact session configuration sent to the server is split
into blocks: the voice (front) instructions as sent - persona (FRONT_INSTRUCTIONS + name), self-settings rules,
workspace files (SOUL / IDENTITY / USER, truncated as injected), remembered facts, the feature on/off note, the
guides briefing, a setup suggestion, the greeting request - and the backend (delegation) instructions with their
own blocks (rules, remembered facts, workspace files, guides briefing) and tool definitions. Next to the blocks:
the tool list (offered or left out, and why), the remembered facts really in the prompt (id, source, confidence),
models / voice / effort / tier, an estimate of the tokens of each block (chars, no tokenizer: "estimate") and a
hash per block. During the conversation every piece of context that reaches the model becomes an event with its
time: [clock] notes, workspace-change notes (a new version of the workspace block), progress / approval / stall
notes, background-task announcements and tool results. At the end: facts learned or forgotten and whether the
persona changed (they apply to the NEXT conversation). Typed chat: the backend instructions of each request (a new
event only when a block changed).

STORAGE: two tables in store.db (context_traces, context_events), pruned with history.keep_days and cleared by the
privacy actions. Secrets never reach them: every text passes diagbundle.sanitize (logs.redact: API keys, bearer
tokens, JWTs, key=value credentials; e-mail addresses masked) plus a final leak scan. Privacy mode (log.privacy on
when the context is captured): only names, ids, sizes, token estimates and hashes are kept, never text.

HOOKS (no change to the conversation itself): attach_live() wraps live.session_config OUTERMOST (after the
workspace / features / guides wrappers, so it sees the final text) and taps the connection the session is started
on (LiveVoice.start opens the connection before it calls session_config); attach_brain() wraps the typed-chat
reply. Every capture step is guarded: a failure here never affects a conversation.

DASHBOARD: dashboard_routes() (plugin) + dashboard_static/context.js (#/context/<session>; a "Context" button on
each conversation page; "where was this used" on workspace file pages).
"""
from __future__ import annotations

import difflib
import hashlib
import inspect
import json
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import diagbundle, features, logs
from .capabilities import PI_ROADMAP

FEATURE = "context_trace"
features.register(FEATURE, "ההקשר של כל שיחה", "Conversation context view", True, applies="next_session",
                  about="keeps what JARVES was given in each conversation (instructions, memory, tools, notes) for the "
                        "dashboard; only sizes and names while privacy is on",
                  targets={"windows": {"status": "implemented_unverified",
                                       "evidence": "core/tests/test_context_trace.py (capture, privacy, retention, "
                                                   "routes) + a real silent GPT-Live run (docs/reviews/"
                                                   "SWV2_CONTEXT_TRACE.md)"},
                           "linux_arm64_pi": {"status": "implemented_unverified", "roadmap_issue": PI_ROADMAP,
                                              "blocker": "pure Python + SQLite, no platform code; not run on a Pi "
                                                         "yet"}})

SCHEMA = """
CREATE TABLE IF NOT EXISTS context_traces (
    id TEXT PRIMARY KEY, core_session TEXT, kind TEXT NOT NULL, started REAL NOT NULL, ended REAL,
    session_id TEXT, private INTEGER NOT NULL DEFAULT 0, meta TEXT, blocks TEXT, memory TEXT, tools TEXT,
    total_tokens INTEGER, end_reason TEXT);
CREATE TABLE IF NOT EXISTS context_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
    label TEXT, block TEXT, text TEXT, chars INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
    hash TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS ctx_traces_t ON context_traces (started);
CREATE INDEX IF NOT EXISTS ctx_traces_s ON context_traces (session_id);
CREATE INDEX IF NOT EXISTS ctx_events_t ON context_events (trace_id, ts);
"""
BLOCK_MAX_CHARS = 20_000        # a block longer than this is stored cut (its size and hash stay the real ones)
EVENT_MAX_CHARS = 4_000
TYPED_GAP_S = 600               # = store.TYPED_GAP_S: typed requests closer than this share one trace
LIVE_MATCH_S = (30.0, 3.0)      # a live trace belongs to the session whose LIVE_STARTED is up to 30 s later
_LEAK = diagbundle._LEAK

# Where each block starts in the instructions (the wrappers append their text after "\n\n"; the first marker found
# at a line start begins the block, everything before the first marker is the persona / rules text).
FRONT_MARKERS = [
    (r"Also delegate \(a word or two", "self", "Self-settings rules", "workspace.FRONT_SELF"),
    (r"Workspace files \(the user can edit them", "workspace", "Workspace files", "workspace.section('front')"),
    (r"Things you know about the user \(from memory\):", "memory", "Remembered facts", "memory.for_prompt"),
    (r"Preferences the owner confirmed \(follow them", "prefs", "Confirmed preferences",
     "preferences.prompt_block"),
    (r"Features that are OFF or not available right now:", "features", "Feature on/off note",
     "features.front_note"),
    (r"When the user asks what you can do, or how to set up", "guides", "Capability briefing (guides)",
     "guides.front_briefing"),
    (r"Setup suggestion for this conversation", "suggestion", "Setup suggestion", "guides.Suggester"),
]
BACKEND_MARKERS = [
    (r"Facts the user asked you to remember:", "memory", "Remembered facts", "memory.list"),
    (r"Workspace files \(the user can edit them", "workspace", "Workspace files", "workspace.section('backend')"),
    (r"Preferences the owner confirmed \(follow them", "prefs", "Confirmed preferences",
     "preferences.prompt_block"),
    (r"## What I can do and how to set it up \(guides\)", "guides", "Capability briefing (guides)",
     "guides.briefing"),
]
WS_NOTE_BLOCK = "front.workspace.update"     # a workspace-change note during the conversation (newest wins)
_WS_FILE = re.compile(r"^\[([A-Z]+\.md) - ([^\]\n]*)\]$", re.M)
_WS_CLEAN = re.compile(r"[^A-Za-z0-9.]")


# ---------------------------------------------------------------------- helpers
def estimate_tokens(text: str | None) -> int:
    """Rough token count (no tokenizer installed): ~4 Latin characters, ~2.2 Hebrew characters per token."""
    if not text:
        return 0
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return max(1, round(ascii_n / 4 + (len(text) - ascii_n) / 2.2))


def digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def sanitize(text: str) -> str:
    """No credentials and no e-mail addresses (the redaction used for logs and support bundles)."""
    return _LEAK.sub("<redacted>", diagbundle.sanitize(str(text)))


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f"\n...(cut here for storage: {len(text)} characters in all)"


def make_block(group: str, bid: str, label: str, source: str, text: str, private: bool, **extra) -> dict:
    """One context block. Size, tokens and hash are of the text as SENT; the text itself only without privacy."""
    text = text or ""
    return {"id": f"{group}.{bid}", "group": group, "label": label, "source": source, "chars": len(text),
            "tokens": estimate_tokens(text), "hash": digest(text),
            "text": None if private else sanitize(_cut(text, BLOCK_MAX_CHARS)), **extra}


def split_instructions(group: str, text: str, markers: list, private: bool) -> list[dict]:
    """Instructions as sent -> blocks (the joined text of all blocks is exactly the instructions)."""
    text = text or ""
    found = []
    for rx, bid, label, source in markers:
        m = re.search(r"(?:^|\n)(" + rx + ")", text)
        if m:
            found.append((m.start(1), bid, label, source))
    found.sort()
    base = {"front": ("persona", "Persona and conversation rules", "voice.live.FRONT_INSTRUCTIONS + name"),
            "backend": ("rules", "Backend rules", "brain.INSTRUCTIONS + name")}[group]
    cuts = [(0, *base)] + [f for f in found if f[0] > 0]
    out = []
    for i, (pos, bid, label, source) in enumerate(cuts):
        end = cuts[i + 1][0] if i + 1 < len(cuts) else len(text)
        part = text[pos:end].strip("\n")
        if not part:
            continue
        if bid == "workspace":
            out.extend(_workspace_blocks(group, part, label, source, private))
        else:
            out.append(make_block(group, bid, label, source, part, private))
    return out


def _workspace_blocks(group: str, part: str, label: str, source: str, private: bool) -> list[dict]:
    files = list(_WS_FILE.finditer(part))
    if not files:
        return [make_block(group, "workspace", label, source, part, private)]
    out = [make_block(group, "workspace", label + " (header)", source, part[:files[0].start()].strip("\n"), private)]
    for i, m in enumerate(files):
        end = files[i + 1].start() if i + 1 < len(files) else len(part)
        name = _WS_CLEAN.sub("", m.group(1))
        out.append(make_block(group, f"workspace.{name}", f"{name} ({m.group(2)})", f"workspace/{name}",
                              part[m.start():end].strip("\n"), private, file=name))
    return out


def tool_list(registry, sent: list[dict]) -> list[dict]:
    """Every registered tool: offered in this session or not, and why; permission and the policy that applies."""
    from .skills import PERMISSIONS
    names = {t.get("name") or t.get("type") for t in sent or []}
    out, seen = [], set()
    for name, (skill, tool) in (getattr(registry, "_tools", None) or {}).items():
        why = None
        if skill.name in getattr(registry, "disabled", set()):
            why = "the whole Skill is switched off (skills.disabled)"
        else:
            gate = registry._gated(skill, tool) if hasattr(registry, "_gated") else None
            if gate:
                why = f"feature off or unavailable: {gate}"
        offered = name in names
        if not offered and why is None:
            why = "not in this session's tool list (registered or switched on after the start)"
        policy = None
        if tool.permission not in PERMISSIONS:
            policy = "no valid permission: refused if called"
        elif tool.permission == "action" and name not in getattr(registry, "allowed_actions", set()):
            policy = "action not on the allow-list: refused if called"
        elif tool.permission == "destructive" or tool.confirm:
            policy = "asks the user on the screen first"
        out.append({"name": name, "skill": skill.name, "permission": tool.permission, "offered": offered,
                    "why_not": sanitize(why) if why else None, "policy": policy,
                    "chars": len(tool.description or "")})
        seen.add(name)
    for n in sorted(x for x in names - seen if x):
        out.append({"name": n, "skill": "hosted (OpenAI)", "permission": "read", "offered": True, "why_not": None,
                    "policy": None, "chars": 0})
    return out


def _tools_block(tools: list[dict], private: bool) -> dict:
    lines = [f"- {t.get('name') or t.get('type')}: {t.get('description') or '(hosted tool)'}" for t in tools or []]
    b = make_block("backend", "tools", "Tool definitions", "skills registry (live_tools)", "\n".join(lines), private)
    b["tokens"] = estimate_tokens(json.dumps(tools or [], ensure_ascii=False))   # the schemas count too
    return b


def memory_in_prompt(memory, front_text: str, backend_text: str, private: bool) -> list[dict]:
    """The remembered facts that really are in the instructions sent (front: for_prompt(8, 0.6), backend: list(20))."""
    if memory is None:
        return []
    from .workspace import FRONT_FACTS
    rows: dict[int, dict] = {}
    for target, cands, text in (("front", memory.for_prompt(FRONT_FACTS, min_confidence=0.6), front_text),
                                ("backend", memory.list(20), backend_text)):
        for r in cands:
            fact = str(r.get("text") or "")
            state = "in" if f"- {fact}" in (text or "") else "cut" if fact[:30] and fact[:30] in (text or "") else None
            if state is None:
                continue
            d = rows.setdefault(r["id"], {"id": r["id"], "text": None if private else sanitize(fact),
                                          "chars": len(fact), "hash": digest(fact), "source": r.get("source"),
                                          "confidence": r.get("confidence"), "kind": r.get("kind"),
                                          "learned_in": r.get("session"), "updated": r.get("updated"),
                                          "targets": []})
            d["targets"].append(target if state == "in" else f"{target} (cut)")
    return list(rows.values())


def snapshot(config: dict, *, memory=None, registry=None, private: bool = False, extra_meta: dict | None = None) -> dict:
    """The context of one GPT-Live session from its session configuration (what start() sends)."""
    front = str(config.get("instructions") or "")
    deleg = ((config.get("delegation") or {}).get("responses") or {})
    backend = str(deleg.get("instructions") or "")
    tools = deleg.get("tools") or []
    blocks = split_instructions("front", front, FRONT_MARKERS, private)
    for item in config.get("input") or []:
        for c in item.get("content") or [] if isinstance(item, dict) else []:
            if isinstance(c, dict) and c.get("text"):
                blocks.append(make_block("front", "greeting", "Greeting request (with the first [clock] note)",
                                         "app greeting + voice.live.clock_note", c["text"], private))
    blocks += split_instructions("backend", backend, BACKEND_MARKERS, private)
    blocks.append(_tools_block(tools, private))
    audio = (config.get("audio") or {}).get("output") or {}
    meta = {"model": config.get("model"), "voice": audio.get("voice"), "backend_model": deleg.get("model"),
            "reasoning": deleg.get("reasoning"), "service_tier": deleg.get("service_tier") or "default",
            "max_output_tokens": deleg.get("max_output_tokens"), "verbosity": (deleg.get("text") or {}).get("verbosity"),
            "tool_choice": deleg.get("tool_choice"), "parallel_tool_calls": deleg.get("parallel_tool_calls"),
            "web_search": any((t or {}).get("type") == "web_search" for t in tools),
            "front_chars": len(front), "backend_chars": len(backend), **(extra_meta or {})}
    return {"meta": meta, "blocks": blocks, "tools": tool_list(registry, tools) if registry is not None else [],
            "memory": memory_in_prompt(memory, front, backend, private),
            "total_tokens": sum(b["tokens"] for b in blocks)}


# ---------------------------------------------------------------------- storage
def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)


class Recorder:
    """Own SQLite connection to store.db + one writer thread (capture never blocks the voice event loop)."""

    def __init__(self, path: Path | str, keep_days: int | None = None) -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        ensure_schema(self._db)
        self._db.commit()
        if keep_days:
            prune(self._db, keep_days)
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="jarves-context", daemon=True)
        self._thread.start()

    def put(self, sql: str, args: tuple) -> None:
        self._q.put((sql, args))

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is None:
                    break
                self._db.execute(*item)
                if self._q.empty():
                    self._db.commit()
            except Exception as e:                    # never log from the writer: history must not feed back
                print(f"jarves context: {type(e).__name__}: {str(e)[:120]}", flush=True)
            finally:
                self._q.task_done()
        self._db.commit()

    def flush(self) -> None:
        self._q.join()
        self._db.commit()

    def close(self) -> None:
        if self._thread.is_alive():
            self._q.put(None)
            self._thread.join(timeout=5)
        self._db.close()


def prune(db: sqlite3.Connection, keep_days: int) -> int:
    cut = time.time() - keep_days * 86400
    db.execute("DELETE FROM context_events WHERE trace_id IN (SELECT id FROM context_traces WHERE started < ?)",
               (cut,))
    n = db.execute("DELETE FROM context_traces WHERE started < ?", (cut,)).rowcount
    db.commit()
    return n


def _mask_json(raw: str | None) -> str | None:
    """Drop every "text" value (blocks, memory, event data), keep sizes / hashes / names."""
    if not raw:
        return raw
    try:
        v = json.loads(raw)
    except ValueError:
        return None

    def strip(x):
        if isinstance(x, dict):
            return {k: (None if k in ("text", "args", "output", "old_text") else strip(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [strip(i) for i in x]
        return x
    return json.dumps(strip(v), ensure_ascii=False)


def mask_all(db) -> int:
    """Privacy action "mask existing history": the words go, sizes / hashes / names stay."""
    ensure_schema(db)
    n = 0
    for r in db.execute("SELECT id, blocks, memory, meta FROM context_traces").fetchall():
        db.execute("UPDATE context_traces SET blocks = ?, memory = ?, private = 1 WHERE id = ?",
                   (_mask_json(r[1]), _mask_json(r[2]), r[0]))
        n += 1
    for r in db.execute("SELECT id, data FROM context_events").fetchall():
        db.execute("UPDATE context_events SET text = NULL, data = ? WHERE id = ?", (_mask_json(r[1]), r[0]))
    db.commit()
    return n


def delete_all(db) -> int:
    """Privacy action "delete all stored conversations"."""
    ensure_schema(db)
    n = db.execute("DELETE FROM context_traces").rowcount
    db.execute("DELETE FROM context_events")
    db.commit()
    return n


_recorders: dict[str, Recorder] = {}
_rlock = threading.Lock()


def recorder(home: Path, cfg=None) -> Recorder | None:
    """The Core's recorder for <home>/store.db (None when the history is switched off)."""
    hist = getattr(cfg, "history", None)
    if hist is not None and not hist.enabled:
        return None
    path = str(Path(home) / "store.db")
    with _rlock:
        if path not in _recorders:
            _recorders[path] = Recorder(path, getattr(hist, "keep_days", None))
        return _recorders[path]


def close_home(home: Path) -> None:
    """The Core stopped: flush and close its recorder (a new one opens on the next capture)."""
    with _rlock:
        r = _recorders.pop(str(Path(home) / "store.db"), None)
    if r is not None:
        r.close()


def close_all() -> None:
    with _rlock:
        rs = list(_recorders.values())
        _recorders.clear()
    for r in rs:
        r.close()


# ---------------------------------------------------------------------- one conversation
class Trace:
    def __init__(self, rec: Recorder, kind: str, private: bool, started: float | None = None) -> None:
        self.rec, self.kind, self.private = rec, kind, private
        self.id = uuid.uuid4().hex[:16]
        self.started = started or time.time()
        self.calls: dict[str, tuple[str, str]] = {}     # call_id -> (tool, arguments) of tool calls in flight
        self.start_state: dict = {}
        self.block_hashes: dict[str, str] = {}

    def save_start(self, snap: dict) -> None:
        self.block_hashes = {b["id"]: b["hash"] for b in snap["blocks"]}
        self.rec.put("INSERT INTO context_traces (id, core_session, kind, started, private, meta, blocks, memory, "
                     "tools, total_tokens) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (self.id, logs._SESSION, self.kind, self.started, int(self.private),
                      json.dumps(snap["meta"], ensure_ascii=False, default=str),
                      json.dumps(snap["blocks"], ensure_ascii=False), json.dumps(snap["memory"], ensure_ascii=False),
                      json.dumps(snap["tools"], ensure_ascii=False), snap["total_tokens"]))

    def event(self, kind: str, label: str, text: str | None = None, *, block: str | None = None,
              data: dict | None = None, ts: float | None = None) -> None:
        text = "" if text is None else str(text)
        if data:            # SW-V4 B: never a secret; in privacy mode the field registry masks the values too
            data = logs.redact_deep(data)
            if self.private:
                data = {k: logs.render_field(k, v, private=True) for k, v in data.items()}
        self.rec.put("INSERT INTO context_events (trace_id, ts, kind, label, block, text, chars, tokens, hash, data) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (self.id, ts or time.time(), kind, label, block,
                      None if self.private or not text else sanitize(_cut(text, EVENT_MAX_CHARS)), len(text),
                      estimate_tokens(text), digest(text) if text else None,
                      json.dumps(data, ensure_ascii=False, default=str) if data else None))

    def end(self, reason: str, data: dict | None = None) -> None:
        self.event("end", f"conversation ended ({reason})", data=data)
        self.rec.put("UPDATE context_traces SET ended = ?, end_reason = ? WHERE id = ?",
                     (time.time(), str(reason)[:60], self.id))


def _enabled(feats) -> bool:
    try:
        return (feats or features.current()).enabled(FEATURE)
    except Exception:
        return True


def _private() -> bool:
    return logs.privacy()


# ---------------------------------------------------------------------- live hooks
COMMENTARY_KINDS = (("jarves_task_", "task", "Background-task result announced"),
                    ("jarves_progress_confirm", "confirm", "Approval reminder note"),
                    ("jarves_progress_", "progress", "Progress note (slow lookup)"),
                    ("jarves_stall_", "stall", "Stalled-lookup note (Core retry / honest failure)"))


def _on_thinking(live, content, event_id) -> None:
    tr = getattr(live, "_ctx_trace", None)
    if tr is None:
        return
    text = str(content or "")
    if text.startswith("[clock]"):
        tr.event("clock", "Clock note", text)
    elif text.startswith("[workspace update"):
        name = text[len("[workspace update: "):].split(" ", 1)[0]
        tr.event("workspace", f"Workspace changed: {name}", text, block=WS_NOTE_BLOCK,
                 data={"file": name, "version_hash": digest(text)})
    else:
        tr.event("thinking", "Silent context note", text)


def _on_commentary(live, content, event_id) -> None:
    tr = getattr(live, "_ctx_trace", None)
    if tr is None:
        return
    eid = str(event_id or "")
    kind, label = next(((k, lb) for p, k, lb in COMMENTARY_KINDS if eid.startswith(p)), ("note", "Commentary note"))
    data = {"event_id": eid}
    if kind == "task":
        data["task"] = eid[len("jarves_task_"):].rsplit("_", 1)[0]
    tr.event(kind, label, str(content or ""), data=data)


def _on_item(live, item) -> None:
    tr = getattr(live, "_ctx_trace", None)
    if tr is None or not isinstance(item, dict) or item.get("type") != "function_call_output":
        return
    name, args = tr.calls.pop(str(item.get("call_id") or ""), ("?", ""))
    out = str(item.get("output") or "")
    ok = code = None
    try:
        parsed = json.loads(out)
        ok, code = parsed.get("ok"), parsed.get("code")
    except (ValueError, AttributeError):
        pass
    data = {"tool": name, "ok": ok, "code": code, "args_chars": len(args or "")}
    try:                                         # readable Hebrew instead of \\u escapes
        args = json.dumps(json.loads(args), ensure_ascii=False) if args else args
    except ValueError:
        pass
    if not tr.private:
        data["args"] = sanitize(_cut(str(args or ""), 600))
    label = f"Tool result: {name}" + ("" if ok is None else " (ok)" if ok else " (failed)")
    tr.event("tool_result", label, out, data=data)


def _wrap_async(orig, before_after):
    async def wrapper(*a, **kw):
        r = orig(*a, **kw)
        if inspect.isawaitable(r):
            r = await r
        try:
            before_after(*a, **kw)
        except Exception:
            pass                                     # capture never breaks a conversation
        return r
    wrapper._jarves_ctx = True
    return wrapper


def _tap(live) -> None:
    """Record what is sent on this connection: thinking / commentary notes and tool results."""
    conn = getattr(live, "conn", None)
    if conn is None:
        return
    session = getattr(conn, "session", None)
    for res, method, fn in ((getattr(session, "thinking", None), "append",
                             lambda *a, **kw: _on_thinking(live, kw.get("content"), kw.get("event_id"))),
                            (getattr(session, "commentary", None), "append",
                             lambda *a, **kw: _on_commentary(live, kw.get("content"), kw.get("event_id"))),
                            (getattr(getattr(conn, "response", None), "item", None), "create",
                             lambda *a, **kw: _on_item(live, kw.get("item", a[0] if a else None)))):
        orig = getattr(res, method, None) if res is not None else None
        if orig is None or getattr(orig, "_jarves_ctx", False):
            continue
        try:
            setattr(res, method, _wrap_async(orig, fn))
        except (AttributeError, TypeError):
            pass


def _end_state(memory, workspace) -> dict:
    out = {}
    if memory is not None:
        out["facts"] = {r["id"]: r for r in memory.list(500)}
    if workspace is not None:
        out["front_hash"] = digest(workspace.section("front"))
        out["backend_hash"] = digest(workspace.section("backend"))
    return out


def _fact(r: dict, private: bool) -> dict:
    return {"id": r["id"], "text": None if private else sanitize(str(r.get("text") or "")),
            "chars": len(str(r.get("text") or "")), "source": r.get("source"), "confidence": r.get("confidence")}


def attach_live(live, *, home: Path, cfg=None, memory=None, workspace=None, registry=None, feats=None) -> bool:
    """Capture this LiveVoice's context (call AFTER the other session_config wrappers). False if not applicable."""
    orig = getattr(live, "session_config", None)
    hist = getattr(cfg, "history", None)
    if orig is None or (hist is not None and not hist.enabled):
        return False

    def session_config() -> dict:
        config = orig()
        try:
            rec = recorder(home, cfg) if _enabled(feats) else None
            if rec is not None:
                tr = Trace(rec, "live", _private())
                extra = {"name": getattr(live, "name", None), "spoken_name": getattr(live, "spoken_name", None),
                         "half_duplex": getattr(live, "half_duplex", None),
                         "clock_context": getattr(live, "clock_context", None),
                         "progress_after_s": getattr(live, "progress_after_s", None),
                         "max_session_s": getattr(live, "max_session_s", None)}
                snap = snapshot(config, memory=memory, registry=registry or getattr(live, "registry", None),
                                private=tr.private, extra_meta=extra)
                tr.save_start(snap)
                tr.start_state = _end_state(memory, workspace)
                live._ctx_trace = tr
                _tap(live)
            else:
                live._ctx_trace = None
        except Exception as e:
            logs.warn("context", "CONTEXT_CAPTURE_FAILED", "conversation context not captured", stage="start",
                      error_type=type(e).__name__)
        return config
    live.session_config = session_config

    run_tool = getattr(live, "_run_tool", None)
    if run_tool is not None:
        async def _run_tool(name, call_id, arguments, *rest, **kw):
            tr = getattr(live, "_ctx_trace", None)
            if tr is not None:
                tr.calls[str(call_id)] = (str(name), str(arguments or ""))
            return await run_tool(name, call_id, arguments, *rest, **kw)
        live._run_tool = _run_tool

    on_end = getattr(live, "on_end", None)

    def finish(reason) -> None:
        try:
            tr = getattr(live, "_ctx_trace", None)
            if tr is not None:
                now, before = _end_state(memory, workspace), tr.start_state
                f0, f1 = before.get("facts", {}), now.get("facts", {})
                data = {"memory_added": [_fact(r, tr.private) for i, r in f1.items() if i not in f0],
                        "memory_removed": [_fact(r, tr.private) for i, r in f0.items() if i not in f1],
                        "memory_changed": [_fact(r, tr.private) for i, r in f1.items()
                                           if i in f0 and r.get("text") != f0[i].get("text")],
                        "workspace_front_changed": before.get("front_hash") != now.get("front_hash"),
                        "workspace_backend_changed": before.get("backend_hash") != now.get("backend_hash")}
                tr.end(reason, data)
                live._ctx_trace = None
        except Exception as e:
            logs.warn("context", "CONTEXT_CAPTURE_FAILED", "end of context not captured", stage="end",
                      error_type=type(e).__name__)

    if on_end is None or inspect.iscoroutinefunction(on_end):
        async def ended(reason):
            finish(reason)
            if on_end is not None:
                await on_end(reason)
    else:                                           # keep a sync callback sync (callers may not await it)
        def ended(reason):
            finish(reason)
            return on_end(reason)
    live.on_end = ended
    return True


def attach_brain(agent, *, home: Path, cfg=None, memory=None, registry=None, feats=None) -> bool:
    """Typed chat / push-to-talk: the backend context of each request (a new trace after TYPED_GAP_S of quiet)."""
    orig = getattr(agent, "reply", None)
    hist = getattr(cfg, "history", None)
    if orig is None or not hasattr(agent, "instructions") or (hist is not None and not hist.enabled):
        return False
    state: dict = {"trace": None, "last": 0.0}

    async def reply(text, on_delta=None):        # the terminal chat streams: pass on_delta through (a TypeError broke it)
        try:
            rec = recorder(home, cfg) if _enabled(feats) else None
            if rec is not None:
                _typed_capture(agent, rec, state, memory, registry)
        except Exception as e:
            logs.warn("context", "CONTEXT_CAPTURE_FAILED", "typed context not captured", stage="typed",
                      error_type=type(e).__name__)
        return await (orig(text, on_delta=on_delta) if on_delta is not None else orig(text))
    agent.reply = reply
    return True


def _typed_capture(agent, rec: Recorder, state: dict, memory, registry) -> None:
    now, private = time.time(), _private()
    instr = str(agent.instructions() or "")
    tools = [{"type": "function", "name": t.name, "description": t.description} for t in registry.tools()] \
        if registry is not None else []
    if getattr(getattr(agent, "cfg", None), "web_search", False):
        tools.append({"type": "web_search"})
    blocks = split_instructions("backend", instr, BACKEND_MARKERS, private) + [_tools_block(tools, private)]
    tr: Trace | None = state["trace"]
    if tr is None or now - state["last"] > TYPED_GAP_S or tr.private != private:
        tr = state["trace"] = Trace(rec, "typed", private, started=now)
        meta = {"backend_model": getattr(getattr(agent, "cfg", None), "text_model", None),
                "connection": getattr(agent, "connection", None), "backend_chars": len(instr)}
        tr.save_start({"meta": meta, "blocks": blocks, "memory": memory_in_prompt(memory, "", instr, private),
                       "tools": tool_list(registry, tools) if registry is not None else [],
                       "total_tokens": sum(b["tokens"] for b in blocks)})
    else:
        for b in blocks:
            if tr.block_hashes.get(b["id"]) != b["hash"]:
                tr.block_hashes[b["id"]] = b["hash"]
                tr.event("block_changed", f"{b['label']} changed", b.get("text") or "", block=b["id"],
                         data={"new_block": b}, ts=now)
    state["last"] = now


# ---------------------------------------------------------------------- reading (dashboard)
def _rows(st, sql: str, args: tuple = ()) -> list[dict]:
    with st._lock:
        ensure_schema(st._db)
        return [dict(r) for r in st._db.execute(sql, args)]


def find_trace(st, sid: str) -> tuple[dict | None, dict | None]:
    """(session, trace) for a store session: the linked trace, else the one captured just before it started."""
    s = st.session(sid)
    if s is None:
        return None, None
    hit = _rows(st, "SELECT * FROM context_traces WHERE session_id = ? ORDER BY started LIMIT 1", (sid,))
    if not hit:
        from .store import TYPED_KINDS
        if s.get("kind") in TYPED_KINDS:
            hit = _rows(st, "SELECT * FROM context_traces WHERE kind = 'typed' AND session_id IS NULL AND started "
                            "BETWEEN ? AND ? ORDER BY started LIMIT 1",
                        (s["started"] - 5, (s.get("ended") or time.time()) + 5))
        else:
            hit = _rows(st, "SELECT * FROM context_traces WHERE kind = 'live' AND session_id IS NULL AND started "
                            "BETWEEN ? AND ? AND (core_session = ? OR ? IS NULL) ORDER BY started DESC LIMIT 1",
                        (s["started"] - LIVE_MATCH_S[0], s["started"] + LIVE_MATCH_S[1], s.get("core_session"),
                         s.get("core_session")))
        if hit:
            with st._lock:
                st._db.execute("UPDATE context_traces SET session_id = ? WHERE id = ? AND session_id IS NULL",
                               (sid, hit[0]["id"]))
                st._db.commit()
    return s, (hit[0] if hit else None)


def _load(raw, default):
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def view(st, sid: str) -> dict | None:
    """Everything the Context page shows for one conversation (None = unknown session)."""
    s, tr = find_trace(st, sid)
    if s is None:
        return None
    if tr is None:
        return {"session_id": sid, "captured": False,
                "why": "No context was captured for this conversation (it is older than this feature, the "
                       "\"Conversation context view\" switch was off, or the history was cleared)."}
    blocks = _load(tr["blocks"], [])
    events = _rows(st, "SELECT * FROM context_events WHERE trace_id = ? ORDER BY ts, id", (tr["id"],))
    for e in events:
        e["data"] = _load(e["data"], None)
    end_blocks = {b["id"]: {**b, "version": 1, "changed_at": None} for b in blocks}
    for b in end_blocks.values():
        b["start_hash"] = b["hash"]
    for e in events:
        nb = (e["data"] or {}).get("new_block") if e["kind"] == "block_changed" else None
        if e["kind"] == "workspace" and e["block"]:
            nb = {"id": e["block"], "group": "front", "label": "Workspace update note (newest version of the files)",
                  "source": "workspace.note_live", "text": e["text"], "chars": e["chars"], "tokens": e["tokens"],
                  "hash": e["hash"]}
        if nb:
            old = end_blocks.get(nb["id"], {"version": 0})
            end_blocks[nb["id"]] = {**nb, "version": old.get("version", 0) + 1, "changed_at": e["ts"],
                                    "start_hash": old.get("start_hash")}
    notes = [e for e in events if e["kind"] not in ("end", "block_changed")]
    start_tokens = tr["total_tokens"] or 0
    end = next((e for e in events if e["kind"] == "end"), None)
    return {"session_id": sid, "captured": True, "trace_id": tr["id"], "kind": tr["kind"], "started": tr["started"],
            "ended": tr["ended"], "private": bool(tr["private"]), "meta": _load(tr["meta"], {}), "blocks": blocks,
            "memory": _load(tr["memory"], []), "tools": _load(tr["tools"], []), "events": events,
            "start_tokens": start_tokens, "total_tokens": start_tokens + sum(e["tokens"] for e in notes),
            "end_blocks": list(end_blocks.values()), "diff": diff(blocks, list(end_blocks.values()), end),
            "estimate_note": "token counts are estimates from the text length (no tokenizer)"}


def diff(start: list[dict], end: list[dict], end_event: dict | None) -> dict:
    s = {b["id"]: b for b in start}
    out = []
    for b in end:
        a = s.get(b["id"])
        if a is None:
            out.append({"id": b["id"], "label": b.get("label"), "change": "added", "at": b.get("changed_at"),
                        "version": b.get("version"), "chars": [0, b.get("chars", 0)]})
        elif a["hash"] != b["hash"]:
            lines = None
            if a.get("text") is not None and b.get("text") is not None:
                lines = list(difflib.unified_diff(a["text"].splitlines(), b["text"].splitlines(), "start", "end",
                                                  lineterm="", n=1))[:200]
            out.append({"id": b["id"], "label": b.get("label"), "change": "changed", "at": b.get("changed_at"),
                        "lines": lines, "chars": [a["chars"], b["chars"]]})
    data = (end_event or {}).get("data") or {}
    return {"blocks": out, "memory_added": data.get("memory_added", []),
            "memory_removed": data.get("memory_removed", []), "memory_changed": data.get("memory_changed", []),
            "workspace_front_changed": data.get("workspace_front_changed"),
            "applies_next": "facts and files changed during the conversation reach the NEXT conversation's "
                            "instructions; this one only saw them through tool results and workspace notes"}


def export(st, sid: str) -> dict | None:
    v = view(st, sid)
    if v is None:
        return None
    text = sanitize(json.dumps(v, ensure_ascii=False, default=str))      # once more over everything, as shared
    return {"format": "jarves-context-1", "exported": time.time(), "redacted": True, **json.loads(text)}


def file_usage(st, name: str, limit: int = 30) -> list[dict]:
    """Conversations whose instructions contained this workspace file (newest first), with the version hash."""
    name = _WS_CLEAN.sub("", name if name.lower().endswith(".md") else name + ".md")
    name = name[:-3].upper() + ".md"
    out = []
    for r in _rows(st, "SELECT id, session_id, kind, started, blocks FROM context_traces ORDER BY started DESC "
                       "LIMIT 400"):
        hits = [b for b in _load(r["blocks"], []) if b.get("file") == name]
        if hits:
            out.append({"trace_id": r["id"], "session_id": r["session_id"], "kind": r["kind"],
                        "started": r["started"],
                        "used": [{"group": b["group"], "chars": b["chars"], "hash": b["hash"],
                                  "tokens": b["tokens"]} for b in hits]})
            if len(out) >= limit:
                break
    return out


def dashboard_routes(register) -> None:
    def get(ctx, m, _body):
        v = view(ctx.store, m.group(1))
        return (404, {"error": "no such conversation"}) if v is None else (200, v)

    def get_export(ctx, m, _body):
        v = export(ctx.store, m.group(1))
        return (404, {"error": "no such conversation"}) if v is None else (200, v)

    def usage(ctx, _m, _body, q):
        name = (q.get("file") or [""])[0]
        if not re.fullmatch(r"[A-Za-z]{2,20}(\.md)?", name):
            return 400, {"error": "file must be a workspace file name like SOUL.md"}
        sessions = {r["id"]: r for r in ctx.store.rows("SELECT id, started FROM sessions")}
        rows = file_usage(ctx.store, name)
        for r in rows:          # an unlinked trace: link it now if its conversation exists
            if not r["session_id"]:
                for sid, s in sessions.items():
                    if abs(s["started"] - r["started"]) <= LIVE_MATCH_S[0]:
                        _, tr = find_trace(ctx.store, sid)
                        if tr is not None and tr["id"] == r["trace_id"]:
                            r["session_id"] = sid
                            break
        return 200, {"file": name, "conversations": rows}
    register("GET", r"/api/session/([^/]+)/context", get)
    register("GET", r"/api/session/([^/]+)/context/export", get_export)
    register("GET", r"/api/context/usage", usage)
