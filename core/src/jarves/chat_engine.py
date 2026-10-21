"""Shared chat engine: the SAME text conversation on every surface (V5 web chat, owner request 21 Oct 2026).

`jarvis web` (the dashboard Chat page on this computer) and the paired phones / PCs (the /m/ page over the V5 mobile
path) are clients of one `WebChat`: it runs the turns through the Core's own Brain (`Core.respond_remote`, or the
standalone text session of `jarvis web` when no Core runs), keeps ONE transcript every client can read, and decides
the things that must never depend on a screen:

- slash commands (/new /resume /undo /status /help): parsed and executed here, never sent to the model;
- tool calls: one short line per call (name + ok/failed + code) with an expandable result that is redacted
  (credentials masked, e-mail addresses hidden) before it leaves this module;
- approvals: shown inline with the popup's plain description (approval_popup.headline / sentence / detail_rows);
  the FIRST answer wins across popup, terminal, dashboard and phone (approvals.ApprovalService does that); typed-code
  approvals are never answered from a phone (mobile.answer_approval) - the page says "answer on the computer";
- silence: quiet hours (the alerts window) and the owner's `silent` switch forbid speech, the face dialog and the
  popup's bell for the turn (approvals.CHAT_TURN). Speech is opt-in per message, only from the owner's own dashboard,
  and only when nothing forbids it; a phone never makes the computer speak;
- history: the exchange is stored exactly like a typed message of the face (Core REPLY record) or, without a Core,
  straight into store.db, so `jarvis chat`, the web page and the Windows voice session see the same conversations.

Streaming uses cursor long-polling over plain JSON (no WebSocket, no SSE): every client asks "events after N" and the
server answers as soon as there is one. A streamed answer is ONE replaceable event carrying the text so far, so the
log stays small however long the answer is. Nothing here logs message text.

Interface used by both web layers (dashboard.py routes below, mobile.py device routes):
    chat = WebChat(core, loop)             # core: respond_remote / agent / approvals / registry / workspace / memory
    status, obj = handle_send(chat, body, client="owner", device=False)
    status, obj = handle_events(chat, query, approvals_fn)
    status, obj = handle_state(chat, query, device=False)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import approvals as appr
from . import features as feat
from . import logs
from .skills import TOOL_SINK

FEATURE = "web_chat"
MAX_TEXT = 4000
RESUME_LIMIT = 8
RESUME_MAX_TURNS = 60
EVENTS_KEEP = 400
MESSAGES_KEEP = 300
REPLAY_TTL_S = 600
REPLAY_KEEP = 200
LONGPOLL_MAX_S = 25.0
POLL_STEP_S = 0.4
DETAIL_CHARS = 600
QUIET_CACHE_S = 20.0
MSG_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Fixed refusal / error codes of the chat API (the pages translate them; they never change):
CODES = ("CHAT_UNAVAILABLE", "CHAT_FEATURE_OFF", "CHAT_BUSY", "CHAT_EMPTY", "CHAT_TOO_LONG", "CHAT_BAD_ID",
         "CHAT_STOPPING", "CHAT_FAILED")

COMMANDS = (
    ("/new", "start a new conversation", "שיחה חדשה"),
    ("/resume", "list recent conversations, or continue one: /resume 2", "רשימת שיחות אחרונות, או המשך שיחה: /resume 2"),
    ("/undo", "undo the last change to a persona/workspace file (asks first)", "ביטול השינוי האחרון בקובץ אישיות או סביבת עבודה (שואל קודם)"),
    ("/status", "what is running and whether this chat is silent", "מה פועל והאם השיחה שקטה"),
    ("/help", "this list", "הרשימה הזאת"),
)
_NAMES = {c[0][1:] for c in COMMANDS}

feat.register(FEATURE, "שיחה בלוח הבקרה", "Web chat (dashboard Chat page)", True, needs=["dashboard"],
              about="the Chat page of the dashboard and `jarvis web`: the same silent text conversation as `jarvis "
                    "chat`, with streaming answers, slash commands, inline approvals and one shared history; paired "
                    "phones use it too when phone access is on",
              targets={"windows": {"status": "implemented_unverified",
                                   "evidence": "UNIT test_web_chat.py (fake Core and model, real dashboard and phone HTTP, "
                                               "first-answer-wins approvals, forbidden pages refused server-side, Hebrew round trip)",
                                   "blocker": "never opened in a real browser on the owner's PC or on a real phone"},
                       "linux_arm64_pi": {"status": "implemented_unverified",
                                          "blocker": "pure Python and plain JavaScript; not run on a Raspberry Pi yet (no device in the test setup)"}},
              permissions=["read", "action"],
              settings="dashboard Chat page, `jarvis web`, dashboard Phone page (pages per phone)")


# ---------------------------------------------------------------------- pure helpers
def parse_command(text: str) -> tuple[str, str] | None:
    """('new', '') for '/new'; ('unknown', 'foo') for '/foo'; None when the text is not a slash command.
    A text that merely starts with a slash but has no letter right after it ('/ 5', '//path') is a message."""
    t = str(text or "").strip()
    m = re.match(r"^/([A-Za-z][A-Za-z_-]*)\b\s*(.*)$", t, re.S)
    if not m:
        return None
    name = m.group(1).lower()
    return (name, m.group(2).strip()) if name in _NAMES else ("unknown", name)


def scrub(text, limit: int = DETAIL_CHARS) -> str:
    """Anything shown as a tool result: credentials masked (logs.redact), e-mail addresses hidden, control characters
    removed, cut at `limit`. Hebrew and other scripts are untouched."""
    s = _CTRL.sub("", str(text or ""))
    s = _EMAIL.sub("<email>", logs.redact(s)).strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def help_text(lang: str) -> str:
    he = lang == "he"
    return "\n".join(f"{c[0]}  {c[2] if he else c[1]}" for c in COMMANDS)


def quiet_now(home: Path, clock=time.time) -> bool:
    """Quiet hours of the alerts engine (default 22:00-07:00, owner-editable): the same window every other surface uses."""
    from datetime import datetime
    from .alerts import in_quiet
    from .scheduler import zone
    from .userdata import open_db
    db = open_db(Path(home))
    try:
        st = db.settings("alerts.")
        if not st["alerts.quiet_enabled"]:
            return False
        return in_quiet(datetime.fromtimestamp(clock(), zone(db.setting("scheduler.timezone"))),
                        st["alerts.quiet_start"], st["alerts.quiet_end"])
    finally:
        db.close()


def recent_sessions(home: Path, limit: int = RESUME_LIMIT) -> list[dict]:
    """Recent conversations of every surface (typed, `jarvis chat`, web, phone, voice) from store.db, newest first."""
    p = Path(home) / "store.db"
    if not p.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT s.id, s.kind, s.started FROM sessions s WHERE EXISTS (SELECT 1 FROM turns t WHERE "
                           "t.session_id = s.id AND t.role = 'user') ORDER BY s.started DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            first = con.execute("SELECT text FROM turns WHERE session_id = ? AND role = 'user' ORDER BY ts, id LIMIT 1",
                                (r["id"],)).fetchone()
            n = con.execute("SELECT COUNT(*) FROM turns WHERE session_id = ? AND role = 'user'", (r["id"],)).fetchone()[0]
            out.append({"id": r["id"], "kind": r["kind"], "started": r["started"], "turns": n,
                        "first": scrub(first["text"], 70) if first and first["text"] is not None else None})
        return out
    except sqlite3.Error:
        logs.warn("core", "CHAT_STORE_UNREADABLE", "conversation store could not be read")
        return []
    finally:
        con.close()


def session_turns(home: Path, sid: str) -> list[tuple[str, str | None]]:
    p = Path(home) / "store.db"
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2)
    try:
        return [(r[0], r[1]) for r in con.execute(
            "SELECT role, text FROM turns WHERE session_id = ? ORDER BY ts, id", (sid,)).fetchall()]
    finally:
        con.close()


def describe_approval(a: dict, lang: str = "en", *, reveal_code: bool = False) -> dict:
    """The popup's plain description of one waiting approval (approval_popup's own helpers): a headline ('Send a
    message?'), ONE plain sentence, the detail rows and the risk. `a` is an Approval.public() / pending-file row."""
    from . import approval_popup as pop
    lang = pop.pick_lang({"lang": lang})
    rows = [{"label": scrub(lbl, 80), "value": scrub(val, 1200), "mono": style == "mono"}
            for lbl, val, style in pop.detail_rows(a, lang)]
    return {"id": str(a.get("id") or ""), "headline": pop.headline(a, lang), "sentence": scrub(pop.sentence(a), 200),
            "rows": rows, "risk": a.get("risk") if a.get("risk") in ("low", "medium", "high") else "high",
            "kind": str(a.get("kind") or "other"), "typed": bool(a.get("typed")),
            "code": str(a.get("typed_code") or "") if reveal_code and a.get("typed") else "",
            "seconds_left": max(0, int(a.get("seconds_left") or 0)), "shown": list(a.get("shown") or [])}


def owner_approvals(home: Path, lang: str = "en") -> list[dict]:
    """What waits for the owner on THIS computer (in-process service, else the pending file of the running Core)."""
    svc = appr.current()
    if svc is not None and svc.home.resolve() == Path(home).resolve():
        rows = svc.pending()
    else:
        rows = appr.read_pending_file(home)
        now = time.time()
        for r in rows:
            r["seconds_left"] = max(0, round(float(r.get("expires") or 0) - now))
    return [describe_approval(r, lang, reveal_code=True) for r in rows]


def device_approvals(home: Path, lang: str = "en") -> list[dict]:
    """The same description for a paired phone: no typed code is ever sent there (it is answered on the computer)."""
    now = time.time()
    rows = appr.read_pending_file(home, reveal=False)
    for r in rows:
        r["seconds_left"] = max(0, round(float(r.get("expires") or 0) - now))
    return [describe_approval(r, lang) for r in rows]


class EventLog:
    """Bounded, thread-safe event list with integer ids (the cursor) and a condition for long-polling."""

    def __init__(self, keep: int = EVENTS_KEEP) -> None:
        self.keep = keep
        self._cv = threading.Condition()
        self._ev: list[dict] = []
        self._n = 0

    @property
    def last(self) -> int:
        with self._cv:
            return self._n

    def add(self, type_: str, *, replace: tuple | None = None, **fields) -> int:
        """Append an event. `replace=(type, turn)` first removes the newest event of that type and turn (a streamed
        answer is one event that grows, so a slow client never misses text and the list stays small)."""
        with self._cv:
            if replace is not None:
                self._ev = [e for e in self._ev if not (e["type"] == replace[0] and e.get("turn") == replace[1])]
            self._n += 1
            self._ev.append({"id": self._n, "type": type_, "ts": round(time.time(), 3), **fields})
            if len(self._ev) > self.keep:
                del self._ev[: len(self._ev) - self.keep]
            self._cv.notify_all()
            return self._n

    def since(self, after: int) -> list[dict]:
        with self._cv:
            return [dict(e) for e in self._ev if e["id"] > after]

    def oldest(self) -> int:
        with self._cv:
            return self._ev[0]["id"] if self._ev else self._n + 1

    def wait(self, after: int, timeout: float) -> None:
        with self._cv:
            if self._n <= after:
                self._cv.wait(timeout)


class _ToolSink:
    """TOOL_SINK of one turn: a short line per tool call plus a redacted, expandable result."""
    wants_detail = True

    def __init__(self, chat: "WebChat", turn: str) -> None:
        self.chat, self.turn = chat, turn

    def __call__(self, name: str, ok: bool, code: str, message: str = "") -> None:
        self.chat._tool(self.turn, str(name)[:60], bool(ok), str(code or "")[:40], message)


class WebChat:
    """One shared conversation for every web client. Thread-safe: the HTTP threads call submit / state / poll, the turns
    run on `loop` (the Core's loop, or the standalone session's own)."""

    def __init__(self, core, loop: asyncio.AbstractEventLoop, *, clock=time.time) -> None:
        self.core, self.loop, self.clock = core, loop, clock
        self.home = Path(core.home)
        self.events = EventLog()
        self._lock = threading.RLock()
        self.messages: list[dict] = []
        self._busy = ""
        self._fresh = False
        self._listed: list[str] = []
        self._replays: dict[tuple[str, str], tuple[float, dict]] = {}
        self._turn_n = 0
        self._future: concurrent.futures.Future | None = None
        self._quiet: tuple[float, bool] = (0.0, False)
        self.closed = False
        svc = getattr(core, "approvals", None)
        self._svc = svc
        if svc is not None:
            svc.on_ask.append(self._asked)
            svc.listeners.append(self._decided)

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self.closed = True
        svc = self._svc
        if svc is not None:
            for lst, fn in ((svc.on_ask, self._asked), (svc.listeners, self._decided)):
                if fn in lst:
                    lst.remove(fn)
        fut = self._future
        if fut is not None and not fut.done():
            fut.cancel()

    def _asked(self, a) -> None:
        self.events.add("approval", id=a.id)

    def _decided(self, a) -> None:
        self.events.add("approval_closed", id=a.id, decision=a.state, channel=a.channel)

    # ------------------------------------------------------------------ silence
    def setting_silent(self) -> bool:
        try:
            return (self.core.memory.get_setting("chat:silent") or "") == "on"
        except Exception:
            return False

    def set_silent(self, on: bool) -> None:
        self.core.memory.set_setting("chat:silent", "on" if on else "off")
        self.events.add("state")

    def quiet(self) -> bool:
        now = self.clock()
        if now - self._quiet[0] > QUIET_CACHE_S:
            try:
                self._quiet = (now, quiet_now(self.home, self.clock))
            except Exception:
                self._quiet = (now, False)
        return self._quiet[1]

    def silent_state(self) -> dict:
        s, q = self.setting_silent(), self.quiet()
        return {"on": s or q, "setting": s, "quiet_hours": q}

    def can_speak(self) -> bool:
        return callable(getattr(self.core, "speak", None)) and getattr(self.core, "live", None) is None

    # ------------------------------------------------------------------ views
    def state(self, *, device: bool = False) -> dict:
        with self._lock:
            msgs = [dict(m) for m in self.messages]
            busy = self._busy
        return {"available": True, "cursor": self.events.last, "busy": busy, "messages": msgs,
                "silent": self.silent_state(), "can_speak": (not device) and self.can_speak(),
                "commands": [{"name": c[0], "en": c[1], "he": c[2]} for c in COMMANDS]}

    def poll(self, after: int, wait_s: float, sig: str, approvals) -> dict:
        """Events after `after`; waits up to wait_s for one (or for the set of waiting approvals to differ from `sig`)."""
        end = time.monotonic() + max(0.0, min(float(wait_s), LONGPOLL_MAX_S))
        while True:
            last = self.events.last
            if after > last or after < self.events.oldest() - 1 and last > 0 and after != 0:
                evs, resync = [], True               # the server restarted or the client fell far behind
            else:
                evs, resync = self.events.since(after), False
            view = approvals()
            cur = ",".join(a["id"] for a in view)
            if evs or resync or cur != sig or time.monotonic() >= end or self.closed:
                break
            self.events.wait(after, min(POLL_STEP_S, max(0.01, end - time.monotonic())))
        return {"events": evs, "cursor": (self.events.last if resync else (evs[-1]["id"] if evs else after)),
                "resync": resync, "approvals": view, "sig": cur, "busy": self._busy,
                "silent": self.silent_state()}

    # ------------------------------------------------------------------ submitting
    def submit(self, text: str, *, client: str = "owner", mid: str | None = None, speak: bool = False,
               device: bool = False, lang: str = "en") -> tuple[int, dict]:
        text = str(text or "").strip()
        if not text:
            return 400, {"error": "text is empty", "code": "CHAT_EMPTY"}
        if len(text) > MAX_TEXT:
            return 413, {"error": "message too long", "code": "CHAT_TOO_LONG"}
        if mid is not None and not (isinstance(mid, str) and MSG_ID_RE.match(mid)):
            return 400, {"error": "message id must be 8-64 letters, digits or _ . : -", "code": "CHAT_BAD_ID"}
        if self.closed or getattr(self.core, "stopping", False):
            return 503, {"error": "shutting down", "code": "CHAT_STOPPING"}
        cmd = parse_command(text)
        with self._lock:
            if mid:
                hit = self._replays.get((client, mid))
                if hit and self.clock() - hit[0] <= REPLAY_TTL_S:
                    return 200, {**hit[1], "replay": True}        # a resend: the stored answer, never a second run
            if self._busy and (cmd is None or cmd[0] not in ("status", "help", "unknown")):
                return 409, {"error": "the previous message is still being answered", "code": "CHAT_BUSY"}
            self._turn_n += 1
            tid = f"t{self._turn_n}"
            if cmd is None or cmd[0] in ("new", "resume", "undo"):
                self._busy = tid                  # /undo may wait minutes for an approval: no other turn meanwhile
            self._add_message({"role": "user", "text": text, "turn": tid, "client": "device" if device else "owner"})
            self.events.add("user", turn=tid, text=text, client="device" if device else "owner")
            out = {"ok": True, "turn": tid, "cursor": self.events.last, "command": cmd[0] if cmd else ""}
            if mid:
                self._replays[(client, mid)] = (self.clock(), out)
                for k in [k for k, v in self._replays.items() if self.clock() - v[0] > REPLAY_TTL_S]:
                    self._replays.pop(k, None)
                while len(self._replays) > REPLAY_KEEP:
                    self._replays.pop(next(iter(self._replays)))
        coro = self._command(tid, cmd[0], cmd[1], lang) if cmd else self._run(
            tid, text, bool(speak) and not device, device)
        try:
            self._future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError:
            coro.close()
            self._release(tid)
            return 503, {"error": "the chat engine is not running", "code": "CHAT_STOPPING"}
        return 200, out

    def _release(self, tid: str) -> None:
        with self._lock:
            if self._busy == tid:
                self._busy = ""
        self.events.add("state")

    def _add_message(self, m: dict) -> None:
        with self._lock:
            self.messages.append(m)
            if len(self.messages) > MESSAGES_KEEP:
                del self.messages[: len(self.messages) - MESSAGES_KEEP]

    # ------------------------------------------------------------------ one model turn (runs on the loop)
    def _delta_to(self, tid: str):
        buf: list[str] = []

        def on_delta(piece: str) -> None:
            if piece:
                buf.append(piece)
                self.events.add("delta", replace=("delta", tid), turn=tid, text="".join(buf))
        return on_delta, buf

    def _tool(self, tid: str, name: str, ok: bool, code: str, message: str) -> None:
        line = {"role": "tool", "turn": tid, "name": name, "ok": ok, "code": code,
                "detail": scrub(message, DETAIL_CHARS)}
        self._add_message(line)
        self.events.add("tool", **line)

    async def _run(self, tid: str, text: str, speak: bool, device: bool) -> None:
        silent = self.silent_state()["on"]
        appr.CHAT_TURN.set({"client": "web", "silent": silent})
        TOOL_SINK.set(_ToolSink(self, tid))
        on_delta, buf = self._delta_to(tid)
        fresh, self._fresh = self._fresh, False
        t0 = time.monotonic()
        try:
            reply = await self.core.respond_remote(text, on_delta=on_delta, fresh=fresh)
        except asyncio.CancelledError:
            self._finish(tid, "", False, "CANCELLED", silent, False)
            raise
        except Exception as e:
            logs.error("core", "CHAT_TURN_FAILED", "web chat turn failed", exc=True, error_type=type(e).__name__)
            self._finish(tid, "Something went wrong; see the log.", False, "CHAT_FAILED", silent, False)
            return
        spoke = False
        if speak and not silent and self.can_speak() and reply.ok and reply.text:
            spoke = True
        self._finish(tid, reply.text, bool(reply.ok), str(reply.code or ""), silent, spoke,
                     ms=round((time.monotonic() - t0) * 1000), source=getattr(reply, "source", ""))
        if spoke:
            try:
                await self.core.speak(reply.text)
            except Exception:
                logs.warn("core", "CHAT_SPEAK_FAILED", "the reply could not be spoken")

    def _finish(self, tid: str, text: str, ok: bool, code: str, silent: bool, spoke: bool, **extra) -> None:
        if text:
            self._add_message({"role": "assistant", "text": text, "turn": tid, "ok": ok, "code": code})
        self.events.add("reply", replace=("delta", tid), turn=tid, text=text, ok=ok, code=code, silent=silent,
                        spoken=spoke, **{k: v for k, v in extra.items() if k == "source"})
        logs.info("core", "CHAT_TURN_DONE", "web chat turn finished", ok=ok, result_code=code,
                  ms=extra.get("ms", 0), count=len(text))
        self._release(tid)

    # ------------------------------------------------------------------ slash commands (run on the loop)
    def _notice(self, tid: str, command: str, en: str, he: str, **data) -> None:
        self._add_message({"role": "notice", "turn": tid, "command": command, "en": en, "he": he})
        self.events.add("notice", turn=tid, command=command, en=en, he=he, **data)

    async def _command(self, tid: str, name: str, arg: str, lang: str) -> None:
        try:
            if name == "help":
                self._notice(tid, "help", help_text("en"), help_text("he"))
            elif name == "unknown":
                self._notice(tid, "unknown", f"Unknown command /{arg}. Try /help.", f"פקודה לא מוכרת: /{arg}. נסו /help.")
            elif name == "status":
                self._status(tid)
            elif name == "new":
                self._new(tid)
            elif name == "resume":
                await self._resume(tid, arg)
            elif name == "undo":
                await self._undo(tid)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logs.error("core", "CHAT_COMMAND_FAILED", "web chat command failed", exc=True, error_type=type(e).__name__)
            self._notice(tid, name, "That command failed; see the log.", "הפקודה נכשלה; ראו את היומן.")
        finally:
            self._release(tid)

    def _status(self, tid: str) -> None:
        try:
            snap = self.core.snapshot()
        except Exception:
            snap = {}
        comps = snap.get("components") or {}
        bad = sorted(k for k, v in comps.items() if str((v or {}).get("status")) not in ("ok", "disabled", "idle"))
        sil = self.silent_state()
        pending = len(owner_approvals(self.home))
        why_en = " (quiet hours)" if sil["quiet_hours"] else " (switch)" if sil["setting"] else ""
        why_he = " (שעות שקט)" if sil["quiet_hours"] else " (מתג)" if sil["setting"] else ""
        core = str(snap.get("core") or "running")
        en = (f"Core: {core}. Needs attention: {', '.join(bad) if bad else 'nothing'}. "
              f"Silent: {'yes' if sil['on'] else 'no'}{why_en}. Waiting for approval: {pending}.")
        he = (f"ליבה: {core}. דורש תשומת לב: {', '.join(bad) if bad else 'שום דבר'}. "
              f"שקט: {'כן' if sil['on'] else 'לא'}{why_he}. ממתינים לאישור: {pending}.")
        self._notice(tid, "status", en, he, core=core, attention=bad, silent=sil, pending=pending)

    def _new(self, tid: str) -> None:
        agent = getattr(self.core, "agent", None)
        if agent is not None and hasattr(agent, "reset_conversation"):
            agent.reset_conversation()
        self._fresh = True
        logs.info("core", "CHAT_NEW", "new text conversation started")
        with self._lock:
            self.messages.clear()
        self.events.add("reset")
        self._notice(tid, "new", "New conversation started.", "התחילה שיחה חדשה.")

    async def _resume(self, tid: str, arg: str) -> None:
        if not arg:
            sess = recent_sessions(self.home)
            with self._lock:
                self._listed = [s["id"] for s in sess]
            if not sess:
                return self._notice(tid, "resume", "No earlier conversations.", "אין שיחות קודמות.", sessions=[])
            lines_en = [f"{i}. {s['first'] or '(private)'} ({s['turns']})" for i, s in enumerate(sess, 1)]
            lines_he = [f"{i}. {s['first'] or '(פרטי)'} ({s['turns']})" for i, s in enumerate(sess, 1)]
            return self._notice(tid, "resume", "Recent conversations (type /resume 2 to continue one):\n" +
                                "\n".join(lines_en), "שיחות אחרונות (כתבו /resume 2 כדי להמשיך אחת מהן):\n" +
                                "\n".join(lines_he), sessions=[{"n": i, **s} for i, s in enumerate(sess, 1)])
        sid = arg
        if arg.isdigit():
            with self._lock:
                listed = list(self._listed) or [s["id"] for s in recent_sessions(self.home)]
            n = int(arg)
            if not 1 <= n <= len(listed):
                return self._notice(tid, "resume", "No such conversation number. Type /resume to list them.",
                                    "אין שיחה במספר הזה. כתבו /resume לרשימה.")
            sid = listed[n - 1]
        try:
            rows = session_turns(self.home, sid)
        except sqlite3.Error:
            rows = []
        agent = getattr(self.core, "agent", None)
        if not rows:
            return self._notice(tid, "resume", "I could not find that conversation.", "לא מצאתי את השיחה הזאת.")
        if any(t is None for _r, t in rows):
            return self._notice(tid, "resume", "That conversation is private: its text was not kept.",
                                "השיחה הזאת פרטית: הטקסט שלה לא נשמר.")
        if agent is None or not hasattr(agent, "seed_history"):
            return self._notice(tid, "resume", "The AI is off, so there is nothing to continue.",
                                "ה-AI כבוי, אז אין מה להמשיך.")
        rows = rows[-RESUME_MAX_TURNS:]
        await agent.seed_history(rows)
        self._fresh = False
        logs.info("core", "CHAT_RESUMED", "earlier conversation loaded into the web chat", turns=len(rows))
        with self._lock:
            self.messages[:] = [{"role": r, "text": scrub(t, MAX_TEXT), "turn": "", "resumed": True}
                                for r, t in rows if r in ("user", "assistant")]
        self.events.add("reset")
        self._notice(tid, "resume", f"Continuing that conversation ({len(rows)} messages).",
                     f"ממשיכים את השיחה הזאת ({len(rows)} הודעות).")

    async def _undo(self, tid: str) -> None:
        ws = getattr(self.core, "workspace", None)
        best = None
        if ws is not None:
            for f in ws.files():
                if f.get("generated") or not f.get("versions"):
                    continue
                saved = ws.history(f["name"])[0]["saved"]
                if best is None or saved > best[0]:
                    best = (saved, f["name"])
        if best is None:
            return self._notice(tid, "undo", "Nothing to undo: no persona or workspace file has an earlier version.",
                                "אין מה לבטל: לאף קובץ אישיות או סביבת עבודה אין גרסה קודמת.")
        name = best[1]
        appr.CHAT_TURN.set({"client": "web", "silent": self.silent_state()["on"]})
        res = await self.core.registry.call("workspace_undo", {"file": name}, origin="user")
        if res.ok:
            self._notice(tid, "undo", f"Undid the last change to {name}.", f"בוטל השינוי האחרון ב-{name}.", file=name, code=res.code)
        else:
            self._notice(tid, "undo", f"{name} was not changed ({res.code}).", f"{name} לא שונה ({res.code}).", file=name, code=res.code)


# ---------------------------------------------------------------------- shared HTTP-level handlers
def _lang(q: dict, default: str = "en") -> str:
    v = q.get("lang")
    v = v[0] if isinstance(v, list) and v else v
    return "he" if str(v or default).lower().startswith("he") else "en"


def _int(q: dict, key: str, default: int = 0) -> int:
    v = q.get(key)
    v = v[0] if isinstance(v, list) and v else v
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def _str(q: dict, key: str) -> str:
    v = q.get(key)
    v = v[0] if isinstance(v, list) and v else v
    return str(v or "")


def handle_state(chat: "WebChat | None", q: dict, *, device: bool, home: Path) -> tuple[int, dict]:
    if chat is None:
        return 200, {"available": False, "code": "CHAT_UNAVAILABLE",
                     "hint": "run `jarvis web` (or start JARVES) to chat here"}
    lang = _lang(q)
    out = chat.state(device=device)
    out["approvals"] = (device_approvals if device else owner_approvals)(home, lang)
    return 200, out


def handle_send(chat: "WebChat | None", body: dict, *, client: str, device: bool) -> tuple[int, dict]:
    if chat is None:
        return 503, {"error": "chat is not running here: start `jarvis web` or JARVES", "code": "CHAT_UNAVAILABLE"}
    body = body or {}
    lang = "he" if str(body.get("lang") or "").lower().startswith("he") else "en"
    return chat.submit(str(body.get("text", "")), client=client, mid=body.get("id"), speak=body.get("speak") is True,
                       device=device, lang=lang)


def handle_events(chat: "WebChat | None", q: dict, *, device: bool, home: Path) -> tuple[int, dict]:
    if chat is None:
        return 503, {"error": "chat is not running here", "code": "CHAT_UNAVAILABLE"}
    lang = _lang(q)
    fn = device_approvals if device else owner_approvals
    return 200, chat.poll(_int(q, "after"), float(_int(q, "wait", 0)), _str(q, "sig"), lambda: fn(home, lang))


# ---------------------------------------------------------------------- owner dashboard routes
def _chat(ctx) -> "WebChat | None":
    return getattr(ctx, "chat", None)


def _gate(ctx) -> tuple[int, dict] | None:
    if not feat._ctx_features(ctx).enabled(FEATURE):
        return 403, {"error": "web chat is switched off: jarvis features on web_chat", "code": "CHAT_FEATURE_OFF"}
    return None


def api_state(ctx, m, body, q=None):
    return _gate(ctx) or handle_state(_chat(ctx), q or {}, device=False, home=ctx.home)


def api_send(ctx, m, body):
    return _gate(ctx) or handle_send(_chat(ctx), body, client="owner", device=False)


def api_events(ctx, m, body, q=None):
    return _gate(ctx) or handle_events(_chat(ctx), q or {}, device=False, home=ctx.home)


def api_silent(ctx, m, body):
    chat = _chat(ctx)
    if chat is None:
        return 503, {"error": "chat is not running here", "code": "CHAT_UNAVAILABLE"}
    if not isinstance((body or {}).get("on"), bool):
        return 400, {"error": "body needs {\"on\": true|false}"}
    chat.set_silent(body["on"])
    return 200, {"ok": True, "silent": chat.silent_state()}


def dashboard_routes(register) -> None:
    register("GET", r"/api/chat/state", api_state)
    register("POST", r"/api/chat/send", api_send)
    register("GET", r"/api/chat/events", api_events)
    register("POST", r"/api/chat/silent", api_silent)
