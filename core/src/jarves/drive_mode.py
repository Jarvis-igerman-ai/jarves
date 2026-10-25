"""Drive mode policy (V6 plan item 2, issue #209): what is allowed while the owner is driving.

Pure policy plus a small "do it when stopped" queue. Nothing here places a call, sends a message or opens a link;
it only DECIDES, and every action that needs an approval keeps needing it (`Verdict.needs_approval` never means
"approved"). Default OFF: with drive mode off every action is allowed exactly as before. The only switch is the owner's:
`jarvis drive on|off` (the `drive_mode` feature switch). No location, no vehicle control.

Rules while ON:
  * emergencies, hang-up and stop/cancel are NEVER blocked or deferred (not even with a full queue);
  * short spoken answers, time/weather/calendar reads, timers/reminders, music and a navigation link are allowed;
  * a call or a message only for a KNOWN contact, by voice, with a short spoken confirmation, and never when the
    request came from untrusted text (e-mail, web, message, recalled text);
  * anything that needs the screen, a long read, settings, files, the browser, purchases or any unknown action is
    deferred to the queue and shown when the owner says they have stopped.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import clilang
from . import features as feat
from . import hebrew
from . import logs

FEATURE = "drive_mode"
MAX_QUEUE = 20
MAX_TEXT = 200
MAX_SPOKEN_CHARS = 280          # a drive-mode answer is read aloud: keep it short
QUEUE_TTL_S = 24 * 3600         # a deferred item older than a day is dropped, not offered

ALWAYS = frozenset({"emergency_call", "hang_up", "stop", "cancel"})
ALLOWED = frozenset({"answer", "time", "weather", "calendar_read", "timer", "reminder", "music", "navigate_link"})
VOICE_CONFIRMED = frozenset({"call", "message_send"})      # allowed only for a known contact, from the owner's own words
UNTRUSTED = ("email", "web", "message", "recalled", "other")   # examples; ANY source but the owner's is untrusted
TRUSTED = frozenset({"owner"})
_CTRL = dict.fromkeys([*range(0, 32), *range(0x7F, 0xA0), 0x202A, 0x202B, 0x202C, 0x202D, 0x202E])


def _clean(text, limit: int) -> str:
    """One line of plain text: whitespace collapsed, control characters (terminal escapes, bidi overrides) removed."""
    return " ".join(str(text if text is not None else "").translate(_CTRL).split())[:limit]


@dataclass(frozen=True)
class Verdict:
    decision: str                 # "allow" | "defer"
    code: str                     # stable reason code, see T
    needs_approval: bool = False  # the normal approval flow still applies; this module never approves
    confirm: str = "none"         # "none" | "spoken_short" (one short spoken yes/no, never a screen)
    always: bool = False          # an emergency / hang-up / stop: nothing may block it

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


T: dict[str, tuple[str, str]] = {
    "DRIVE_OFF": ("מצב נהיגה כבוי. להפעלה: jarvis drive on", "Drive mode is off. Turn it on with: jarvis drive on"),
    "DRIVE_ALWAYS": ("חירום וניתוק תמיד פעילים.", "Emergency, hang-up and stop always work."),
    "DRIVE_OK": ("מותר בנהיגה.", "Allowed while driving."),
    "DRIVE_CONFIRM": ("מותר בנהיגה עם אישור קולי קצר.", "Allowed while driving with one short spoken confirmation."),
    "DRIVE_UNKNOWN_CONTACT": ("אפשר להתקשר או לשלוח רק לאיש קשר מוכר. נשמר לאחר העצירה.",
                              "Calls and messages only go to a known contact. Saved for when you stop."),
    "DRIVE_UNTRUSTED": ("בקשה שמקורה בטקסט לא מהימן לא מבוצעת בנהיגה. נשמר לאחר העצירה.",
                        "A request that came from untrusted text is not run while driving. Saved for when you stop."),
    "DRIVE_DEFER": ("זה נשמר לאחר העצירה.", "That is saved for when you stop."),
    "DRIVE_QUEUE_FULL": ("רשימת הממתינים מלאה; הפעולה לא נשמרה.", "The when-stopped list is full; the action was not saved."),
    "DRIVE_STOPPED": ("עצרת. ממתינים לך: {n}.", "You have stopped. Waiting for you: {n}."),
    "DRIVE_CLEARED": ("נמחקו {n} פריטים.", "Cleared {n} items."),
}


def say(code: str, lang: str = "en", **kw) -> str:
    he, en = T[code]
    return (he if lang == "he" else en).format(**kw)


def decide(action: str, *, active: bool, known_contact: bool = False, source: str = "owner",
           by_voice: bool = True) -> Verdict:
    """The policy. `action` is a short kind (see ALWAYS / ALLOWED / VOICE_CONFIRMED); anything else is deferred."""
    trusted = source in TRUSTED
    if action == "emergency_call" and not trusted:
        # untrusted text (an e-mail, a web page, a message) can never place an emergency call; the owner's own words can
        return Verdict("defer", "DRIVE_UNTRUSTED")
    if action in ALWAYS:
        return Verdict("allow", "DRIVE_ALWAYS", always=True)          # even with drive mode off or the queue full
    if not active:
        return Verdict("allow", "DRIVE_OFF")
    if action in ALLOWED:
        # a navigation link is opened by the owner's tap or approval, never by this module
        return Verdict("allow", "DRIVE_OK", needs_approval=(action == "navigate_link"))
    if action in VOICE_CONFIRMED:
        if not trusted:
            return Verdict("defer", "DRIVE_UNTRUSTED")
        if not known_contact or not by_voice:
            return Verdict("defer", "DRIVE_UNKNOWN_CONTACT")
        return Verdict("allow", "DRIVE_CONFIRM", needs_approval=True, confirm="spoken_short")
    return Verdict("defer", "DRIVE_DEFER")


def shorten(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    """Cut a spoken answer at a word edge so a drive-mode reply stays short."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "..."


@dataclass
class QueueItem:
    id: int
    action: str
    text: str
    ts: float
    source: str = "owner"


@dataclass
class DriveQueue:
    """The 'when stopped' list: a small JSON file next to memory.db. Text is cut to MAX_TEXT; nothing is executed."""
    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _read(self, now: float) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out: list[dict] = []
        for d in data if isinstance(data, list) else []:
            try:
                if isinstance(d, dict) and {"id", "action", "text", "ts"} <= set(d) and int(d["id"]) >= 0 \
                        and now - float(d["ts"]) <= QUEUE_TTL_S:
                    out.append(d)
            except (TypeError, ValueError, OverflowError):
                continue
        return out[-MAX_QUEUE:]

    def _write(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
        os.replace(tmp, self.path)

    @staticmethod
    def _item(r: dict) -> QueueItem:
        return QueueItem(int(r["id"]), _clean(r["action"], 40), _clean(r["text"], MAX_TEXT), float(r["ts"]),
                         _clean(r.get("source", "owner"), 20))

    def items(self, now: float | None = None) -> list[QueueItem]:
        with self._lock:
            return [self._item(r) for r in self._read(time.time() if now is None else now)]

    def add(self, action: str, text: str, *, source: str = "owner", now: float | None = None) -> QueueItem | None:
        """Save an action for later; None when the list is full."""
        now = time.time() if now is None else now
        with self._lock:
            rows = self._read(now)
            if len(rows) >= MAX_QUEUE:
                return None
            row = {"id": max([int(r["id"]) for r in rows] + [0]) + 1, "action": _clean(action, 40),
                   "text": _clean(text, MAX_TEXT), "ts": now, "source": _clean(source, 20)}
            self._write(rows + [row])
        return self._item(row)

    def release(self, now: float | None = None) -> list[QueueItem]:
        """The owner has stopped: return everything waiting and empty the list. Items are shown, never auto-run."""
        with self._lock:
            out = [self._item(r) for r in self._read(time.time() if now is None else now)]
            self._write([])
        return out

    def clear(self) -> int:
        with self._lock:
            n = len(self._read(time.time()))
            self._write([])
        return n


def handle(action: str, text: str, queue: DriveQueue, *, active: bool, known_contact: bool = False,
           source: str = "owner", by_voice: bool = True) -> tuple[Verdict, QueueItem | None]:
    """Decide, and queue a deferred action. A full queue turns the verdict into DRIVE_QUEUE_FULL (still 'defer')."""
    v = decide(action, active=active, known_contact=known_contact, source=source, by_voice=by_voice)
    if v.decision != "defer":
        return v, None
    item = queue.add(action, text, source=source)
    if item is None:
        return Verdict("defer", "DRIVE_QUEUE_FULL"), None
    return v, item


# ---------------------------------------------------------------------- the SkillRegistry gate (V6 item 9, issue #216)
# Which policy kind a registered tool is. Anything not listed: a read tool is a short "answer" (a read tool that
# returns text written by others, like mail, is a "long_read" and waits), every other tool is its own unknown kind
# and is DEFERRED. The kind comes from the tool NAME (code), never from a model-written argument, except that
# dial_prepare with an emergency number/word is an emergency (which only the owner's own words may place).
ALWAYS_TOOLS: dict[str, str] = {
    "end_conversation": "hang_up", "mute_microphone": "stop", "music_stop": "stop", "music_pause": "stop",
    "music_mute": "stop", "timer_cancel": "cancel", "task_cancel": "cancel",
}
TOOL_KINDS: dict[str, str] = {
    **ALWAYS_TOOLS,
    "get_time": "time", "calendar_list_events": "calendar_read", "calendar_next_event": "calendar_read",
    "calendar_find_events": "calendar_read", "timer_start": "timer", "alarm_set": "timer", "anydo_remind": "reminder",
    "music_play": "music", "music_next": "music", "music_resume": "music", "music_volume": "music",
    "music_unmute": "music", "music_status": "music",
    "dial_prepare": "call", "dial_open": "call",
    "whatsapp_send": "message_send", "telegram_send": "message_send", "instagram_send": "message_send",
    "gmail_send_draft": "message_send", "whatsapp_draft": "message_send", "whatsapp_compose": "message_send",
    "telegram_draft": "message_send", "instagram_draft": "message_send", "gmail_create_draft": "message_send",
}
_RECIPIENT_FIELDS = ("name", "to", "contact", "chat", "recipient")

T.update({
    "DRIVE_OWNER_ONLY": ("רק אתם מכבים את מצב הנהיגה: jarvis drive off.",
                         "Only you can turn drive mode off: jarvis drive off."),
    "DRIVE_ERROR": ("בדיקת מצב הנהיגה נכשלה, אז לא ביצעתי את זה.",
                    "The drive-mode check failed, so I did not do that."),
    "DRIVE_REFUSED": ("שיחת חירום רק מהמילים שלכם, לא מטקסט שהגיע מבחוץ.",
                      "An emergency call only comes from your own words, never from outside text."),
})


def tool_kind(name: str, permission: str | None, args=None, *, untrusted_output: bool = False) -> str:
    if name == "dial_prepare" and args is not None:
        from .dialing import is_emergency
        if is_emergency(str(getattr(args, "number", "") or "")) or is_emergency(str(getattr(args, "name", "") or "")):
            return "emergency_call"
    if name in TOOL_KINDS:
        return TOOL_KINDS[name]
    if permission == "read":
        return "long_read" if untrusted_output else "answer"
    return name                     # unknown kind: decide() defers it


def turn_source(origin: str, route: str, tainted: bool) -> str:
    """Who the request really comes from, from Core state only (never a model-written field): text written by
    others was read in this turn -> "untrusted"; the owner's own words (the deterministic parser, or the live voice /
    text conversation with no third-party text this turn) -> "owner"; a scheduled job / goal plan -> "scheduled"."""
    if tainted:
        return "untrusted"
    if origin == "user" or route in ("voice", "text"):
        return "owner"
    return "scheduled"


def contacts_from_memory(memory) -> list[dict]:
    """The owner's explicitly saved contacts (memory kind "contact"): label, name, email."""
    out: list[dict] = []
    for row in memory.by_kind("contact"):
        try:
            d = json.loads(row["text"])
        except (TypeError, ValueError):
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _no_contacts() -> list[dict]:
    return []


@dataclass
class Gate:
    """Consulted by SkillRegistry._authorize BEFORE the approval prompt. Returns None (continue into the normal flow,
    unchanged) or a refusal ToolResult. It never approves anything and never runs a queued item."""
    active: Callable[[], bool]               # the drive_mode switch (only the owner flips it)
    queue: DriveQueue
    contacts: Callable[[], list] = _no_contacts

    def known(self, who: str) -> bool:
        w = " ".join(str(who or "").casefold().split())
        if not w:
            return False
        for c in self.contacts() or []:
            names = {" ".join(str(c.get(k) or "").casefold().split()) for k in ("label", "name", "email")} - {""}
            first = (str(c.get("name") or "").casefold().split() or [""])[0]
            if w in names or (first and w == first):
                return True
        return False

    @staticmethod
    def recipient(tool, args) -> str:
        if getattr(tool, "recipient", None) is not None:
            try:
                who = tool.recipient(args)
            except Exception:
                who = None
            if who:
                return str(who)
        for k in _RECIPIENT_FIELDS:
            v = getattr(args, k, None)
            if isinstance(v, str) and v.strip():
                return v
        return ""

    def check(self, tool, args, *, origin: str, route: str, tainted: bool):
        from .skills import ToolResult
        if not self.active():
            return None                                   # drive mode off: zero behaviour change
        source = turn_source(origin, route, tainted)
        by_voice = route == "voice"                       # set by the voice code path (skills.ROUTE), not the model
        # same normalisation as feature_set itself (a.id.strip().lower()), so "Drive_Mode " is caught here too
        if tool.name == "feature_set" and str(getattr(args, "id", "") or "").strip().lower() == FEATURE:
            if origin == "user" and not tainted:
                return None            # the owner's own words (deterministic parser): the normal flow decides
            # the model can never switch drive mode off (or "declare stopped") on its own authority
            logs.warn("drive", "DRIVE_OWNER_ONLY", "model tried to change drive mode; refused", tool=tool.name,
                      source=source)
            return ToolResult(False, say("DRIVE_OWNER_ONLY"), {"drive": "refused"}, code="DRIVE_OWNER_ONLY")
        kind = tool_kind(tool.name, tool.permission, args, untrusted_output=bool(tool.untrusted_output))
        who = self.recipient(tool, args) if kind in VOICE_CONFIRMED else ""
        v = decide(kind, active=True, known_contact=self.known(who), source=source, by_voice=by_voice)
        if v.allowed:
            if v.code != "DRIVE_OK" or tool.permission != "read":
                logs.info("drive", "DRIVE_ALLOWED", "drive mode allows this; normal approvals still apply",
                          tool=tool.name, kind=kind, reason=v.code, source=source, voice=by_voice)
            return None
        if kind == "emergency_call":
            logs.warn("drive", "DRIVE_REFUSED", "emergency call from untrusted text refused", tool=tool.name,
                      source=source)
            return ToolResult(False, say("DRIVE_REFUSED"), {"drive": "refused"}, code="DRIVE_REFUSED")
        label = tool.name + (f" -> {(who.split() or [''])[0]}" if who else "")     # never message text or arguments
        item = self.queue.add(kind, label, source=source)
        code = v.code if item is not None else "DRIVE_QUEUE_FULL"
        logs.info("drive", "DRIVE_DEFERRED", "deferred until the owner has stopped", tool=tool.name, kind=kind,
                  reason=code, source=source, voice=by_voice, item=item.id if item else 0)
        return ToolResult(False, say(code) + " Queued until stopped: tell the user in one short sentence; do not "
                          "retry it and do not use another tool for it.",
                          {"drive": "deferred" if item else "queue_full", "queued_id": item.id if item else 0},
                          code="DRIVE_DEFERRED" if item else "DRIVE_QUEUE_FULL")


def attach(registry, features, home, memory=None) -> Gate:
    """Wire drive mode into a SkillRegistry (Core and text session). With the switch off it does nothing."""
    contacts = (lambda: contacts_from_memory(memory)) if memory is not None else _no_contacts
    gate = Gate(active=lambda: features.active(FEATURE), queue=queue_for(home), contacts=contacts)
    registry.drive = gate
    return gate


# ---------------------------------------------------------------------- CLI: jarvis drive status|on|off|queue|stopped|clear
def queue_for(home) -> DriveQueue:
    return DriveQueue(Path(home) / "drive_queue.json")


def add_cli(sub) -> None:
    p = sub.add_parser("drive", help="drive mode: jarvis drive status | on | off | queue | stopped | clear. Off by default; "
                                     "emergencies and hang-up are never blocked; approvals are unchanged")
    p.add_argument("action", nargs="?", default="status", choices=["status", "on", "off", "queue", "stopped", "clear"])
    p.add_argument("--json", action="store_true")


def cmd_drive(a, cfg, home) -> int:
    from .cli import _cli_features
    f = _cli_features(cfg, home)
    q = queue_for(home)
    act = a.action

    def p(code: str, **kw) -> None:
        print(clilang.fmt(say(code, "he", **kw), say(code, "en", **kw)))
    if act in ("on", "off"):
        f.set(FEATURE, act == "on", by="cli")
        if act == "off" and q.items():
            p("DRIVE_STOPPED", n=len(q.items()))
    if act in ("queue", "stopped"):
        items = q.release() if act == "stopped" else q.items()
        if a.json:
            print(json.dumps([i.__dict__ for i in items], ensure_ascii=False))
        else:
            p("DRIVE_STOPPED", n=len(items))
            for i in items:
                print(f"  {i.id}. [{i.action}] {i.text}")
        return 0
    if act == "clear":
        p("DRIVE_CLEARED", n=q.clear())
        return 0
    state = {"drive_mode": "on" if f.active(FEATURE) else "off", "waiting": len(q.items())}
    print(json.dumps(state) if a.json else f"drive mode: {state['drive_mode']}; waiting for when you stop: {state['waiting']}")
    return 0


COMMANDS = {"drive": cmd_drive}


# ---------------------------------------------------------------------- catalog (default OFF, never wired by itself)
EVIDENCE = ("UNIT test_v6_drive_mode.py + test_v6_drive_wire.py (policy, queue, and the SkillRegistry gate before "
            "the approval prompt; no car session yet)")
BLOCKER = "no parked-car session with a real voice conversation yet; dial_open has no known-contact name, so calls wait"
ABOUT = ("a driving policy: short answers, calls and messages only to known contacts with one spoken confirmation, "
         "everything else waits in a 'when you stop' list; emergencies and hang-up are never blocked")
PI = "no vehicle path on the Raspberry Pi in V6 (GitHub issue #209 tracks the PC module)"
hebrew.TEXT.update({
    ABOUT: "מדיניות נהיגה: תשובות קצרות, שיחות והודעות רק לאנשי קשר מוכרים עם אישור קולי אחד, וכל השאר ממתין ברשימה "
           "\"כשתעצרו\"; חירום וניתוק אף פעם לא נחסמים",
    EVIDENCE: "בדיקות יחידה test_v6_drive_mode.py ו-test_v6_drive_wire.py (מדיניות, תור, ובדיקה ב-SkillRegistry לפני "
              "שאלת האישור; עדיין אין בדיקה ברכב)",
    BLOCKER: "עוד אין בדיקה ברכב חונה עם שיחה קולית אמיתית; ל-dial_open אין שם איש קשר, ולכן שיחות ממתינות",
    PI: "אין נתיב רכב על ה-Raspberry Pi ב-V6 (נושא #209 עוקב אחר מודול ה-PC)",
})

feat.register(FEATURE, "מצב נהיגה", "Drive mode", False, risk="low", about=ABOUT,
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["read", "queue of deferred actions; approvals unchanged"],
              settings="jarvis drive on|off|status|queue|stopped")
