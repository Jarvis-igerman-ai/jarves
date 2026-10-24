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

from . import clilang
from . import features as feat
from . import hebrew

FEATURE = "drive_mode"
MAX_QUEUE = 20
MAX_TEXT = 200
MAX_SPOKEN_CHARS = 280          # a drive-mode answer is read aloud: keep it short
QUEUE_TTL_S = 24 * 3600         # a deferred item older than a day is dropped, not offered

ALWAYS = frozenset({"emergency_call", "hang_up", "stop", "cancel"})
ALLOWED = frozenset({"answer", "time", "weather", "calendar_read", "timer", "reminder", "music", "navigate_link"})
VOICE_CONFIRMED = frozenset({"call", "message_send"})      # allowed only for a known contact, from the owner's own words
UNTRUSTED = ("email", "web", "message", "recalled", "other")


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
    if action in ALWAYS:
        return Verdict("allow", "DRIVE_ALWAYS", always=True)          # even with drive mode off or the queue full
    if not active:
        return Verdict("allow", "DRIVE_OFF")
    if action in ALLOWED:
        # a navigation link is opened by the owner's tap or approval, never by this module
        return Verdict("allow", "DRIVE_OK", needs_approval=(action == "navigate_link"))
    if action in VOICE_CONFIRMED:
        if source in UNTRUSTED:
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
        rows = [d for d in data if isinstance(d, dict) and {"id", "action", "text", "ts"} <= set(d)] \
            if isinstance(data, list) else []
        return [r for r in rows if now - float(r["ts"]) <= QUEUE_TTL_S]

    def _write(self, rows: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False)
        os.replace(tmp, self.path)

    @staticmethod
    def _item(r: dict) -> QueueItem:
        return QueueItem(int(r["id"]), str(r["action"]), str(r["text"]), float(r["ts"]), str(r.get("source", "owner")))

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
            row = {"id": max([int(r["id"]) for r in rows] + [0]) + 1, "action": str(action)[:40],
                   "text": " ".join(str(text).split())[:MAX_TEXT], "ts": now, "source": str(source)[:20]}
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
EVIDENCE = "UNIT test_v6_drive_mode.py (pure policy and queue; not wired into approvals or a car yet)"
BLOCKER = "not wired into approvals/SkillRegistry yet (V6 item 9) and no parked-car session"
ABOUT = ("a driving policy: short answers, calls and messages only to known contacts with one spoken confirmation, "
         "everything else waits in a 'when you stop' list; emergencies and hang-up are never blocked")
PI = "no vehicle path on the Raspberry Pi in V6 (GitHub issue #209 tracks the PC module)"
hebrew.TEXT.update({
    ABOUT: "מדיניות נהיגה: תשובות קצרות, שיחות והודעות רק לאנשי קשר מוכרים עם אישור קולי אחד, וכל השאר ממתין ברשימה "
           "\"כשתעצרו\"; חירום וניתוק אף פעם לא נחסמים",
    EVIDENCE: "בדיקות יחידה test_v6_drive_mode.py (מדיניות ותור טהורים; עדיין לא מחוברים לאישורים או לרכב)",
    BLOCKER: "עוד לא מחובר לאישורים/SkillRegistry (V6 פריט 9) ואין בדיקה ברכב חונה",
    PI: "אין נתיב רכב על ה-Raspberry Pi ב-V6 (נושא #209 עוקב אחר מודול ה-PC)",
})

feat.register(FEATURE, "מצב נהיגה", "Drive mode", False, risk="low", about=ABOUT,
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["read", "queue of deferred actions; approvals unchanged"],
              settings="jarvis drive on|off|status|queue|stopped")
