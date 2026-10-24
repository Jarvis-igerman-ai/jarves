"""Terminal text chat, Core side (`jarvis chat` / `jarvis text`; owner request 21 Oct 2026).

The REPL (chat_cli.py) is a client of the RUNNING Core on the local link (role "cli"). It sends text turns; the Core
runs them through the same Brain, Skills, approvals, budget and history as every typed request, but NOTHING in a
chat turn speaks, plays a chime or moves the face (unless the owner turned `/voice on` in a quiet-free session).

Messages (cli -> Core):  chat.hello  chat.turn {id, text, voice, silent}  chat.cancel  chat.command {name, ...}
                         chat.approval_answer {id, approved, typed}
Messages (Core -> cli):  chat.state  chat.delta {id, text}  chat.tool {id, name, ok, code}  chat.done {id, ...}
                         chat.approval_request {...}  chat.approval_closed {id, decision, channel}
                         chat.approval_result {id, ok, why}  chat.info {command, ...}  chat.error {code, message}

Rules enforced HERE, not by the terminal:
- Silent hours: when quiet hours are active (alerts engine, default 22:00-07:00) or the client asks for silence, the
  turn is silent: no speech, no face change, no voice approval, no face dialog. The wake word listener is untouched.
- A chat turn never uses the voice approval channel (spoken approvals do not apply to text); popup / tray / dashboard
  keep working and the terminal is one more place to answer. The first answer wins and the others close.
- Tool-call lines carry the tool name and ok/fail only (never arguments or results): nothing to leak.
- Stored exactly like a typed message of the dashboard/face: the same core REPLY record.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from . import approvals as appr
from . import features as feat
from . import logs
from .skills import ROUTE, TOOL_SINK

CHAT_TYPES = ("chat.hello", "chat.turn", "chat.cancel", "chat.command", "chat.approval_answer")
MAX_TEXT = 8000                       # a pasted multi-line message; the face limit (1000) is for the small input box
RESUME_LIMIT = 8
RESUME_MAX_TURNS = 60

feat.register("text_chat", "שיחת טקסט בטרמינל", "Terminal text chat", True,
              about="silent text chat with JARVES in the terminal (`jarvis chat`): no microphone, no speech, "
                    "approvals answered in the terminal",
              targets={"windows": {"status": "implemented_unverified",
                                   "evidence": "core/tests/test_chat.py (scripted terminal, fake Core and model); "
                                               "no hands-on run in a real Windows console yet"},
                       "linux_arm64_pi": {"status": "implemented_unverified",
                                          "blocker": "pure Python over the local link; not run on a Raspberry Pi "
                                                     "yet (no device in the test setup)",
                                          "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/3"}})


class TerminalApprovalChannel:
    """approvals channel: shows the request in the terminal that started the chat turn which is asking."""
    name = "terminal"

    def __init__(self, service: "ChatService") -> None:
        self.service = service

    def available(self, a) -> bool:
        turn = appr.CHAT_TURN.get()
        return turn is not None and turn.get("client") in self.service.clients

    async def open(self, a) -> None:
        turn = appr.CHAT_TURN.get() or {}
        client = self.service.clients.get(turn.get("client"))
        if client is not None:
            self.service.approval_client[a.id] = client
            await self.service.send(client, "chat.approval_request", self.service.describe(a))

    async def close(self, a) -> None:
        client = self.service.approval_client.pop(a.id, None)
        if client is not None:
            await self.service.send(client, "chat.approval_closed",
                                    {"id": a.id, "decision": a.state, "channel": a.channel})


class ChatService:
    def __init__(self, core) -> None:
        self.core = core
        self.clients: dict[str, object] = {}                  # client id -> link Client with a chat session
        self.turns: dict[str, asyncio.Task] = {}              # client id -> its running turn
        self.approval_client: dict[str, object] = {}
        self.channel = TerminalApprovalChannel(self)

    # ------------------------------------------------------------------ plumbing
    async def send(self, client, t: str, payload: dict) -> bool:
        link = getattr(self.core, "link", None)
        return bool(link is not None and await link.reply(client, t, payload))

    def quiet_now(self) -> bool:
        ops = getattr(self.core, "ops", None)
        try:
            return bool(ops is not None and ops.alerts.quiet_now())
        except Exception:
            return False

    def quiet_window(self) -> dict:
        ops = getattr(self.core, "ops", None)
        try:
            st = ops.alerts.db.settings("alerts.")
            return {"enabled": bool(st["alerts.quiet_enabled"]), "start": st["alerts.quiet_start"],
                    "end": st["alerts.quiet_end"]}
        except Exception:
            return {"enabled": False, "start": "22:00", "end": "07:00"}

    def state(self, client) -> dict:
        c = self.core
        snap = c.snapshot()
        comps = {k: {"status": v.get("status"), "detail": str(v.get("detail") or "")[:160]}
                 for k, v in (snap.get("components") or {}).items()}
        agent = getattr(c, "agent", None)
        try:
            ai = agent.describe() if agent is not None and hasattr(agent, "describe") else \
                ("offline: built-in commands only" if agent is None else "AI on")
        except Exception:
            ai = "AI on"
        return {"core": snap.get("core"), "version": snap.get("version"), "uptime_s": snap.get("uptime_s"),
                "components": comps, "ai": ai, "text_model": getattr(c.cfg.ai, "text_model", ""),
                "connection": getattr(c.cfg.ai, "connection", ""), "billing": snap.get("billing"),
                "voice_mode": getattr(c, "voice_mode", "off"), "ai_requests_today": snap.get("ai_requests_today"),
                "quiet": self.quiet_now(), "quiet_window": self.quiet_window(),
                "can_speak": bool(getattr(c, "dev", None) is not None and c.dev.audio is not None),
                "pending_approvals": len(c.approvals.pending()) if getattr(c, "approvals", None) else 0,
                "profile": (snap.get("profile") or {}).get("name"), "client": client.id}

    def describe(self, a) -> dict:
        """The approval as the terminal shows it: the popup's plain sentence plus details; recipients by first name
        only; credentials masked."""
        details = {}
        raw_to = str((a.details or {}).get("recipient") or "").strip()
        first = appr.first_name(raw_to) if raw_to else ""

        def clean(v) -> str:
            v = v if isinstance(v, str) else str(v)
            if raw_to:                                   # the whole label (name + address) shrinks to the first name
                v = v.replace(raw_to, first or "the recipient")
            return logs.redact(v)
        for k, v in (a.details or {}).items():
            if k == "recipient":
                if not first:
                    continue
                v = first
            details[str(k)] = clean(v)[:600]
        lang = getattr(getattr(getattr(self.core, "cfg", None), "ui", None), "popup_language", "en")
        return {"id": a.id, "title": clean(a.title), "prompt": clean(a.prompt), "why": clean(a.why),
                "risk": a.risk, "kind": a.kind, "details": details, "typed": bool(a.typed_code),
                "code": a.typed_code, "seconds_left": max(0, round(a.expires - time.time())), "lang": lang,
                "shown": list(a.shown)}

    # ------------------------------------------------------------------ dispatch
    async def handle(self, t: str, p: dict, client) -> None:
        if t == "chat.hello":
            self.clients[client.id] = client
            await self.send(client, "chat.state", self.state(client))
        elif t == "chat.turn":
            await self._start_turn(client, p)
        elif t == "chat.cancel":
            task = self.turns.get(client.id)
            if task is not None and not task.done():
                task.cancel()
        elif t == "chat.approval_answer":
            aid = str(p.get("id", ""))
            ok, why = (False, "no approval service")
            if getattr(self.core, "approvals", None) is not None:
                ok, why = self.core.approvals.answer(aid, p.get("approved") is True, channel="terminal",
                                                     typed=str(p.get("typed") or "")[:12])
            await self.send(client, "chat.approval_result", {"id": aid, "ok": ok, "why": why})
        elif t == "chat.command":
            await self._command(client, p)

    def forget(self, client) -> None:
        """The terminal went away: its turn ends (a waiting approval is denied) and it is no longer asked."""
        self.clients.pop(client.id, None)
        task = self.turns.pop(client.id, None)
        if task is not None and not task.done():
            task.cancel()

    async def close(self) -> None:
        tasks = [t for t in self.turns.values() if not t.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.turns.clear()

    # ------------------------------------------------------------------ one turn
    async def _start_turn(self, client, p: dict) -> None:
        text = str(p.get("text", "")).strip()[:MAX_TEXT]
        tid = str(p.get("id") or "")[:32]
        if not text:
            return
        running = self.turns.get(client.id)
        if running is not None and not running.done():
            await self.send(client, "chat.error", {"id": tid, "code": "CHAT_BUSY",
                                                   "message": "an answer is still being written"})
            return
        if self.core.stopping:
            await self.send(client, "chat.error", {"id": tid, "code": "CORE_STOPPING", "message": "shutting down"})
            return
        self.clients[client.id] = client
        silent = bool(p.get("silent")) or self.quiet_now()          # the Core decides too: quiet hours always win
        voice = bool(p.get("voice")) and not silent
        self.turns[client.id] = asyncio.create_task(self._turn(client, tid, text, voice, silent))

    async def _turn(self, client, tid: str, text: str, voice: bool, silent: bool) -> None:
        core = self.core
        appr.CHAT_TURN.set({"client": client.id, "silent": silent})
        TOOL_SINK.set(lambda name, ok, code: asyncio.get_running_loop().create_task(
            self.send(client, "chat.tool", {"id": tid, "name": name, "ok": ok, "code": code})))
        sent = [False]

        def on_delta(piece: str) -> None:
            if piece:
                sent[0] = True
                asyncio.get_running_loop().create_task(self.send(client, "chat.delta", {"id": tid, "text": piece}))

        logs.new_cid()
        t0 = time.monotonic()
        token = ROUTE.set("text")
        reply = None
        try:
            if voice:
                await core.set_face("thinking")
            reply = await core.brain.respond(text, spoken=voice, on_delta=on_delta)
        except asyncio.CancelledError:
            await self.send(client, "chat.done", {"id": tid, "cancelled": True, "ok": False, "code": "CANCELLED",
                                                  "text": "", "silent": silent, "streamed": sent[0]})
            logs.info("core", "CHAT_CANCELLED", "text chat answer cancelled by the owner")
            raise
        except Exception:
            logs.error("core", "CHAT_TURN_FAILED", "text chat turn failed", exc=True)
            await self.send(client, "chat.done", {"id": tid, "ok": False, "code": "CORE_INTERNAL", "silent": silent,
                                                  "text": "Something went wrong; see the log.", "streamed": sent[0]})
            return
        finally:
            ROUTE.reset(token)
        from .app import SENSITIVE_TEXT
        logs.info("core", "REPLY", "text chat reply", tool=reply.tool, ok=reply.ok, result=reply.code,
                  ms=round((time.monotonic() - t0) * 1000), text=reply.text, typed=True,
                  utterance=f"<{len(text)} chars>" if SENSITIVE_TEXT.search(text) else text)
        core.curator.observe("user", text)
        core.curator.observe("assistant", reply.text, idle=core.live is None)
        await asyncio.sleep(0)                           # deltas already queued go out before the final message
        await self.send(client, "chat.done", {"id": tid, "ok": reply.ok, "code": reply.code, "source": reply.source,
                                              "tool": reply.tool, "text": reply.text, "silent": silent,
                                              "streamed": sent[0], "spoken": bool(voice)})
        if voice and core.live is None:
            await core.speak(reply.text)
        if voice:
            await core.set_face("idle")

    # ------------------------------------------------------------------ slash commands that need the Core
    def _store_path(self) -> Path:
        return Path(self.core.home) / "store.db"

    def recent_sessions(self) -> list[dict]:
        from .store import TYPED_KINDS
        p = self._store_path()
        if not p.exists():
            return []
        try:
            con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2)
            con.row_factory = sqlite3.Row
            try:
                q = ",".join("?" * len(TYPED_KINDS))
                rows = con.execute(f"SELECT id, started FROM sessions WHERE kind IN ({q}) ORDER BY started DESC "
                                   f"LIMIT ?", (*TYPED_KINDS, RESUME_LIMIT)).fetchall()
                out = []
                for r in rows:
                    first = con.execute("SELECT text, chars FROM turns WHERE session_id = ? AND role = 'user' "
                                        "ORDER BY ts LIMIT 1", (r["id"],)).fetchone()
                    n = con.execute("SELECT COUNT(*) FROM turns WHERE session_id = ? AND role = 'user'",
                                    (r["id"],)).fetchone()[0]
                    out.append({"id": r["id"], "started": r["started"], "turns": n,
                                "first": (first["text"] if first and first["text"] is not None else None)})
                return out
            finally:
                con.close()
        except sqlite3.Error:
            logs.warn("core", "CHAT_STORE_UNREADABLE", "conversation store could not be read")
            return []

    def session_turns(self, sid: str) -> list[tuple[str, str | None]]:
        p = self._store_path()
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            return [(r[0], r[1]) for r in con.execute(
                "SELECT role, text FROM turns WHERE session_id = ? ORDER BY ts, id", (sid,)).fetchall()]
        finally:
            con.close()

    async def _command(self, client, p: dict) -> None:
        name = str(p.get("name", ""))
        core = self.core
        if name == "status":
            await self.send(client, "chat.state", self.state(client))
        elif name == "sessions":
            await self.send(client, "chat.info", {"command": "sessions", "sessions": self.recent_sessions()})
        elif name == "new":
            if core.agent is not None and hasattr(core.agent, "reset_conversation"):
                core.agent.reset_conversation()
            logs.info("core", "CHAT_NEW", "new text conversation started")
            await self.send(client, "chat.info", {"command": "new", "ok": True})
        elif name == "resume":
            await self._resume(client, str(p.get("id", "")))
        elif name == "approvals":
            svc = core.approvals
            pend = [self.describe(a) for a, _f, _l in list(svc._pending.values())] if svc is not None else []
            hist = appr.read_history(core.home, 5)
            await self.send(client, "chat.info", {"command": "approvals", "pending": pend, "history": [
                {"decision": h.get("decision"), "channel": h.get("channel"), "kind": h.get("kind"),
                 "title": logs.redact(str(h.get("title", "")))[:120], "ts": h.get("ts")} for h in hist]})
        elif name == "undo":
            await self._undo(client, str(p.get("file", "")))

    async def _resume(self, client, sid: str) -> None:
        agent = self.core.agent
        if agent is None or not hasattr(agent, "seed_history"):
            await self.send(client, "chat.info", {"command": "resume", "ok": False, "reason": "no_ai"})
            return
        try:
            rows = self.session_turns(sid)
        except sqlite3.Error:
            rows = []
        if not rows:
            await self.send(client, "chat.info", {"command": "resume", "ok": False, "reason": "unknown"})
            return
        if any(text is None for _r, text in rows):
            await self.send(client, "chat.info", {"command": "resume", "ok": False, "reason": "private"})
            return
        rows = rows[-RESUME_MAX_TURNS:]
        await agent.seed_history(rows)
        logs.info("core", "CHAT_RESUMED", "earlier conversation loaded into the text chat", turns=len(rows))
        await self.send(client, "chat.info", {"command": "resume", "ok": True, "turns": [list(r) for r in rows]})

    async def _undo(self, client, name: str) -> None:
        """The last change to a workspace file (SOUL.md, USER.md ...), through the tool that asks first."""
        core = self.core
        ws = core.workspace
        running = self.turns.get(client.id)
        if running is not None and not running.done():
            await self.send(client, "chat.error", {"code": "CHAT_BUSY", "message": "an answer is still being written"})
            return
        if not name:
            best = None
            for f in ws.files():
                if f["generated"] or not f["versions"]:
                    continue
                saved = ws.history(f["name"])[0]["saved"]
                if best is None or saved > best[0]:
                    best = (saved, f["name"])
            name = best[1] if best else ""
        if not name:
            await self.send(client, "chat.info", {"command": "undo", "ok": False, "reason": "nothing"})
            return
        async def run() -> None:
            appr.CHAT_TURN.set({"client": client.id, "silent": True})
            res = await core.registry.call("workspace_undo", {"file": Path(name).stem.upper()}, origin="user")
            await self.send(client, "chat.info", {"command": "undo", "ok": res.ok, "file": name, "code": res.code})
        self.turns[client.id] = asyncio.create_task(run())
