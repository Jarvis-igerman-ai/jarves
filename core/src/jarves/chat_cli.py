"""`jarvis chat` (alias `jarvis text`): an interactive terminal text chat with the running Core, for silent hours.

Text only: no microphone, no speech, no chimes (see chat_service.py for the Core side and its rules). Standard
library only: line input comes from `input()` on a worker thread (so the link keeps answering pings while you type),
history lives in <home>/chat_history.txt (also fed to `readline` where Python has it). Hebrew is printed in logical
order exactly as received/typed; the terminal does the right-to-left display and nothing here ever reverses text.

Keys: Enter sends. A line ending in a backslash continues on the next line; a line of just \"\"\" opens/closes a
block. Ctrl+C = cancel the answer being written (at the prompt: clear the line). Ctrl+D or /exit = leave.
"""
from __future__ import annotations

import asyncio
import getpass
import json
import signal
import sys
import uuid
from pathlib import Path

from . import approval_popup as popup
from . import clilang, logs

HISTORY_FILE = "chat_history.txt"
HISTORY_KEEP = 1000
CANCEL_WAIT_S = 15.0
CODE_TRIES = 3
YES = frozenset({"y", "yes", "כן", "אישור", "approve"})
NO = frozenset({"n", "no", "לא", "deny", "cancel", ""})
FENCE = '"""'

COMMANDS = (
    ("/help", "show this list", "הצג את הרשימה"),
    ("/status", "Core and connections summary", "סיכום של הליבה והחיבורים"),
    ("/new", "start a new conversation", "שיחה חדשה"),
    ("/resume", "pick a recent conversation to continue", "המשך שיחה אחרונה"),
    ("/voice on|off", "also speak the answers (off by default; never in silent hours)", "להקריא גם את התשובות"),
    ("/quiet", "quiet hours and whether this session is silent", "שעות שקט והאם השיחה שקטה"),
    ("/approvals", "what waits for your approval, and the last decisions", "מה ממתין לאישור ומה הוחלט"),
    ("/undo [file]", "undo the last change to a persona/workspace file (asks first)", "ביטול השינוי האחרון בקובץ"),
    ("/model", "the AI model and how it is billed (read only)", "המודל ואופן החיוב (קריאה בלבד)"),
    ("/mode", "text/voice/silent mode of this session (read only)", "מצב השיחה (קריאה בלבד)"),
    ("/exit", "leave (also Ctrl+D)", "יציאה"),
)


def T(he: str, en: str) -> str:
    return clilang.fmt(he, en)


# ---------------------------------------------------------------------- streams
def prepare_streams() -> None:
    """Hebrew in and out: UTF-8 on redirected/legacy streams. The Windows console itself is already Unicode."""
    for s in (sys.stdin, sys.stdout, sys.stderr):
        enc = str(getattr(s, "encoding", "") or "").lower().replace("-", "")
        if s is not None and enc != "utf8" and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


# ---------------------------------------------------------------------- history
class History:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lines: list[str] = []
        if path is not None:
            try:
                self.lines = [l for l in path.read_text(encoding="utf-8").split("\x1e") if l][-HISTORY_KEEP:]
            except OSError:
                self.lines = []
        try:
            import readline                      # POSIX: arrow-up recall; Windows consoles keep their own history
            for l in self.lines:
                readline.add_history(l.replace("\n", " "))
        except ImportError:
            pass

    def add(self, text: str) -> None:
        text = text.strip()
        if not text or (self.lines and self.lines[-1] == text):
            return
        self.lines = (self.lines + [text])[-HISTORY_KEEP:]
        if self.path is not None:
            try:                                  # records are separated by \x1e so a multi-line message stays one
                self.path.write_text("\x1e".join(self.lines), encoding="utf-8")
            except OSError:
                pass


# ---------------------------------------------------------------------- line input
class StdinLines:
    """Blocking reads on a worker thread. A read that was abandoned (the approval was answered elsewhere) stays
    pending and is returned by the next read: no typed line is ever lost or read twice."""

    def __init__(self, stdin=None, out=None) -> None:
        self.stdin = stdin or sys.stdin
        self.out = out or sys.stdout
        self._pending: asyncio.Future | None = None

    def _blocking(self, prompt: str, secret: bool) -> str:
        if getattr(self.stdin, "isatty", lambda: False)():
            return getpass.getpass(prompt) if secret else input(prompt)
        self.out.write(prompt)
        self.out.flush()
        line = self.stdin.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n")

    async def read(self, prompt: str, *, secret: bool = False) -> str:
        if self._pending is None:
            self._pending = asyncio.get_running_loop().run_in_executor(None, self._blocking, prompt, secret)
        try:
            return await asyncio.shield(self._pending)
        finally:
            if self._pending is not None and self._pending.done():
                self._pending = None


# ---------------------------------------------------------------------- link transport
class LinkTransport:
    """The local link (websocket, role "cli") to the running Core."""

    def __init__(self, ws) -> None:
        self.ws = ws

    @classmethod
    async def connect(cls, home: Path, port: int) -> "LinkTransport":
        from websockets.asyncio.client import connect
        token = (home / "link_token").read_text(encoding="utf-8").strip()
        ws = await connect(f"ws://127.0.0.1:{port}", open_timeout=5, ping_interval=None, max_size=1024 * 1024)
        await ws.send(json.dumps({"type": "hello", "payload": {"token": token, "client": "cli"}}))
        while json.loads(await ws.recv())["type"] != "hello.ok":
            pass
        return cls(ws)

    async def send(self, t: str, payload: dict | None = None) -> None:
        await self.ws.send(json.dumps({"type": t, "payload": payload or {}}, ensure_ascii=False))

    async def recv(self):
        try:
            m = json.loads(await self.ws.recv())
        except Exception:
            return None
        return m["type"], m.get("payload") or {}

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------- the REPL
class ChatSession:
    def __init__(self, transport, lines, out=None, *, history: History | None = None, silent: bool = False,
                 verbose: bool = False) -> None:
        self.t, self.lines = transport, lines
        self.out = out or sys.stdout
        self.history = history or History(None)
        self.forced_silent = silent
        self.verbose = verbose
        self.quiet = False                    # the Core's quiet hours
        self.voice = False
        self.state: dict = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.in_wait = False
        self._get: asyncio.Future | None = None
        self._pump_task: asyncio.Task | None = None
        self.exit_code = 0

    # -------------------------------------------------------------- output
    def say(self, text: str = "", end: str = "\n") -> None:
        self.out.write(text + end)
        self.out.flush()

    @property
    def silent(self) -> bool:
        return self.forced_silent or self.quiet

    def mode(self) -> str:
        return "silent" if self.silent else ("voice" if self.voice else "text")

    def prompt(self, cont: bool = False) -> str:
        return "....> " if cont else f"you [{self.mode()}]> "

    # -------------------------------------------------------------- link plumbing
    async def _pump(self) -> None:
        while True:
            m = await self.t.recv()
            if m is None:
                self.events.put_nowait(("__closed__", {}))
                return
            self.events.put_nowait(m)

    def interrupt(self) -> None:
        """Ctrl+C. While an answer is being written: cancel it. At the prompt: only a hint."""
        if self.in_wait:
            self.events.put_nowait(("__interrupt__", {}))
        else:
            self.say("\n" + T("(Ctrl+C) יציאה: Ctrl+D או /exit", "(Ctrl+C) leave with Ctrl+D or /exit"))

    async def _next(self, extra: set | None = None):
        """Next link event, or one of `extra` finishing first (returned as (task, None))."""
        if self._get is None or self._get.cancelled():
            self._get = asyncio.ensure_future(self.events.get())
        done, _ = await asyncio.wait({self._get, *(extra or set())}, return_when=asyncio.FIRST_COMPLETED)
        if self._get.done():
            ev, self._get = self._get.result(), None
            return ev
        return next(iter(done & (extra or set())))

    # -------------------------------------------------------------- the event loop of one turn / command
    async def wait_for(self, finish: set[str], tid: str = "") -> tuple[str, dict]:
        """Show everything that arrives until one of the `finish` message types; handles approvals, streaming,
        tool lines and Ctrl+C. Returns that final message."""
        self.in_wait = True
        streamed = False
        cancelled_at: float | None = None
        ask: asyncio.Task | None = None
        ask_req: dict = {}
        tries = 0
        loop = asyncio.get_running_loop()
        try:
            while True:
                if cancelled_at is not None and loop.time() - cancelled_at > CANCEL_WAIT_S:
                    self.say(T("אין מענה מהליבה.", "No answer from the Core."))
                    return "chat.error", {"code": "TIMEOUT"}
                got = await self._next({ask} if ask is not None else None)
                if isinstance(got, asyncio.Future):                    # the approval prompt returned a line
                    ask_task, ask = got, None
                    try:
                        answer = ask_task.result()
                    except (EOFError, KeyboardInterrupt):
                        answer = ""
                    approved, typed = self._parse_answer(ask_req, answer)
                    if approved is None:                                # not understood: ask again
                        ask = asyncio.ensure_future(self._ask_line(ask_req))
                        continue
                    await self.t.send("chat.approval_answer", {"id": ask_req["id"], "approved": approved,
                                                               "typed": typed})
                    ask_req["_sent"] = approved
                    continue
                t, p = got
                if t == "__closed__":
                    self.say("\n" + T("החיבור לליבה נסגר.", "The connection to the Core closed."))
                    self.exit_code = 1
                    return "__closed__", {}
                if t == "__interrupt__":
                    if cancelled_at is None:
                        cancelled_at = loop.time()
                        await self.t.send("chat.cancel", {})
                        self.say("\n" + T("מבטל את התשובה...", "Cancelling the answer..."))
                    continue
                if t == "chat.delta" and p.get("id") == tid:
                    if not streamed:
                        self.say("jarvis> ", end="")
                        streamed = True
                    self.say(p.get("text", ""), end="")
                elif t == "chat.tool" and p.get("id") == tid:
                    self._tool_line(p, streamed)
                elif t == "chat.approval_request":
                    if streamed:
                        self.say()
                        streamed = False
                    ask_req = p
                    tries = 0
                    self._show_request(p)
                    ask = asyncio.ensure_future(self._ask_line(p))
                elif t == "chat.approval_result":
                    if not p.get("ok") and ask_req and p.get("id") == ask_req.get("id"):
                        if ask_req.get("typed") and ask_req.get("_sent"):
                            tries += 1
                            if tries < CODE_TRIES:
                                self.say(T("הקוד לא נכון. נסה שוב.", "Wrong code. Try again."))
                                ask_req.pop("_sent", None)
                                ask = asyncio.ensure_future(self._ask_line(ask_req))
                                continue
                            await self.t.send("chat.approval_answer", {"id": ask_req["id"], "approved": False})
                            self.say(T("נדחה.", "Denied."))
                        else:
                            self.say(T("כבר נענה במקום אחר.", "Already answered elsewhere."))
                elif t == "chat.approval_closed":
                    if ask is not None and p.get("id") == ask_req.get("id"):
                        ask.cancel()                                    # the read itself stays pending, not lost
                        ask = None
                        self.say(T("נענה במקום אחר: ", "Answered elsewhere: ") + str(p.get("decision")) +
                                 (" (" + str(p.get("channel")) + ")" if p.get("channel") else "") + " " +
                                 T("(לחץ Enter להמשך)", "(press Enter to continue)"))
                    elif p.get("id") == ask_req.get("id"):
                        self.say(("Approved." if p.get("decision") == "approved" else "Not approved.") +
                                 (f" [{p.get('decision')}]" if self.verbose else ""))
                    ask_req = {}
                elif t in finish and (not tid or p.get("id", tid) == tid or t != "chat.done"):
                    if streamed:
                        self.say()
                    return t, p
                elif t == "chat.error" and t not in finish:
                    self.say(f"! {p.get('message', p.get('code', ''))}")
        finally:
            self.in_wait = False
            if ask is not None:
                ask.cancel()

    def _tool_line(self, p: dict, streaming: bool) -> None:
        if streaming:
            self.say()
        name = logs.redact(str(p.get("name", "?")))[:60]
        ok = bool(p.get("ok"))
        extra = f" [{logs.redact(str(p.get('code', '')))}]" if (self.verbose or not ok) else ""
        self.say(f"  > {name}  {'ok' if ok else 'FAILED'}{extra}")

    # -------------------------------------------------------------- approvals
    def _show_request(self, p: dict) -> None:
        lang = "he" if str(p.get("lang", "en")).lower().startswith("he") else "en"
        s = popup.STRINGS[lang]
        self.say()
        self.say(f"[{s['risk'].get(p.get('risk'), p.get('risk'))}] {popup.headline(p, lang)}")
        self.say("  " + logs.redact(popup.sentence(p)))
        for label, value, _style in popup.detail_rows(p, lang):
            self.say(f"  {label + ': ' if label else '    '}{logs.redact(value)}")
        if p.get("typed"):
            self.say("  " + s["code"].format(code=p.get("code", "")))
        if self.verbose:
            self.say(f"  (id {p.get('id')}, shown on {', '.join(p.get('shown') or [])}, {p.get('seconds_left')} s left)")
        else:
            self.say("  " + T(f"ממתין {p.get('seconds_left')} שניות; אפשר לענות גם בחלון.",
                              f"Waiting {p.get('seconds_left')} s; you can also answer in the popup."))

    async def _ask_line(self, req: dict) -> str:
        if req.get("typed"):
            return await self.lines.read(T("קוד (מוסתר), Enter לדחייה: ", "Code (hidden), Enter to deny: "), secret=True)
        return await self.lines.read(T("לאשר? [y]כן / [n]לא: ", "Approve? [y]es / [n]o: "))

    @staticmethod
    def _parse_answer(req: dict, answer: str) -> tuple[bool | None, str]:
        a = answer.strip().lower()
        if req.get("typed"):
            return (True, answer.strip()) if a else (False, "")
        if a in YES:
            return True, ""
        if a in NO:
            return False, ""
        return None, ""

    # -------------------------------------------------------------- input
    async def read_message(self) -> str | None:
        """One message (multi-line aware). None = leave (Ctrl+D)."""
        parts: list[str] = []
        block = False
        while True:
            try:
                line = await self.lines.read(self.prompt(bool(parts) or block))
            except EOFError:
                return None
            except KeyboardInterrupt:
                self.say("\n" + T("(נוקה)", "(cleared)"))
                parts, block = [], False
                continue
            if block:
                if line.strip() == FENCE:
                    return "\n".join(parts)
                parts.append(line)
                continue
            if not parts and line.strip() == FENCE:
                block = True
                continue
            if line.endswith("\\") and not line.endswith("\\\\"):
                parts.append(line[:-1])
                continue
            parts.append(line)
            return "\n".join(parts)

    # -------------------------------------------------------------- commands
    def show_help(self) -> None:
        self.say(T("פקודות:", "Commands:"))
        for c, en, he in COMMANDS:
            self.say(f"  {c:<14} {T(he, en)}")
        self.say(T('שורה שמסתיימת ב-\\ ממשיכה; """ פותח/סוגר בלוק. Ctrl+C מבטל תשובה, Ctrl+D יוצא.',
                   'A line ending in \\ continues; """ opens/closes a block. Ctrl+C cancels an answer, Ctrl+D leaves.'))

    def show_status(self, st: dict) -> None:
        self.state = st or self.state
        if st.get("quiet") is not None and not self.forced_silent:
            self.quiet = bool(st["quiet"])
        self.say(f"Core: {st.get('core')}  (JARVES {st.get('version')}, up {st.get('uptime_s')} s)")
        for name, c in (st.get("components") or {}).items():
            self.say(f"  {name:<14} {c.get('status')}" + (f"  {c.get('detail')}" if c.get("detail") and
                                                           c.get("status") != "ok" else ""))
        self.say(f"AI: {st.get('ai')}")
        self.say(f"Session: {self.mode()}" + (f"; pending approvals: {st.get('pending_approvals')}"
                                              if st.get("pending_approvals") else ""))

    def show_quiet(self) -> None:
        w = self.state.get("quiet_window") or {}
        on = "on" if w.get("enabled") else "off"
        self.say(f"Quiet hours: {on}, {w.get('start')}-{w.get('end')}; now: "
                 f"{'ACTIVE' if self.quiet else 'not active'}")
        self.say(T("השיחה הזו: ", "This session: ") + ("silent" + (" (--silent)" if self.forced_silent and
                                                                 not self.quiet else "") if self.silent else self.mode()))

    async def command(self, line: str) -> bool:
        """A slash command. False = leave."""
        parts = line.strip().split()
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in ("/exit", "/quit"):
            return False
        if cmd == "/help":
            self.show_help()
        elif cmd == "/status":
            await self.t.send("chat.command", {"name": "status"})
            t, p = await self.wait_for({"chat.state", "chat.error"})
            if t == "chat.state":
                self.show_status(p)
        elif cmd == "/new":
            await self.t.send("chat.command", {"name": "new"})
            await self.wait_for({"chat.info", "chat.error"})
            self.say(T("שיחה חדשה.", "New conversation."))
        elif cmd == "/resume":
            await self.resume(args)
        elif cmd == "/voice":
            await self.set_voice(args)
        elif cmd == "/quiet":
            await self.refresh_state()
            self.show_quiet()
        elif cmd == "/approvals":
            await self.t.send("chat.command", {"name": "approvals"})
            t, p = await self.wait_for({"chat.info", "chat.error"})
            self.show_approvals(p)
        elif cmd == "/undo":
            await self.t.send("chat.command", {"name": "undo", "file": args[0] if args else ""})
            t, p = await self.wait_for({"chat.info", "chat.error"})
            if t == "chat.info":
                self.say(T("הוחזר: ", "Restored: ") + str(p.get("file")) if p.get("ok") else
                         T("אין מה לבטל או שלא אושר.", "Nothing to undo, or it was not approved.")
                         + (f" [{p.get('code') or p.get('reason')}]" if self.verbose else ""))
        elif cmd in ("/model", "/mode"):
            await self.refresh_state()
            st = self.state
            if cmd == "/model":
                self.say(f"{st.get('ai')}")
                self.say(f"text model: {st.get('text_model')}; connection: {st.get('connection')}")
                if st.get("billing"):
                    self.say(f"billing: {json.dumps(st.get('billing'), ensure_ascii=False)}")
            else:
                self.say(f"session: {self.mode()}; Core voice mode: {st.get('voice_mode')}; "
                         f"speaker: {'yes' if st.get('can_speak') else 'no'}")
        else:
            self.say(T("פקודה לא מוכרת. /help מציג את הרשימה.", "Unknown command. /help lists them."))
        return True

    async def refresh_state(self) -> None:
        await self.t.send("chat.command", {"name": "status"})
        t, p = await self.wait_for({"chat.state", "chat.error"})
        if t == "chat.state":
            self.state = p
            if not self.forced_silent:
                self.quiet = bool(p.get("quiet"))

    def show_approvals(self, p: dict) -> None:
        pend = p.get("pending") or []
        if not pend:
            self.say(T("שום דבר לא ממתין לאישור.", "Nothing is waiting for your approval."))
        for r in pend:
            self.say(f"  [{r.get('risk')}] {r.get('kind')}: {logs.redact(str(r.get('title')))}  ({r.get('seconds_left')} s)")
        for h in p.get("history") or []:
            self.say(f"  {h.get('decision'):<9} {h.get('channel') or '':<9} {h.get('kind')}: {h.get('title')}")

    async def set_voice(self, args: list[str]) -> None:
        want = (args[0].lower() if args else "")
        if want not in ("on", "off"):
            self.say(f"voice: {'on' if self.voice else 'off'}   (/voice on|off)")
            return
        if want == "off":
            self.voice = False
            self.say(T("הקול כבוי: טקסט בלבד.", "Voice off: text only."))
            return
        await self.refresh_state()
        if self.silent:
            self.say(T("אי אפשר להפעיל קול בזמן שקט.", "Voice cannot be turned on while the session is silent."))
        elif not self.state.get("can_speak"):
            self.say(T("אין רמקול זמין בליבה.", "The Core has no speaker available."))
        else:
            self.voice = True
            self.say(T("הקול דולק: התשובות גם יוקראו.", "Voice on: answers are also spoken."))

    async def resume(self, args: list[str]) -> None:
        await self.t.send("chat.command", {"name": "sessions"})
        t, p = await self.wait_for({"chat.info", "chat.error"})
        rows = p.get("sessions") or []
        if not rows:
            self.say(T("אין שיחות קודמות.", "No earlier conversations."))
            return
        from datetime import datetime
        for i, r in enumerate(rows, 1):
            when = datetime.fromtimestamp(r["started"]).strftime("%Y-%m-%d %H:%M")
            first = " ".join(str(r["first"] if r["first"] is not None else "(private)").split())[:60]
            self.say(f"  {i}. {when}  {r['turns']} msg  {first}")
        pick = args[0] if args else ""
        if not pick:
            try:
                pick = (await self.lines.read(T("מספר (Enter לביטול): ", "Number (Enter to cancel): "))).strip()
            except (EOFError, KeyboardInterrupt):
                pick = ""
        if not pick.isdigit() or not 1 <= int(pick) <= len(rows):
            self.say(T("בוטל.", "Cancelled."))
            return
        await self.t.send("chat.command", {"name": "resume", "id": rows[int(pick) - 1]["id"]})
        t, p = await self.wait_for({"chat.info", "chat.error"})
        if not p.get("ok"):
            why = {"private": T("השיחה נשמרה במצב פרטיות ואי אפשר להמשיך אותה.",
                                "That conversation was stored in privacy mode and cannot be resumed."),
                   "no_ai": T("אין בינה מלאכותית פעילה.", "No AI is active.")}.get(
                p.get("reason"), T("לא נמצאה השיחה.", "Conversation not found."))
            self.say(why)
            return
        self.say(T("ממשיך את השיחה. הסוף שלה:", "Resumed. The end of it:"))
        for role, text in (p.get("turns") or [])[-6:]:
            self.say(f"  {'you' if role == 'user' else 'jarvis'}> " + " ".join(str(text).split())[:300])

    # -------------------------------------------------------------- main loop
    async def run(self) -> int:
        self._pump_task = asyncio.ensure_future(self._pump())
        try:
            await self.t.send("chat.hello", {})
            t, st = await self.wait_for({"chat.state"})
            if t != "chat.state":
                return self.exit_code or 1
            self.state = st
            self.quiet = bool(st.get("quiet"))
            w = st.get("quiet_window") or {}
            self.say(T("שיחת טקסט עם JARVES. /help לעזרה, Ctrl+D ליציאה.",
                       "JARVES text chat. /help for help, Ctrl+D to leave."))
            if self.silent:
                why = "--silent" if self.forced_silent and not self.quiet else \
                    f"quiet hours {w.get('start')}-{w.get('end')}"
                self.say(f"[silent] {why}: " + T("שום קול, צליל או אור בפנים בשיחה הזו.",
                                                  "no speech, chimes or face light in this session."))
            else:
                self.say(T("טקסט בלבד. /voice on מקריא גם בקול.", "Text only. /voice on also speaks the answers."))
            while True:
                msg = await self.read_message()
                if msg is None:
                    break
                if not msg.strip():
                    continue
                self.history.add(msg)
                if msg.lstrip().startswith("/") and "\n" not in msg:
                    if not await self.command(msg):
                        break
                    if self.exit_code:
                        break
                    continue
                tid = uuid.uuid4().hex[:8]
                await self.t.send("chat.turn", {"id": tid, "text": msg, "voice": self.voice and not self.silent,
                                                "silent": self.silent})
                t, p = await self.wait_for({"chat.done", "chat.error"}, tid)
                if t == "__closed__":
                    break
                self.finish_turn(p)
            return self.exit_code
        finally:
            if self._pump_task is not None:
                self._pump_task.cancel()
            await self.t.close()

    def finish_turn(self, p: dict) -> None:
        if p.get("cancelled"):
            self.say(T("(בוטל)", "(cancelled)"))
            return
        if "silent" in p and not self.forced_silent:
            now = bool(p["silent"])
            if now and not self.quiet:
                self.say(T("[silent] התחילו שעות השקט: מעכשיו שקט לגמרי.", "[silent] quiet hours began: silent from now on."))
                self.voice = False
            self.quiet = now
        if not p.get("streamed") and p.get("text"):
            self.say("jarvis> " + p["text"])
        if not p.get("ok"):
            self.say(f"[{p.get('code')}]")


# ---------------------------------------------------------------------- entry point
async def run_chat(home: Path, port: int, *, silent: bool, verbose: bool) -> int:
    prepare_streams()
    transport = await LinkTransport.connect(home, port)
    session = ChatSession(transport, StdinLines(), history=History(home / HISTORY_FILE), silent=silent,
                          verbose=verbose)
    loop = asyncio.get_running_loop()
    old = None
    try:
        old = signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(session.interrupt))
    except ValueError:
        old = None                                   # not the main thread
    try:
        return await session.run()
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)
