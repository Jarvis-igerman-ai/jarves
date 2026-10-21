"""ONE approvals service (V3, Issue #17): every "JARVES needs the owner's approval" goes through here.

Callers: SkillRegistry.confirm (destructive tools, tool `confirm` prompts, Telegram/Instagram sends...), feature_set
for medium/high-risk switches, the camera switch on the face, scheduler/autonomy/guides confirmations, and the Helper
Skill (shell commands and installs). The Core installs `Core.ask_confirm` -> `ApprovalService.ask`.

Where the owner sees a request (all at once; the first answer wins, the others close):
- popup      a small always-on-top native window (approval_popup.py, Tk, own process) even when the face is hidden
             and the Core runs in the background; closing it = Deny. Only on a desktop session of a real run.
- tray       a notification balloon pointing at the popup / dashboard (the tray menu opens the dashboard).
- face       the existing Godot confirm dialog (confirm.request / confirm.reply bound to the asked face client).
- dashboard  the Approvals list (GET /api/approvals, banner on every page), in-process or from another process
             through <home>/approvals/pending.json + answers/<id>.json.
- voice      a pure yes / no utterance of the OWNER ("כן", "אישור", "approve" / "לא", "בטל") heard in the live
             conversation, typed on the face, or spoken with push-to-talk while exactly one request waits.
- CLI        `jarvis approvals list|approve|deny|history`.
Rules (enforced here, never by prompt text):
- default DENY: timeout (DEFAULT_TIMEOUT_S), shutdown, cancellation and "nobody could be asked" all deny.
- no approval from model-controlled text: `answer()` refuses while a Skill tool runs (IN_TOOL), there is no tool
  that answers, and voice answers come only from the owner's own transcript (role "user"), never from tool
  results, mail, chat messages or JARVES's speech. Typed-confirmation requests (dangerous shell commands) cannot be
  approved by voice or the face at all: only by typing the shown code in the popup, dashboard or CLI.
- dedupe: an identical request already waiting shares its answer; the same request denied less than
  DENY_COOLDOWN_S ago is denied again without asking; more than RATE_LIMIT new requests per window are denied.
- audit: every decision is appended to <home>/approvals/audit.jsonl (bounded, rotated once).

Interface:
    svc = approvals.ApprovalService(home, timeout_s=60)
    svc.channels.append(channel)                 # objects with .name, .available(a), .open(a), .close(a)
    ok = await svc.ask(prompt, title=..., why=..., risk="high", kind="shell", details={...}, typed=True)
    svc.answer(id, True, channel="dashboard", typed="1234") -> (ok, why)
    svc.answer_words("כן", channel="voice") -> bool    # True = it answered a waiting request
    approvals.attach(svc) / approvals.current()
    approvals.audited(home, confirm_fn, channel="terminal")   # terminal-only sessions (jarvis execute, CLI tools)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import approval_keys as akeys
from . import logs

DEFAULT_TIMEOUT_S = 60.0
MAX_TIMEOUT_S = 600.0
RATE_LIMIT = (6, 60.0)           # at most 6 NEW requests per 60 s; more are denied without asking
DENY_COOLDOWN_S = 30.0           # an identical request denied by the owner is not asked again for 30 s, unless the
                                 # owner spoke / typed since (note_user_turn): a model retry loop cannot nag
DASHBOARD_FRESH_S = 15.0         # a dashboard that polled within this time counts as a place to answer
VOICE_MIN_AGE_S = 1.5            # speech transcribed sooner than this after the request is not an answer to it
POLL_S = 0.4                    # answers written by another process (dashboard, CLI)
AUDIT_MAX_BYTES = 1_000_000
RISKS = ("low", "medium", "high")
KINDS = ("tool", "feature", "camera", "message", "shell", "install", "schedule", "call", "other")
HASHED_DETAILS = ("number",)       # SW-V4 H: a phone number is never written to the audit file, only a keyed hash
CHANNELS = ("popup", "tray", "face", "dashboard", "voice", "typed", "cli", "terminal")
FILE_CHANNELS = ("dashboard", "cli")          # what an answers/<id>.json file may claim to be

# Owner words (whole utterance; punctuation ignored). Mixed yes+no or anything else = not an answer.
YES_WORDS = frozenset({"כן", "אישור", "מאשר", "מאשרת", "אשר", "אשרי", "בטח", "approve", "approved", "yes", "yep",
                       "confirm", "confirmed", "ok", "okay", "אוקיי"})
NO_WORDS = frozenset({"לא", "ביטול", "בטל", "בטלי", "אל", "תבטל", "deny", "denied", "no", "cancel", "nope", "stop"})
FILLER = frozenset({"אני", "בבקשה", "תודה", "כן,", "please", "i", "do", "it", "thanks", "את", "זה"})
_PUNCT = re.compile(r"[.,!?;:\"'()\[\]{}\-–—…״׳]+")

HE = {   # Hebrew texts of this module (fixed strings; user content is never translated)
    "no_channel": "אין מסך, חלון או לוח בקרה פתוח שיכול לאשר: לא בוצע.",
    "timeout": "לא התקבל אישור בזמן: לא בוצע.",
    "rate": "יותר מדי בקשות אישור ברגע: לא בוצע. נסה שוב בעוד דקה.",
    "cooldown": "בדיוק סירבת לבקשה הזאת: לא שואל שוב עכשיו.",
    "typed": "צריך להקליד את הקוד שמוצג בחלון האישור או בלוח הבקרה.",
    "voice_typed": "את זה אפשר לאשר רק בהקלדת הקוד על המסך, לא בקול.",
}


# Spoken approval questions name ONLY the kind of action (owner decision 20 Oct): never message text, commands,
# addresses or other details. The full text stays on the popup / dashboard (Approval.prompt, unchanged).
# (regex on the tool name/title, English question, Hebrew question, may name the recipient)
_SPOKEN_KINDS = (
    (r"self_connect|self_manage_connect", "Open the connection window for an account?", "לפתוח חלון לחיבור חשבון?", False),
    (r"self_undo|self_manage_undo", "Undo my last change to myself?", "לבטל את השינוי האחרון שעשיתי בעצמי?", False),
    (r"self_change|self_manage", "Change one of my own files, features or settings?",
     "לשנות אחד מהקבצים, היכולות או ההגדרות שלי?", False),
    (r"dial_open|\bcall\b", "Open the phone dialer{to}?", "לפתוח את החייגן{to}?", True),
    (r"whatsapp", "Send a WhatsApp message{to}?", "לשלוח הודעת וואטסאפ{to}?", True),
    (r"telegram", "Send a Telegram message{to}?", "לשלוח הודעת טלגרם{to}?", True),
    (r"instagram", "Send an Instagram message{to}?", "לשלוח הודעה באינסטגרם{to}?", True),
    (r"gmail|mail", "Send an e-mail{to}?", "לשלוח מייל{to}?", True),
    (r"calendar_(create|update|share)|calendar", "Change your calendar?", "לשנות את היומן?", False),
    (r"shell|command", "Run a command on this computer?", "להריץ פקודה במחשב?", False),
    (r"install", "Install software on this computer?", "להתקין תוכנה במחשב?", False),
    (r"camera", "Turn the camera on?", "להפעיל את המצלמה?", False),
    (r"feature|profile", "Change a setting?", "לשנות הגדרה?", False),
    (r"home_control|lock|door", "Control a smart-home device?", "לשלוט במכשיר בבית החכם?", False),
    (r"delete|forget|clear|remove|erase", "Delete something?", "למחוק משהו?", False),
    (r"schedule|job", "Run a scheduled job?", "להפעיל משימה מתוזמנת?", False),
)


def first_name(recipient: Any) -> str:
    """FIRST NAME only of a recipient label ('Dana Levi <d@x.com>' -> 'Dana'); an address/number alone gives ''."""
    s = re.sub(r"<[^>]*>|\([^)]*\)", " ", str(recipient or "")).strip()
    for tok in s.replace(",", " ").split():
        tok = tok.strip("\"'.;:")
        if re.match(r"[A-Za-z֐-׿]", tok) and "@" not in tok:
            return tok[:30]
        break
    return ""


def spoken_kind(title: str, kind: str, details: dict | None = None) -> tuple[str, str]:
    """(English, Hebrew) question that may be SPOKEN aloud. Names the kind of action (and at most a short recipient
    name when the caller supplied details['recipient']); never the message text, command or other details."""
    # the approval's own kind wins for the system kinds (a feature titled "Turn on Send e-mail" is a setting change)
    hay = kind.lower() if kind in ("feature", "shell", "install", "camera", "schedule") else f"{title} {kind}".lower()
    who = first_name((details or {}).get("recipient"))
    he_to = ""
    if who:
        he_to = f" ל{who}" if re.match(r"[א-ת]", who) else f" אל {who}"     # ל prefix on a Hebrew name
    for rx, en, he, named in _SPOKEN_KINDS:
        if re.search(rx, hay):
            return (en.format(to=f" to {who}" if (named and who) else ""),
                    he.format(to=he_to if (named and who) else ""))
    if kind == "message":
        return ("Send a message?", "לשלוח הודעה?")
    return ("Do something that needs your approval?", "לבצע פעולה שדורשת את האישור שלך?")


class ApprovalRefused(Exception):
    pass


@dataclass
class Approval:
    id: str
    title: str
    prompt: str
    why: str
    risk: str
    kind: str
    source: str
    details: dict
    typed_code: str
    voice_ok: bool
    created: float
    expires: float
    key: str = ""
    state: str = "pending"            # pending | approved | denied | timeout | cancelled
    channel: str = ""
    decided_at: float = 0.0
    shown: list = field(default_factory=list)
    typed_salt: str = ""              # R-01: pending.json keeps salt + keyed hash + sealed code, never the code
    typed_hash: str = ""

    @property
    def spoken(self) -> str:
        """What may be said aloud: the kind of action only, ONE language (English). Not the prompt (that has details)."""
        return self.spoken_in("en")

    def spoken_in(self, lang: str = "en") -> str:
        """The spoken question in ONE language: Hebrew only when lang is 'he', English otherwise."""
        en, he = spoken_kind(self.title, self.kind, self.details)
        return he if str(lang or "").lower().startswith("he") else en

    def public(self) -> dict:
        """In-process view (the Core's own dashboard routes, popup): includes the code to SHOW to the owner."""
        d = asdict(self)
        for k in ("key", "typed_salt", "typed_hash"):
            d.pop(k, None)
        d["typed"] = bool(self.typed_code)
        d["seconds_left"] = max(0, round(self.expires - time.time()))
        return d

    def file_row(self, key: bytes | None) -> dict:
        """What pending.json holds (read by other processes): no plain code. A typed request carries the salt, the
        keyed hash (to check a typed code) and the code sealed with the profile key (so the owner's dashboard / CLI,
        which can read the key file, can still show it)."""
        d = self.public()
        d.pop("typed_code", None)
        if self.typed_code:
            d["typed_salt"], d["typed_hash"] = self.typed_salt, self.typed_hash
            d["typed_sealed"] = akeys.seal(key, self.typed_salt, self.typed_code) if key else ""
        return d

    def code_ok(self, key: bytes | None, typed: str) -> bool:
        if not self.typed_code:
            return True
        if key and self.typed_hash:
            return akeys.code_matches(key, self.typed_salt, self.typed_hash, typed)
        return hmac.compare_digest(str(typed or "").strip().encode(), self.typed_code.encode())


def _norm_words(text: str) -> list[str]:
    return [w for w in _PUNCT.sub(" ", (text or "").lower()).split() if w]


def classify_words(text: str) -> bool | None:
    """True = a pure yes, False = a pure no, None = not an answer (a sentence, a question, mixed words)."""
    words = _norm_words(text)
    if not words or len(words) > 5:
        return None
    yes = [w for w in words if w in YES_WORDS]
    no = [w for w in words if w in NO_WORDS]
    rest = [w for w in words if w not in YES_WORDS and w not in NO_WORDS and w not in FILLER]
    if rest or (yes and no) or not (yes or no):
        return None
    return bool(yes)


def _in_tool() -> str:
    try:
        from .skills import IN_TOOL
    except Exception:
        return ""
    return IN_TOOL.get() or ""


class ApprovalService:
    def __init__(self, home: Path, *, timeout_s: float = DEFAULT_TIMEOUT_S, rate_limit: tuple[int, float] = RATE_LIMIT,
                 clock: Callable[[], float] = time.time) -> None:
        self.home = Path(home)
        self.dir = self.home / "approvals"
        self.timeout_s = float(timeout_s)
        self.rate_limit = rate_limit
        self.clock = clock
        self.channels: list[Any] = []
        self._pending: dict[str, tuple[Approval, asyncio.Future, asyncio.AbstractEventLoop]] = {}
        self._lock = threading.RLock()
        self._recent: list[float] = []
        self._denied: dict[str, float] = {}
        self._poller: asyncio.Task | None = None
        self.listeners: list[Callable[[Approval], None]] = []    # after every decision (tests, runtime)
        self.on_ask: list[Callable[[Approval], None]] = []       # when a request starts waiting (tests, runtime)
        self.closed = False
        self.loop: asyncio.AbstractEventLoop | None = None       # the Core's loop (set by Core.start): see ask_owner
        # SW-V4 F: details["grant"] of requests the OWNER approved -> decision time. A caller (the WhatsApp send) may
        # spend a grant once to get a signed, single-use approval id the bridge checks (take_grant).
        self._grants: dict[str, float] = {}
        # R-01: the profile key, held in memory. Answer files must be signed with it; typed codes are stored hashed
        # and sealed with it. A service that is not the Core (dashboard fallback, tests) uses the existing key.
        self._key: bytes | None = akeys.load_or_create(self.home)
        self._quiet: set[str] = set()                             # file-answer problems already logged once
        self._nonces: dict[str, float] = {}
        if self._key is None:
            logs.warn("approvals", "APPROVAL_KEY_UNAVAILABLE", "no private approval key: answers from other processes "
                      "(dashboard / CLI files) are refused")

    def rotate_key(self) -> bool:
        """Core start: a fresh key (the old one, and every answer file signed with it, stops counting). Old answer
        files (unsigned ones from before this version included) are removed and pending.json is rewritten without
        any plain code."""
        try:
            self._key = akeys.rotate(self.home)
        except OSError as e:
            self._key = None
            logs.warn("approvals", "APPROVAL_KEY_UNAVAILABLE", "could not create a private approval key: answers "
                      "from other processes are refused", error_type=type(e).__name__)
            return False
        adir = self.dir / "answers"
        try:
            stale = list(adir.glob("*.json")) if adir.is_dir() else []
        except OSError:
            stale = []
        for p in stale:
            try:
                p.unlink()
            except OSError:
                pass
        self._write_pending()
        logs.info("approvals", "APPROVAL_KEY_ROTATED", "new approval key for this run", count=len(stale))
        return True

    def _quiet_reject(self, reason: str, path: Path) -> None:
        """Ignored answer file: logged once per reason and run, quietly (never the content)."""
        if reason in self._quiet:
            logs.debug("approvals", "APPROVAL_FILE_IGNORED", "ignored an answer file", reason=reason,
                       file=path.name[:40])
            return
        self._quiet.add(reason)
        logs.info("approvals", "APPROVAL_FILE_IGNORED", "ignored an answer file (logged once per reason)",
                  reason=reason, file=path.name[:40])

    # ------------------------------------------------------------------ asking
    async def ask(self, prompt: str, *, title: str = "", why: str = "", risk: str = "medium", kind: str = "tool",
                  details: dict | None = None, typed: bool = False, timeout: float | None = None,
                  source: str = "model", voice: bool | None = None) -> bool:
        """Ask the owner; True only on an explicit approval. Everything else (timeout, no channel, shutdown,
        cancellation, rate limit) is False."""
        risk = risk if risk in RISKS else "high"
        kind = kind if kind in KINDS else "other"
        details = {str(k): v for k, v in (details or {}).items()}
        prompt = str(prompt or "").strip()[:2000] or "Approve?"
        title = (title or prompt.splitlines()[0])[:120]
        key = hashlib.sha256(json.dumps([kind, prompt, details], sort_keys=True, ensure_ascii=False,
                                        default=str).encode()).hexdigest()[:24]
        loop = asyncio.get_running_loop()
        now = self.clock()
        if self.closed:
            self._audit_simple(title, prompt, kind, risk, source, details, "denied", "shutdown")
            return False
        with self._lock:
            for a, fut, _ in self._pending.values():
                if a.key == key and not fut.done():      # identical request already waiting: one prompt, one answer
                    logs.info("approvals", "APPROVAL_DEDUPED", "same request already waiting", id=a.id, kind=kind)
                    shared = fut
                    break
            else:
                shared = None
        if shared is not None:
            return bool(await asyncio.shield(shared))
        if source == "model" and now - self._denied.get(key, -1e9) < DENY_COOLDOWN_S:   # a button press is a new ask
            self._audit_simple(title, prompt, kind, risk, source, details, "denied", "cooldown")
            logs.info("approvals", "APPROVAL_COOLDOWN", "identical request was just denied; not asking again", kind=kind)
            return False
        limit, window = self.rate_limit
        self._recent = [t for t in self._recent if now - t < window]
        if len(self._recent) >= limit:
            self._audit_simple(title, prompt, kind, risk, source, details, "denied", "rate_limited")
            logs.warn("approvals", "APPROVAL_RATE_LIMITED", "too many approval requests; denied", limit=limit,
                      window_s=window, kind=kind)
            return False
        self._recent.append(now)
        t = min(MAX_TIMEOUT_S, max(1.0, float(timeout if timeout is not None else self.timeout_s)))
        a = Approval(uuid.uuid4().hex[:16], title, prompt, str(why or "")[:600], risk, kind, source, details,
                     f"{secrets.randbelow(9000) + 1000}" if typed else "",
                     (not typed) if voice is None else (bool(voice) and not typed), now, now + t, key)
        if a.typed_code:
            a.typed_salt = secrets.token_hex(8)
            a.typed_hash = akeys.code_hash(self._key, a.typed_salt, a.typed_code) if self._key else ""
        fut = loop.create_future()
        with self._lock:
            self._pending[a.id] = (a, fut, loop)
        self._write_pending()
        opened = []
        for ch in list(self.channels):
            try:
                if not ch.available(a):
                    continue
                r = ch.open(a)
                if asyncio.iscoroutine(r):
                    await r
                opened.append(ch)
                a.shown.append(ch.name)
            except Exception:
                logs.error("approvals", "APPROVAL_CHANNEL_FAILED", "approval channel failed", channel=str(getattr(ch, "name", ch)),
                           exc=True)
        if self.dashboard_watching():
            a.shown.append("dashboard")
        logs.info("approvals", "APPROVAL_ASKED", "approval requested", id=a.id, kind=kind, risk=risk,
                  shown=a.shown, typed=bool(a.typed_code), timeout_s=t)
        if not a.shown:
            self._resolve(a.id, False, "no_channel")
        else:
            self._ensure_poller(loop)
            for fn in list(self.on_ask):
                try:
                    fn(a)
                except Exception:
                    logs.error("approvals", "APPROVAL_LISTENER_FAILED", "approval listener failed", exc=True)
        try:
            return bool(await asyncio.wait_for(asyncio.shield(fut), max(0.0, a.expires - self.clock())))
        except (TimeoutError, asyncio.TimeoutError):
            self._resolve(a.id, False, "timeout")
            return False
        except asyncio.CancelledError:
            self._resolve(a.id, False, "cancelled")
            raise
        finally:
            with self._lock:
                self._pending.pop(a.id, None)
            self._write_pending()
            for ch in opened:
                try:
                    r = ch.close(a)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    logs.warn("approvals", "APPROVAL_CLOSE_FAILED", "could not close an approval channel",
                          channel=str(getattr(ch, "name", ch)))

    # ------------------------------------------------------------------ answering
    def answer(self, aid: str, approved: bool, *, channel: str, typed: str = "") -> tuple[bool, str]:
        """An owner's answer from a real channel. Thread-safe (dashboard threads, popup watcher threads)."""
        tool = _in_tool()
        if tool:
            logs.warn("approvals", "APPROVAL_FROM_TOOL", "refused: an answer came from inside a tool", tool=tool)
            return False, "approvals cannot be answered from a tool"
        if channel not in CHANNELS:
            return False, f"unknown channel {channel!r}"
        with self._lock:
            entry = self._pending.get(str(aid))
        if entry is None:
            return False, "no such waiting approval (already answered or expired)"
        a, fut, loop = entry
        if approved is True:
            if a.typed_code and not a.code_ok(self._key, typed):
                return False, HE["typed"] + " / Type the code shown to approve."
            if channel == "voice" and not a.voice_ok:
                return False, HE["voice_typed"]
            if channel == "face" and a.typed_code:
                return False, HE["typed"]
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._resolve(a.id, approved is True, channel)
        else:
            loop.call_soon_threadsafe(self._resolve, a.id, approved is True, channel)
        return True, ""

    def answer_words(self, text: str, *, channel: str = "voice") -> bool:
        """The owner said / typed something while a request waits: a pure yes/no answers the ONE waiting request
        that allows it. Anything else (or two waiting requests) is not an answer and goes on as conversation."""
        verdict = classify_words(text)
        if verdict is None:
            return False
        with self._lock:
            waiting = [a for a, fut, _ in self._pending.values() if not fut.done()]
        if len(waiting) != 1:
            if waiting:
                logs.info("approvals", "APPROVAL_VOICE_AMBIGUOUS", "several approvals wait; answer on the screen",
                          waiting=len(waiting))
            return False
        a = waiting[0]
        if channel == "voice" and self.clock() - a.created < VOICE_MIN_AGE_S:
            # a transcript finishing just after the request appeared belongs to what he said BEFORE it
            logs.info("approvals", "APPROVAL_VOICE_TOO_EARLY", "speech older than the request ignored", id=a.id)
            return False
        if verdict and not a.voice_ok:
            logs.info("approvals", "APPROVAL_VOICE_REFUSED", "this approval is answered on the screen only",
                      typed=bool(a.typed_code), id=a.id)
            return False
        ok, _ = self.answer(a.id, verdict, channel=channel)
        return ok

    def _resolve(self, aid: str, approved: bool, channel: str) -> None:
        with self._lock:
            entry = self._pending.get(aid)
        if entry is None:
            return
        a, fut, _ = entry
        if fut.done():
            return
        # approved | denied (an owner said no) | timeout | cancelled | no_channel (nobody could be asked)
        a.state = "approved" if approved else ("denied" if channel in CHANNELS else channel)
        a.channel, a.decided_at = channel, self.clock()
        if not approved and channel in CHANNELS:
            self._denied[a.key] = a.decided_at
        if approved and channel in CHANNELS and isinstance(a.details.get("grant"), str):
            self._grants[a.details["grant"]] = a.decided_at
        fut.set_result(bool(approved))
        self._audit(a)
        logs.info("approvals", "APPROVAL_DECIDED", "approval decided", id=a.id, kind=a.kind,
                  risk=a.risk, decision=a.state, channel=channel)
        for fn in list(self.listeners):
            try:
                fn(a)
            except Exception:
                logs.error("approvals", "APPROVAL_LISTENER_FAILED", "approval listener failed", exc=True)

    def take_grant(self, grant: str, max_age_s: float = 120.0) -> bool:
        """True ONCE for a grant the owner approved on a real channel less than max_age_s ago (SW-V4 F: the
        WhatsApp approval id is minted only after this). Never from inside a tool's own approval answer path."""
        with self._lock:
            now = self.clock()
            self._grants = {g: t for g, t in self._grants.items() if now - t < max_age_s}
            return self._grants.pop(str(grant), None) is not None

    # ------------------------------------------------------------------ views
    def note_user_turn(self) -> None:
        """The owner said / typed something new (not an approval answer): a request he denied may be asked again
        (he may have changed his mind). Without a new turn, a repeat of a denied request is denied at once."""
        self._denied.clear()

    def has_pending(self) -> bool:
        with self._lock:
            return any(not f.done() for _, f, _ in self._pending.values())

    def pending(self) -> list[dict]:
        with self._lock:
            return [a.public() for a, f, _ in self._pending.values() if not f.done()]

    def history(self, limit: int = 50) -> list[dict]:
        return read_history(self.home, limit)

    def dashboard_watching(self) -> bool:
        try:
            return time.time() - (self.dir / "dashboard_seen").stat().st_mtime < DASHBOARD_FRESH_S
        except OSError:
            return False

    def close(self) -> None:
        """Shutdown: every waiting request is denied (never approved by a shutdown)."""
        self.closed = True
        with self._lock:
            ids = [aid for aid, (_, f, _) in self._pending.items() if not f.done()]
        for aid in ids:
            with self._lock:
                entry = self._pending.get(aid)
            if entry is None:
                continue
            _, _, loop = entry
            try:
                if asyncio.get_running_loop() is loop:
                    self._resolve(aid, False, "cancelled")
                    continue
            except RuntimeError:
                pass
            loop.call_soon_threadsafe(self._resolve, aid, False, "cancelled")

    # ------------------------------------------------------------------ files (other processes)
    def _write_pending(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / f"pending.{os.getpid()}.tmp"
            with self._lock:
                rows = [a.file_row(self._key) for a, f, _ in self._pending.values() if not f.done()]
            tmp.write_text(json.dumps({"pid": os.getpid(), "at": time.time(), "pending": rows, "signed": True},
                                      ensure_ascii=False), encoding="utf-8")
            for i in range(8):                       # Windows: a reader holding the file for a moment refuses the swap
                try:
                    os.replace(tmp, self.dir / "pending.json")
                    break
                except PermissionError:
                    if i == 7:
                        raise
                    time.sleep(0.02)
        except OSError:
            logs.warn("approvals", "APPROVAL_FILE_FAILED", "could not write approvals/pending.json")

    def _ensure_poller(self, loop) -> None:
        if self._poller is None or self._poller.done():
            self._poller = loop.create_task(self._poll_answers())

    async def _poll_answers(self) -> None:
        """Answers from another process (dashboard, CLI): runs only while something waits."""
        adir = self.dir / "answers"
        # The poller task is created by the first ask(); when that ask came from INSIDE a tool (shell_propose,
        # install_propose) the task inherited IN_TOOL and refused every file answer (CLI, other-process dashboard)
        # with APPROVAL_FROM_TOOL (campaign 20 Oct, B-05). Answers read here come from files written by the owner's
        # other processes, never from this tool: run with a clean marker.
        from .skills import IN_TOOL
        IN_TOOL.set("")
        while self.has_pending():
            try:
                files = list(adir.glob("*.json")) if adir.is_dir() else []
            except OSError:
                files = []
            for p in files:
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    d = None
                try:
                    p.unlink()
                except OSError:
                    pass
                if not isinstance(d, dict) or d.get("channel") not in FILE_CHANNELS:
                    self._quiet_reject("not an answer", p)
                    continue
                why = self._file_problem(d, p)
                if why:
                    self._quiet_reject(why, p)
                    continue
                ok, why = self.answer(str(d.get("id")), d.get("approved") is True, channel=d["channel"],
                                      typed=str(d.get("typed") or ""))
                if not ok:
                    logs.info("approvals", "APPROVAL_FILE_ANSWER_REJECTED", "file answer rejected", why=why[:120], channel=d["channel"])
            await asyncio.sleep(POLL_S)

    def _file_problem(self, d: dict, p: Path) -> str:
        """Why an answer file does not count ('' = it does): it must be signed with this run's key, name the file's
        own id, be fresh and not be a replay."""
        if "sig" not in d:
            return "unsigned"
        if not akeys.verify(self._key, d):
            return "bad signature"
        if f"{d.get('id')}.json" != p.name:
            return "id does not match the file"
        try:
            ts = float(d.get("ts"))
        except (TypeError, ValueError):
            return "no time"
        now = time.time()
        if not (now - MAX_TIMEOUT_S - 60 <= ts <= now + 60):
            return "stale"
        nonce = str(d.get("nonce") or "")
        self._nonces = {n: t for n, t in self._nonces.items() if now - t < MAX_TIMEOUT_S + 120}
        if not nonce or nonce in self._nonces:
            return "replayed"
        self._nonces[nonce] = now
        return ""

    # ------------------------------------------------------------------ audit
    def _hashed(self, value: Any) -> str:
        """Keyed hash of a detail that must not be written down (a phone number); '<hidden>' without a key."""
        if not self._key:
            return "<hidden>"
        return "hmac:" + hmac.new(self._key, str(value).encode(), hashlib.sha256).hexdigest()[:16]

    def _audit(self, a: Approval) -> None:
        self._audit_simple(a.title, a.prompt, a.kind, a.risk, a.source, a.details, a.state, a.channel, a.id,
                           a.decided_at - a.created, a.shown)

    def _audit_simple(self, title, prompt, kind, risk, source, details, decision, channel, aid="", waited=0.0,
                      shown=None) -> None:
        private = logs.privacy()
        if isinstance((details or {}).get("grant"), str):
            # SW-V4 F: a WhatsApp send is audited with hashes only in BOTH modes (no recipient, number or text)
            private = True
            details = {"grant": details["grant"], "chars": len(str(details.get("text") or ""))}
        rec = {"ts": round(time.time(), 3), "id": aid, "kind": kind, "risk": risk, "source": source,
               "title": logs.redact(str(title))[:200], "decision": decision, "channel": channel,
               "waited_s": round(max(0.0, waited), 1), "shown": list(shown or []),
               "prompt": f"<{len(prompt)} chars>" if (private and kind not in ("shell", "install")) or kind == "call" else
               logs.redact(str(prompt))[:600],
               "details": {k: (self._hashed(v) if k in HASHED_DETAILS else logs.redact(str(v))[:600])
                           for k, v in (details or {}).items() if not logs.is_credential(k)}}
        append_audit(self.dir / "audit.jsonl", rec)


# ---------------------------------------------------------------------- module helpers
def _private_audit(rec: dict) -> dict:
    """SW-V4 B: in privacy mode the audit keeps who/what kind/when/decision, not the words: the title (it can name
    the recipient) and the detail values are masked by the field registry (an unregistered key is content).
    Shell / install commands stay readable (redacted): they are the security record of what ran."""
    if not logs.privacy() or rec.get("kind") in ("shell", "install"):
        return rec
    title = rec.get("title")
    return {**rec, "title": title if not title else logs._mask(title),
            "details": {k: logs.render_field(k, v, private=True) for k, v in (rec.get("details") or {}).items()}}


def append_audit(path: Path, rec: dict) -> None:
    rec = logs.redact_deep(_private_audit(rec))     # no secret in any field, whatever the mode
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > AUDIT_MAX_BYTES:
            os.replace(path, path.with_suffix(".1.jsonl"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        logs.warn("approvals", "AUDIT_WRITE_FAILED", "could not append to the audit file", file=path.name)


def read_history(home: Path, limit: int = 50) -> list[dict]:
    path = Path(home) / "approvals" / "audit.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _write_json(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{secrets.token_hex(3)}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_pending_file(home: Path, *, reveal: bool = True) -> list[dict]:
    """The waiting requests of the running Core (for a dashboard / CLI in another process). pending.json holds no
    plain code; with reveal (the owner's own dashboard / CLI) the code is unsealed with the profile key to SHOW it.
    A row whose sealed code does not check out against its hash shows no code (it cannot be approved by typing)."""
    try:
        d = json.loads((Path(home) / "approvals" / "pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = d.get("pending") if isinstance(d, dict) else None
    now = time.time()
    out = [dict(r) for r in rows or [] if isinstance(r, dict) and float(r.get("expires") or 0) > now]
    key = akeys.load(home) if reveal and any(r.get("typed") for r in out) else None
    for r in out:
        r.pop("typed_code", None)                    # a plain code from an older file is never trusted or shown
        if r.get("typed") and reveal:
            code = akeys.unseal(key, str(r.get("typed_salt") or ""), str(r.get("typed_sealed") or ""))
            ok = code and akeys.code_matches(key, str(r.get("typed_salt") or ""), str(r.get("typed_hash") or ""), code)
            r["typed_code"] = code if ok else ""
    return out


def submit_answer(home: Path, aid: str, approved: bool, *, channel: str, typed: str = "") -> tuple[bool, str]:
    """Answer from another process: in-process service when it serves this home, else the answers file, signed
    with the profile key (the Core ignores unsigned or wrongly signed files)."""
    if _in_tool():
        return False, "approvals cannot be answered from a tool"
    svc = _current
    if svc is not None and svc.home.resolve() == Path(home).resolve():
        return svc.answer(aid, approved, channel=channel, typed=typed)
    if channel not in FILE_CHANNELS:
        return False, f"channel {channel} cannot answer through a file"
    if not re.fullmatch(r"[0-9a-f]{16}", str(aid)):
        return False, "bad id"
    rows = {r["id"]: r for r in read_pending_file(home, reveal=False)}
    r = rows.get(str(aid))
    if r is None:
        return False, "no such waiting approval (already answered or expired)"
    key = akeys.load(home)
    if key is None:
        return False, "the approval key of the running JARVES is missing (restart JARVES)"
    if approved and r.get("typed") and not akeys.code_matches(key, str(r.get("typed_salt") or ""),
                                                              str(r.get("typed_hash") or ""), typed):
        return False, HE["typed"] + " / Type the code shown to approve."
    body = akeys.sign(key, {"id": aid, "approved": bool(approved), "typed": str(typed or "").strip(),
                            "channel": channel, "ts": round(time.time(), 3), "nonce": secrets.token_hex(12)})
    _write_json(Path(home) / "approvals" / "answers" / f"{aid}.json", body)
    return True, ""


def audited(home: Path, confirm: Callable[[str], Awaitable[bool]], *, channel: str = "terminal"):
    """Wrap a terminal-only confirm (jarvis execute, `jarvis calendar/mail`) so its decisions reach the same audit."""
    async def wrapped(prompt: str, **meta) -> bool:
        t0 = time.time()
        try:
            ok = bool(await confirm(prompt))
        except Exception:
            ok = False
        title = meta.get("title") or (prompt.splitlines()[0] if prompt else "")
        rec = {"ts": round(time.time(), 3), "id": "", "kind": meta.get("kind", "tool"),
               "risk": meta.get("risk", "medium"), "source": meta.get("source", "model"),
               "title": logs.redact(str(title))[:200],
               "decision": "approved" if ok else "denied", "channel": channel, "waited_s": round(time.time() - t0, 1),
               "shown": [channel], "prompt": f"<{len(prompt)} chars>" if logs.privacy() else logs.redact(prompt)[:600],
               "details": {}}
        append_audit(Path(home) / "approvals" / "audit.jsonl", rec)
        return ok
    wrapped.accepts_meta = True
    return wrapped


_current: ApprovalService | None = None


def attach(svc: ApprovalService) -> ApprovalService:
    global _current
    _current = svc
    return svc


def current() -> ApprovalService | None:
    return _current


def detach(svc: ApprovalService | None = None) -> None:
    global _current
    if svc is None or _current is svc:
        _current = None


def ask_owner(home: Path, prompt: str, *, title: str, why: str = "", kind: str = "tool", risk: str = "high",
              details: dict | None = None, timeout: float | None = None) -> tuple[bool, str]:
    """For a destructive DASHBOARD action (called from the HTTP handler thread, which blocks until the owner
    answers): the same ApprovalService the model tools use shows the popup / tray / face / dashboard banner; the
    request waits up to the popup timeout. (ok, reason): ok only on an explicit approval; a denial, timeout or
    nobody to ask is a refusal and the caller must not act. Without a running Core the request is asked by a
    service of its own (answerable from the dashboard banner or `jarvis approvals approve`)."""
    home = Path(home)
    kw = dict(title=title, why=why, kind=kind, risk=risk, details=details, source="dashboard", timeout=timeout)
    svc = _current
    try:
        if svc is not None and svc.loop is not None and svc.loop.is_running() and svc.home.resolve() == home.resolve():
            t = min(MAX_TIMEOUT_S, max(1.0, float(timeout if timeout is not None else svc.timeout_s)))
            ok = asyncio.run_coroutine_threadsafe(svc.ask(prompt, **kw), svc.loop).result(t + 15)
        else:
            import concurrent.futures as cf                    # own loop in a worker thread (also when called from a loop)
            with cf.ThreadPoolExecutor(1) as ex:
                ok = ex.submit(lambda: asyncio.run(ApprovalService(home).ask(prompt, **kw))).result(
                    MAX_TIMEOUT_S + 15)
    except Exception as e:
        logs.warn("approvals", "DASHBOARD_APPROVAL_FAILED", "dashboard approval failed", error_type=type(e).__name__)
        return False, "no approval"
    return (True, "") if ok else (False, "not approved")


APPROVAL_REFUSED = {"error": "not done: it needs your approval on the screen and was not approved",
                    "error_he": "לא בוצע: נדרש אישור שלך על המסך ולא התקבל", "needs_approval": True}


# ---------------------------------------------------------------------- the native popup channel
def desktop_available() -> tuple[bool, str]:
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False, "no desktop session"
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        return False, f"tkinter missing ({type(e).__name__})"
    return True, ""


def _popup_python() -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


class PopupChannel:
    """A small always-on-top window in its own process (approval_popup.py). Its result file is read only after
    the child exits; closing the window = Deny. `test_invoke` (tests only) makes the child press a button itself
    on an invisible window, so the real Tk code and the real decision path run without a person."""
    name = "popup"

    def __init__(self, svc: ApprovalService, *, enabled: bool = True, lang: str = "en",
                 test_invoke: str | None = None) -> None:
        self.svc, self.enabled, self.lang, self.test_invoke = svc, enabled, lang, test_invoke
        self.procs: dict[str, subprocess.Popen] = {}
        self.last_error = ""

    def available(self, a: Approval) -> bool:
        if not self.enabled:
            return False
        ok, why = desktop_available()
        self.last_error = why
        return ok

    def open(self, a: Approval) -> None:
        d = self.svc.dir / "popup"
        d.mkdir(parents=True, exist_ok=True)
        inp, out = d / f"{a.id}.in.json", d / f"{a.id}.out.json"
        out.unlink(missing_ok=True)
        # R-01: the typed code and a one-time nonce go to the window through its stdin pipe, never into a file;
        # its result file counts only when it echoes the nonce (a result file written by anything else is ignored)
        nonce = secrets.token_hex(16)
        _write_json(inp, {"id": a.id, "title": a.title, "prompt": a.prompt, "why": a.why, "risk": a.risk,
                          "kind": a.kind, "details": a.details, "typed": bool(a.typed_code),
                          "timeout_s": max(1, round(a.expires - time.time())), "lang": self.lang})
        argv = [_popup_python(), "-m", "jarves.approval_popup", "--in", str(inp), "--out", str(out), "--stdin"]
        if self.test_invoke:
            argv += ["--test-invoke", self.test_invoke]
        env = dict(os.environ)
        src = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             env=env, creationflags=flags)
        self.procs[a.id] = p
        secret = (json.dumps({"typed_code": a.typed_code, "nonce": nonce}) + "\n").encode("utf-8")
        threading.Thread(target=self._watch, args=(a.id, p, inp, out, nonce, secret),
                         name=f"approval-popup-{a.id[:6]}", daemon=True).start()

    def _watch(self, aid: str, p: subprocess.Popen, inp: Path, out: Path, nonce: str = "",
               secret: bytes | None = None) -> None:
        try:
            _, err = p.communicate(input=secret, timeout=MAX_TIMEOUT_S + 30)
        except subprocess.TimeoutExpired:
            p.kill()
            err = b""
        try:
            d = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        except (OSError, ValueError):
            d = None
        for f in (inp, out):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        self.procs.pop(aid, None)
        if isinstance(d, dict) and d.get("id") == aid and nonce and \
                not hmac.compare_digest(str(d.get("nonce") or "").encode(), nonce.encode()):
            logs.info("approvals", "APPROVAL_FILE_IGNORED", "ignored a popup result without the window's nonce", id=aid)
            d = None
        if isinstance(d, dict) and d.get("id") == aid and d.get("decision") in ("approve", "deny"):
            ok, why = self.svc.answer(aid, d["decision"] == "approve", channel="popup", typed=str(d.get("typed") or ""))
            if not ok and "no such" not in why:
                logs.warn("approvals", "APPROVAL_POPUP_REJECTED", "popup answer rejected", why=why[:120], id=aid)
        elif p.returncode not in (0, None, -15, 1):
            self.last_error = (err or b"").decode("utf-8", "replace")[-300:]
            logs.warn("approvals", "APPROVAL_POPUP_FAILED", "approval window failed", returncode=p.returncode,
                      detail=self.last_error[-160:])

    def close(self, a: Approval) -> None:
        p = self.procs.pop(a.id, None)
        if p is not None and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass


class TrayChannel:
    """Notification balloon from the tray icon: points the owner at the popup / dashboard (the tray menu has
    Dashboard). Counts as a place to answer only while the tray icon is really shown."""
    name = "tray"

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def available(self, a: Approval) -> bool:
        return getattr(self.runtime, "tray", None) is not None

    def open(self, a: Approval) -> None:
        self.runtime._notify(f"JARVES מבקש אישור / needs your approval: {a.title}"[:240])

    def close(self, a: Approval) -> None:
        pass


# ---------------------------------------------------------------------- dashboard
def api_approvals(ctx, m, body):
    home = Path(ctx.home)
    try:
        (home / "approvals").mkdir(parents=True, exist_ok=True)
        (home / "approvals" / "dashboard_seen").write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass
    svc = _current
    pending = svc.pending() if svc is not None and svc.home.resolve() == home.resolve() else read_pending_file(home)
    for a in pending:                    # SW-V4 K: the page previews the question he may SAY aloud (kind only)
        a["spoken_en"], a["spoken_he"] = spoken_kind(str(a.get("title") or ""), str(a.get("kind") or ""), a.get("details"))
    return 200, {"pending": pending, "history": read_history(home, 30),
                 "note": "Approve or deny what JARVES asked for. Typed requests need the code shown."}


def api_approval_answer(ctx, m, body):
    body = body or {}
    if not isinstance(body.get("approved"), bool):
        return 400, {"error": "body needs {\"approved\": true|false}"}
    ok, why = submit_answer(Path(ctx.home), m.group(1), body["approved"], channel="dashboard",
                            typed=str(body.get("typed") or "")[:12])
    return (200, {"ok": True}) if ok else (409, {"error": why})


def dashboard_routes(register_route) -> None:
    register_route("GET", r"/api/approvals", api_approvals)
    register_route("POST", r"/api/approvals/([0-9a-f]{16})", api_approval_answer)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    p = sub.add_parser("approvals", help="what JARVES is waiting for you to approve: list / approve / deny / history")
    p.add_argument("action", choices=["list", "approve", "deny", "history"])
    p.add_argument("id", nargs="?", help="approve/deny: the id from `jarvis approvals list`")
    p.add_argument("--code", default="", help="approve: the code shown for a typed-confirmation request")
    p.add_argument("-n", type=int, default=20, help="history: how many decisions")


def cmd_approvals(a, cfg, home) -> int:
    if a.action == "list":
        rows = read_pending_file(home)
        if not rows:
            print("Nothing is waiting for your approval.")
            return 0
        for r in rows:
            print(f"{r['id']}  [{r['risk']}] {r['kind']}: {r['title']}  ({r['seconds_left']} s left)"
                  + ((f"  -- type: jarvis approvals approve {r['id']} --code {r['typed_code']}" if r.get("typed_code")
                      else "  -- needs the code shown on the popup / dashboard") if r.get("typed") else ""))
            for k, v in (r.get("details") or {}).items():
                print(f"    {k}: {v}")
        return 0
    if a.action == "history":
        for r in reversed(read_history(home, a.n)):
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r.get('ts', 0)))}  {r.get('decision'):<10} "
                  f"{r.get('channel', ''):<10} [{r.get('risk')}] {r.get('kind')}: {r.get('title')}")
        return 0
    if not a.id:
        print("Give the id from `jarvis approvals list`.")
        return 2
    ok, why = submit_answer(home, a.id, a.action == "approve", channel="cli", typed=a.code)
    print("Sent." if ok else f"Not sent: {why}")
    return 0 if ok else 1
