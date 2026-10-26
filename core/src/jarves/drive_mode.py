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

import contextlib
import json
import os
import tempfile
import threading
import time
import unicodedata
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


def strip_invisible(text) -> str:
    """Remove invisible format characters (Unicode category Cf: bidi overrides/isolates, zero-width, BOM, soft hyphen)
    and the C1 controls. ONE helper for every sanitiser (the queue labels here, journey.clean_place). Keeps C0 controls
    and whitespace for the caller to fold or refuse."""
    return "".join(c for c in str(text if text is not None else "")
                   if unicodedata.category(c) != "Cf" and not 0x7F <= ord(c) < 0xA0)


def _clean(text, limit: int) -> str:
    """One line of plain text: whitespace collapsed, control characters (terminal escapes, bidi overrides) removed."""
    return " ".join(strip_invisible(str(text if text is not None else "").translate(_CTRL)).split())[:limit]


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


LOCK_WAIT_S = 5.0


@contextlib.contextmanager
def _file_lock(path: Path, wait_s: float | None = None):
    """An exclusive cross-process lock on `<path>.lock` (msvcrt on Windows, flock elsewhere). Fails closed: if it
    cannot be taken within `wait_s` (default LOCK_WAIT_S, read at call time) a TimeoutError is raised and nothing is
    written (issue #300)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path.with_name(path.name + ".lock"), "a+b")
    try:
        deadline = time.monotonic() + (LOCK_WAIT_S if wait_s is None else wait_s)
        while True:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("drive queue is locked by another process") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


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
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False)
            # Windows: os.replace fails while another process is reading the file (items() takes no lock); retry
            # briefly, then raise (the caller fails closed and the old file stays intact)
            for attempt in range(50):
                try:
                    os.replace(tmp, self.path)
                    return
                except PermissionError:
                    if attempt == 49:
                        raise
                    time.sleep(0.02)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)                         # only left over when the replace did not happen

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
        with self._lock, _file_lock(self.path):       # read-modify-write under one lock (issue #300)
            rows = self._read(now)
            if len(rows) >= MAX_QUEUE:
                return None
            row = {"id": max([int(r["id"]) for r in rows] + [0]) + 1, "action": _clean(action, 40),
                   "text": _clean(text, MAX_TEXT), "ts": now, "source": _clean(source, 20)}
            self._write(rows + [row])
        return self._item(row)

    def release(self, now: float | None = None) -> list[QueueItem]:
        """The owner has stopped: return everything waiting and empty the list. Items are shown, never auto-run."""
        with self._lock, _file_lock(self.path):
            out = [self._item(r) for r in self._read(time.time() if now is None else now)]
            self._write([])
        return out

    def clear(self) -> int:
        with self._lock, _file_lock(self.path):
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
BRIEFING_TOOL = "drive_briefing_get"                  # refused (not queued) while drive mode is on (#324)
PHONE_SCREEN = "phone_screen"                        # skills.ROUTE value of the phone Drive tab's briefing (Core code only)
PHONE_SCREEN_KINDS = ("answer", "long_read", "time", "calendar_read")        # the only tool kinds a phone screen request may run (reads)
_RECIPIENT_FIELDS = ("name", "to", "contact", "chat", "recipient")

T.update({
    "DRIVE_OWNER_ONLY": ("רק אתם מכבים את מצב הנהיגה: jarvis drive off.",
                         "Only you can turn drive mode off: jarvis drive off."),
    "DRIVE_PHONE_READ_ONLY": ("מסך הנהיגה בטלפון מציג מידע בלבד, אז לא ביצעתי את זה.",
                              "The phone Drive screen only shows information, so I did not do that."),
    "DRIVE_ERROR": ("בדיקת מצב הנהיגה נכשלה, אז לא ביצעתי את זה.",
                    "The drive-mode check failed, so I did not do that."),
    "DRIVE_BRIEFING_FIRST": ("התדריך לפני נסיעה צריך לבקש לפני שמדליקים את מצב הנהיגה, אז לא הרצתי אותו.",
                             "The pre-drive briefing has to be asked for before drive mode is turned on, so I did not run it."),
    "DRIVE_REFUSED": ("שיחת חירום רק מהמילים שלכם, לא מטקסט שהגיע מבחוץ.",
                      "An emergency call only comes from your own words, never from outside text."),
})


def trusted_origin(tool) -> bool:
    """Is this tool really the built-in one? The registry stamps `origin` on every tool it registers (issue #301).
    Anything else (generated / third-party, or a tool built outside a registry) is NOT built-in: fail closed."""
    return getattr(tool, "origin", None) == "builtin"


def tool_kind(name: str, permission: str | None, args=None, *, untrusted_output: bool = False,
              builtin: bool = True) -> str:
    """`builtin=False`: a tool with an allow-listed NAME but a non-built-in origin gets no allow-list kind (it is an
    unknown action and is deferred); a plain read is still only a read."""
    if not builtin:
        return ("long_read" if untrusted_output else "answer") if permission == "read" else name
    if name == "dial_prepare" and args is not None:
        from .dialing import is_emergency
        if is_emergency(str(getattr(args, "number", "") or "")) or is_emergency(str(getattr(args, "name", "") or "")):
            return "emergency_call"
    if name in TOOL_KINDS:
        return TOOL_KINDS[name]
    if permission == "read":
        return "long_read" if untrusted_output else "answer"
    return name                     # unknown kind: decide() defers it


def fallback_allows(name: str, permission: str | None, route: str) -> bool:
    """What may still run when the Gate itself raised (SkillRegistry._drive_check fails closed): a plain read, and
    hang-up / stop / cancel. On the read-only phone screen route only a plain read (issue #318): no phone path may
    cause a side effect, not even when the policy is broken."""
    if permission == "read":
        return True
    return route != PHONE_SCREEN and name in ALWAYS_TOOLS


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
    unchanged) or a refusal ToolResult. It never approves anything and never runs a queued item.

    The phone screen rule (issue #311): drive mode governs what JARVES says and does. A read-only display requested on
    a phone that holds the drive scope is allowed; the passenger rule always applies; no phone path may cause speech,
    a side effect or a queue release. In code: the phone's briefing runs under the route PHONE_SCREEN, which only Core
    code sets (skills.ROUTE; never a tool argument, never the model). On that route a plain READ tool (kind "answer",
    "long_read", "time" or "calendar_read", e.g. the briefing's mail/calendar/weather reads) is allowed and NOT queued, so repeated taps leave
    no junk in the "when you stop" list. Every other tool on that route is refused at once, drive mode on or off,
    and is never queued: nothing on the phone path can speak, send, call, or release the queue."""
    active: Callable[[], bool]               # the drive_mode switch (only the owner flips it)
    queue: DriveQueue
    contacts: Callable[[], list] = _no_contacts

    @staticmethod
    def _addr(value) -> str:
        """A phone number as E.164 or a plain ASCII e-mail address lower-cased; "" when it is neither. No casefold
        (it maps 'ß'->'ss' and the Kelvin sign->'k', so two different addresses could compare equal); a non-ASCII
        address, a display-name form ('Dana <d@x.com>') or anything with inner spaces is "" (never known)."""
        v = str(value or "").strip()
        if not v:
            return ""
        if "@" in v:
            return v.lower() if v.isascii() and v.count("@") == 1 and not any(c.isspace() or c in "<>\",;"
                                                                                for c in v) else ""
        from .dialing import normalize
        return normalize(v) or ""

    def known(self, who: str, target: str | None = None) -> bool:
        """Is the REAL destination a saved contact (issue #302)? With a `target` (the number / address the tool will
        really use) it must equal a saved contact's normalised number or address; the name is not enough. Without a
        target the name must match exactly ONE saved contact that has a number or address (an ambiguous or number-less
        name is not known). Fail closed."""
        contacts = [c for c in (self.contacts() or []) if isinstance(c, dict)]

        def addrs(c) -> set[str]:
            return {a for a in (self._addr(c.get(k)) for k in ("phone", "email", "address", "number")) if a}
        if target is not None:
            t = self._addr(target)
            return bool(t) and any(t in addrs(c) for c in contacts)
        w = " ".join(str(who or "").casefold().split())
        if not w:
            return False
        hits = []
        for c in contacts:
            names = {" ".join(str(c.get(k) or "").casefold().split()) for k in ("label", "name", "email")} - {""}
            first = (str(c.get("name") or "").casefold().split() or [""])[0]
            if (w in names or (first and w == first)) and addrs(c):
                hits.append(c)
        return len(hits) == 1

    @staticmethod
    def target(tool, args) -> str | None:
        """The real number / address a send or call tool will use (Tool.target), or None when it declares none.
        A tool that declares one but cannot name it gets "" (never known)."""
        fn = getattr(tool, "target", None)
        if fn is None:
            return None
        try:
            return str(fn(args) or "")
        except Exception:
            return ""

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
        if route == PHONE_SCREEN:                         # a read-only display on the owner's phone (see the class docstring)
            if tool.permission == "read" and tool_kind(tool.name, tool.permission, args,
                                                       untrusted_output=bool(tool.untrusted_output),
                                                       builtin=trusted_origin(tool)) in PHONE_SCREEN_KINDS:
                return None
            logs.warn("drive", "DRIVE_PHONE_REFUSED", "a phone screen request may only read; refused", tool=tool.name)
            return ToolResult(False, say("DRIVE_PHONE_READ_ONLY"), {"drive": "refused"}, code="DRIVE_REFUSED")
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
        if tool.name == BRIEFING_TOOL and trusted_origin(tool):
            # #324: the pre-drive briefing is a long read that would be deferred; refuse it WITHOUT queueing a junk
            # "when you stop" item. It is asked for before drive mode is turned on (the phone screen route is separate).
            logs.info("drive", "DRIVE_BRIEFING_REFUSED", "pre-drive briefing refused while drive mode is on; not queued",
                      tool=tool.name, source=source)
            return ToolResult(False, say("DRIVE_BRIEFING_FIRST") + " Tell the user in one short sentence; do not retry it "
                              "and do not use another tool for it.", {"drive": "refused"}, code="DRIVE_REFUSED")
        kind = tool_kind(tool.name, tool.permission, args, untrusted_output=bool(tool.untrusted_output),
                         builtin=trusted_origin(tool))
        who = self.recipient(tool, args) if kind in VOICE_CONFIRMED else ""
        v = decide(kind, active=True, known_contact=self.known(who, self.target(tool, args)), source=source, by_voice=by_voice)
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
