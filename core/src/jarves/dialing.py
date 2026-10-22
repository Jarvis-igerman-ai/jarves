"""Assisted dialing through Phone Link (SW-V4 package H): "תתקשר לדנה" / "call Dana" / `jarvis dial Dana`.

JARVES resolves the contact, reads the number back digit by digit, shows name + number + where the number came from
in the approvals popup, and only after the OWNER approved opens a `tel:` link. Windows hands that link to Phone Link
(when it owns `tel:`) or to the default dialer, and the OWNER presses Call. JARVES never places, joins, answers or
records a call, and never drives the Phone Link window.

Rules (enforced here, never by prompt text):
- numbers come only from (a) the owner's own words in this conversation / typed command line, (b) contacts the owner
  saved (memory kind "contact" with a phone, <home>/whatsapp/contacts.json), (c) read-only Google Contacts when that
  feature is on. A number that came from an e-mail, a web page, a WhatsApp/Telegram message or recalled text is NEVER
  dialled (DIAL_UNTRUSTED_NUMBER): the owner has to say it again. For a spoken number the digits must really appear in
  something the owner said (the Core keeps the last few owner utterances in memory only).
- an unknown or ambiguous name never guesses; several numbers of one person: ask "mobile or home?".
- emergency numbers (100, 101, 102, 112, 911, 999 and the words police/ambulance/fire): no resolution, no approval and
  no rate limit; the dialer is just opened with the number and the owner presses Call.
- a number never called before needs the approval ON THE SCREEN (a spoken yes alone is refused); a known one may be
  approved by voice. Deny, timeout or nobody to ask opens nothing.
- at most RATE_LIMIT dialer openings per window; every decision is audited in <home>/dialing/audit.jsonl and in the
  approvals audit with hashes only (HMAC with a per-install salt): no number and no name in the dialing log.
- the log messages are fixed strings; variable parts are registered fields (privacy package B, tools/log_lint.py).

`tel:` handler facts (checked 2026-10-20): Windows stores the default handler of a URL scheme at
HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\tel\\UserChoice (ProgId) and the command at
HKCR\\<ProgId>\\shell\\open\\command; Phone Link is the package Microsoft.YourPhone_8wekyb3d8bbwe
(https://support.microsoft.com/windows/phone-link). Whether a phone is paired right now cannot be read reliably, so
"paired" is a best-effort guess from files and is reported as such (UNVERIFIED until the owner runs OH-01).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from . import features as feat
from . import hebrew, logs
from .channels_common import read_json, write_private_json
from .skills import Skill, Tool, ToolResult

FEATURE = "assisted_dialing"
DEFAULT_COUNTRY = "972"
DRAFT_TTL_S = 300.0
RATE_LIMIT = (5, 600.0)                  # at most 5 dialer openings per 10 minutes
SEEN_MAX = 500
EMERGENCY = {"100": "police", "101": "ambulance", "102": "fire", "112": "emergency", "911": "emergency",
             "999": "emergency"}
EMERGENCY_WORDS = {"police": "100", "משטרה": "100", "ambulance": "101", "אמבולנס": "101", "מדא": "101",
                   "fire": "102", "firefighters": "102", "כבאים": "102", "כיבויאש": "102", "מכבי אש": "102",
                   "emergency": "112", "חירום": "112"}
SOURCES = {"contacts_memory": ("הזיכרון המקומי", "your saved contacts"),
           "whatsapp_book": ("מספרים שאמרת לי", "numbers you told me"),
           "google_contacts": ("אנשי הקשר בגוגל", "Google Contacts"),
           "owner_said": ("אמרת לי עכשיו", "you just said it")}
UNTRUSTED_SOURCES = ("email", "web", "message", "recalled", "other")
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


# ---------------------------------------------------------------------- texts (code -> (Hebrew, English))
T: dict[str, tuple[str, str]] = {
    "DIAL_OFF": ("חיוג מסייע כבוי. להפעלה: jarvis features on assisted_dialing",
                 "Assisted dialing is off. Turn it on with: jarvis features on assisted_dialing"),
    "DIAL_NEED_TARGET": ("למי להתקשר? אמרו שם או מספר.", "Who should I call? Say a name or a number."),
    "DIAL_UNKNOWN_CONTACT": ("אין לי מספר של {name}. אמרו לי את המספר בעצמכם, ואני אקריא אותו בחזרה.",
                             "I have no number for {name}. Say the number yourself and I will read it back."),
    "DIAL_AMBIGUOUS": ("יש כמה אנשים בשם הזה: {choices}. למי להתקשר?",
                       "More than one person matches: {choices}. Which one?"),
    "DIAL_WHICH_NUMBER": ("ל{name} יש כמה מספרים: {choices}. לאיזה להתקשר?",
                          "{name} has more than one number: {choices}. Which one?"),
    "DIAL_BAD_NUMBER": ("המספר לא נראה תקין. אמרו אותו שוב ספרה אחרי ספרה, עם קידומת.",
                        "That does not look like a valid number. Say it again digit by digit, with the area code."),
    "DIAL_UNTRUSTED_NUMBER": ("מספר שהגיע ממייל, מאתר או מהודעה לא מחויג. אמרו לי את המספר בעצמכם ואקריא אותו.",
                              "I never dial a number that came from an e-mail, a web page or a message. "
                              "Say the number yourself and I will read it back."),
    "DIAL_NUMBER_NOT_HEARD": ("לא שמעתי אתכם אומרים את המספר הזה. אמרו אותו ספרה אחרי ספרה (או הקלידו אותו) ואקריא בחזרה.",
                              "I did not hear you say that number. Say it digit by digit (or type it) and I will "
                              "read it back."),
    "DIAL_READY": ("מוכן להתקשר ל{who}. המספר: {readback}. אישור אחד ופותחים את החייגן, ואתם לוחצים חיוג.",
                   "Ready to call {who}. The number: {readback}. One approval opens the dialer and you press Call."),
    "DIAL_UNKNOWN_DRAFT": ("אני פותח חייגן רק למספר שהקראתי עכשיו.", "I only open the dialer for a number I just read back."),
    "DIAL_RATE_LIMITED": ("יותר מדי חיוגים ברצף. נסו שוב בעוד כמה דקות.", "Too many calls in a row. Try again in a few minutes."),
    "DIAL_NO_HANDLER": ("אין במחשב אפליקציה שמטפלת בחיוג (tel:). התקינו והתאימו את Phone Link, ראו: jarvis help assisted_dialing",
                        "No app on this PC handles dialing (tel:). Set up Phone Link, see: jarvis help assisted_dialing"),
    "DIAL_PHONE_LINK_NOT_PAIRED": ("Phone Link לא מחובר לטלפון. חברו אותו (Phone Link > הוסף טלפון), ראו: jarvis help assisted_dialing",
                                   "Phone Link is not paired with a phone. Pair it (Phone Link > add phone), see: "
                                   "jarvis help assisted_dialing"),
    "DIAL_DECLINED": ("לא אושר, אז שום דבר לא נפתח.", "Not approved, so nothing was opened."),
    "DIAL_OPEN_FAILED": ("לא הצלחתי לפתוח את החייגן במחשב.", "I could not open the dialer on this PC."),
    "DIAL_OPENED": ("פתחתי את {app}. לוחצים חיוג בעצמכם.", "I opened {app}. You press Call yourself."),
    "DIAL_EMERGENCY_OPENED": ("פתחתי את החייגן עם {number}. אני לא מחייג: לוחצים חיוג בעצמכם. אם זה דחוף, חייגו מהטלפון עכשיו.",
                              "I opened the dialer with {number}. I do not place calls: press Call yourself. If it "
                              "is urgent, dial from your phone right now."),
    "DIAL_EMERGENCY_NO_HANDLER": ("אין חייגן במחשב. חייגו {number} מהטלפון עכשיו.",
                                  "There is no dialer on this PC. Dial {number} from your phone right now."),
    "DIAL_REMEMBERED": ("שמרתי את המספר של {name} במחשב הזה.", "I saved {name}'s number on this PC."),
}


def say(code: str, lang: str, **kw) -> str:
    """The text of `code` in one language. A value may be a (Hebrew, English) pair (the digits are read in the
    language of the sentence)."""
    he, en = T[code]
    pick = 0 if lang == "he" else 1
    return (he if lang == "he" else en).format(**{k: v[pick] if isinstance(v, tuple) else v for k, v in kw.items()})


# ---------------------------------------------------------------------- numbers
_DIGIT_WORDS_EN = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_DIGIT_WORDS_HE = ["אפס", "אחד", "שתיים", "שלוש", "ארבע", "חמש", "שש", "שבע", "שמונה", "תשע"]
_SPOKEN_DIGITS = {**{w: str(i) for i, w in enumerate(_DIGIT_WORDS_EN)}, "oh": "0", "o": "0",
                  **{w: str(i) for i, w in enumerate(_DIGIT_WORDS_HE)}, "אחת": "1", "שניים": "2", "שתים": "2",
                  "שני": "2", "שתי": "2", "שלושה": "3", "ארבעה": "4", "חמישה": "5", "שישה": "6", "שבעה": "7",
                  "שמונה": "8", "תשעה": "9"}


def is_emergency(raw: str) -> str | None:
    """The emergency number a text means ('100', '112', 'police', 'משטרה'), else None."""
    s = re.sub(r"[\s\-.()]", "", str(raw or "")).casefold().strip("\"'")
    if s in EMERGENCY:
        return s
    s = re.sub(r"[^\wא-ת]", "", str(raw or "").casefold())
    s = re.sub(r"^(?:ל|ה)", "", s) if s not in EMERGENCY_WORDS else s
    return EMERGENCY_WORDS.get(s)


def normalize(raw: str, country: str = DEFAULT_COUNTRY) -> str | None:
    """E.164 (+972541234567) or None when it cannot be a phone number. 0541234567 / 054-123-4567 / +972 54 ... /
    00972... are accepted; a bare number without a leading 0 or country code is refused (never guess)."""
    s = str(raw or "").strip()
    if not s or re.search(r"[^\d\s+()\-.]", s):
        return None
    plus = s.startswith("+")
    d = re.sub(r"\D", "", s)
    if d.startswith("00") and not plus:
        d, plus = d[2:], True
    elif not plus and d.startswith("0"):
        d = country + d[1:]
    elif not plus and not d.startswith(country):
        return None
    if not 8 <= len(d) <= 15 or d.startswith("0"):
        return None
    if d.startswith(DEFAULT_COUNTRY) and len(d) - len(DEFAULT_COUNTRY) not in (8, 9):
        return None                                    # Israeli numbers: 8 (landline) or 9 (mobile) digits after 972
    return "+" + d


def readback(e164: str, lang: str = "en") -> str:
    """The number as separate digits, the way it is read aloud: '+972541234567' -> 'plus nine seven two, five four
    one, two three four, five six seven'."""
    words = _DIGIT_WORDS_HE if lang == "he" else _DIGIT_WORDS_EN
    digits = [words[int(c)] for c in e164 if c.isdigit()]
    cc = 3 if e164.startswith("+972") else 2
    groups = [digits[:cc]] + [digits[i:i + 3] for i in range(cc, len(digits), 3)]
    return ("פלוס " if lang == "he" else "plus ") + ", ".join(" ".join(g) for g in groups)


def masked(e164: str) -> str:
    """For lists on screen: '+972 54 •••• 567' (enough to recognise, not enough to copy)."""
    d = e164[1:]
    return f"+{d[:3]} {d[3:5]} {'•' * max(0, len(d) - 8)} {d[-3:]}" if len(d) > 8 else "••••"


def spoken_digits(text: str) -> str:
    """All digits in an owner utterance, digit words included ('zero five four' / 'אפס חמש ארבע' -> '054')."""
    out = []
    for tok in re.findall(r"\d+|[^\W\d_]+", str(text or "").casefold()):
        out.append(tok if tok.isdigit() else _SPOKEN_DIGITS.get(tok, ""))
    return "".join(out)


def heard_in(e164: str, utterances: list[str], country: str = DEFAULT_COUNTRY) -> bool:
    """Did the owner really say this number? Full international or national (leading 0) digits appear in ONE
    utterance (spaces, dashes and digit words ignored)."""
    d = e164.lstrip("+")
    forms = {d, "00" + d}
    if d.startswith(country):
        forms.add("0" + d[len(country):])
    return any(any(f in spoken_digits(u) for f in forms) for u in utterances)


# ---------------------------------------------------------------------- names
def _plain(s: str) -> str:
    s = re.sub(r"[֑-ׇ]", "", str(s or ""))                          # niqqud
    return re.sub(r"[\"'׳״.,!?]", "", s).casefold().strip()


def _name_forms(said: str) -> list[str]:
    """What the owner said, with and without the Hebrew ל / ל־ prefix ('לדנה' -> 'דנה')."""
    s = _plain(said)
    forms = [s]
    m = re.match(r"^ל[\-־]?(.+)$", s)
    if m and len(m.group(1)) >= 2:
        forms.append(m.group(1).strip())
    return forms


def _is_hebrew(s: str) -> bool:
    return bool(re.search(r"[֐-׿]", s))


def _query_name(said: str) -> str:
    """The name for a Google search: what was said without a Hebrew ל / ל־ prefix (case kept)."""
    s = said.strip()
    m = re.match(r"^ל[-־]?(.{3,})$", s)
    return m.group(1).strip() if m else s


def _skeleton(s: str) -> str:
    from .profiles import _skeleton as sk
    return sk(s)


def name_matches(said: str, candidates: list[str]) -> tuple[list[str], bool]:
    """([matching candidate names], fuzzy). Exact full-name or first-name matches win; only when there is none the
    consonant skeleton matches Hebrew spelling against Latin ('דנה' ~ 'Dana'). Several matches stay several."""
    forms = _name_forms(said)
    for form in forms:
        hit = [c for c in candidates if _plain(c) == form or form in _plain(c).split()]
        if hit:
            return list(dict.fromkeys(hit)), False
    for form in forms:                  # only across scripts (Hebrew spelling of a Latin name or the reverse): never "Lea ~ Ela"
        sk = _skeleton(form)
        if sk:
            other = [c for c in candidates if _is_hebrew(c) != _is_hebrew(form)]
            hit = [c for c in other if _skeleton(c) == sk or sk in {_skeleton(w) for w in _plain(c).split()}]
            if hit:
                return list(dict.fromkeys(hit)), True
    return [], False


_LABELS = {"mobile": ("mobile", "cell", "נייד", "סלולרי", "פלאפון"), "home": ("home", "בית"),
           "work": ("work", "office", "עבודה", "משרד")}


def _label_kind(label: str) -> str:
    low = _plain(label)
    for kind, words in _LABELS.items():
        if any(w in low for w in words):
            return kind
    return low


# ---------------------------------------------------------------------- handler detection (read-only)
@dataclass
class Handler:
    kind: str            # phone_link | teams | skype | other | none
    name: str            # "Phone Link", the ProgId or ""
    raw: str = ""

    @property
    def usable(self) -> bool:
        return self.kind != "none"


class WinRegistry:
    """Read-only access to the Windows registry (winreg); tests pass a fake with the same `get`."""

    def get(self, root: str, path: str, value: str = "") -> str | None:
        if sys.platform != "win32":
            return None
        import winreg
        hive = {"HKCU": winreg.HKEY_CURRENT_USER, "HKCR": winreg.HKEY_CLASSES_ROOT}[root]
        try:
            with winreg.OpenKey(hive, path) as k:
                return str(winreg.QueryValueEx(k, value)[0])
        except OSError:
            return None


_USER_CHOICE = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\tel\UserChoice"


def detect_handler(reg=None) -> Handler:
    """Which app owns `tel:` (Phone Link, Teams, Skype, other, none). Never writes the registry."""
    reg = reg or WinRegistry()
    progid = reg.get("HKCU", _USER_CHOICE, "ProgId")
    cmd = reg.get("HKCR", f"{progid}\\shell\\open\\command") if progid else None
    if not cmd:
        cmd = reg.get("HKCR", "tel\\shell\\open\\command")
    if not progid and not cmd:
        # a "tel" URL protocol key without a command still means "registered, no app"
        return Handler("none", "")
    hay = f"{progid or ''} {cmd or ''}".casefold()
    if any(w in hay for w in ("yourphone", "phoneexperiencehost", "phonelink", "phone link")):
        return Handler("phone_link", "Phone Link", hay[:120])
    if "teams" in hay:
        return Handler("teams", "Microsoft Teams", hay[:120])
    if "skype" in hay:
        return Handler("skype", "Skype", hay[:120])
    return Handler("other", (progid or "the default dialer")[:60], hay[:120])


def phone_link_state(local_appdata: str | None = None) -> str:
    """'not_installed' | 'no_data' | 'data_found': best effort from files only. 'data_found' does NOT prove that a phone
    is paired or reachable right now (nothing reliable can be read); it only means Phone Link has been set up here."""
    base = Path(local_appdata or os.environ.get("LOCALAPPDATA", "")) / "Packages" / "Microsoft.YourPhone_8wekyb3d8bbwe"
    if not base.is_dir():
        return "not_installed"
    try:
        for p in (base / "LocalState").rglob("*"):
            if p.is_file() and p.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                return "data_found"
    except OSError:
        pass
    return "no_data"


def default_open(url: str) -> bool:
    if not re.fullmatch(r"tel:\+?\d{3,15}", url):          # only ever a validated tel: link
        return False
    if sys.platform == "win32" and hasattr(os, "startfile"):
        os.startfile(url)                                   # noqa: S606 - the shell hands tel: to its registered app
        return True
    return bool(webbrowser.open(url))


# ---------------------------------------------------------------------- the service
@dataclass
class Numbers:
    label: str
    e164: str
    source: str


@dataclass
class Person:
    name: str
    numbers: list[Numbers] = field(default_factory=list)


@dataclass
class Draft:
    id: str
    name: str
    e164: str
    source: str
    t: float
    lang: str = "en"


def _lang_of(text: str, default: str) -> str:
    return "he" if re.search(r"[֐-׿]", text or "") else default


class Dialer:
    """Resolve, read back, ask, open. The model tools, `jarvis dial` and the dashboard Call button all use this."""

    def __init__(self, home: Path, *, contacts: Callable[[], list[dict]] | None = None,
                 google: Callable[[str], Awaitable[list[dict]]] | None = None, registry=None,
                 phone_link: Callable[[], str] | None = None, opener: Callable[[str], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 owner_said: Callable[[], list[str]] | None = None, country: str = DEFAULT_COUNTRY,
                 lang: str = "he", rate: tuple[int, float] = RATE_LIMIT) -> None:
        self.home = Path(home)
        self.dir = self.home / "dialing"
        self._contacts, self._google, self._registry = contacts, google, registry
        self._phone_link = phone_link or phone_link_state
        self.opener, self.clock, self.wall = opener or (lambda url: default_open(url)), clock, wall
        self.owner_said, self.country, self.lang, self.rate = owner_said, country, lang, rate
        self._drafts: dict[str, Draft] = {}
        self._opened: list[float] = []

    # --- hashes (the only form in which a number or a name reaches a log)
    def _salt(self) -> bytes:
        p = self.dir / "salt.json"
        s = read_json(p).get("salt")
        if not isinstance(s, str) or len(s) < 16:
            s = secrets.token_hex(16)
            write_private_json(p, {"salt": s})
        return s.encode()

    def digest(self, value: str) -> str:
        return hmac.new(self._salt(), str(value).casefold().encode(), hashlib.sha256).hexdigest()[:16]

    def _audit(self, event: str, code: str, name: str = "", e164: str = "", source: str = "", handler: str = "") -> None:
        rec = {"ts": round(self.wall(), 3), "event": event, "code": code, "name_hash": self.digest(name) if name else "",
               "number_hash": self.digest(e164) if e164 else "", "source": source, "handler": handler}
        p = self.dir / "audit.jsonl"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > 500_000:
                os.replace(p, p.with_suffix(".1.jsonl"))
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            logs.warn("dialing", "DIAL_AUDIT_FAILED", "could not append to the dialing audit file")

    # --- called-before memory (hashes only)
    def _seen(self) -> dict:
        v = read_json(self.dir / "seen.json").get("n")
        return v if isinstance(v, dict) else {}

    def seen_before(self, e164: str) -> bool:
        return self.digest(e164) in self._seen()

    def _mark_seen(self, e164: str) -> None:
        seen = self._seen()
        seen[self.digest(e164)] = round(self.wall())
        for k in sorted(seen, key=seen.get)[:max(0, len(seen) - SEEN_MAX)]:
            seen.pop(k)
        write_private_json(self.dir / "seen.json", {"n": seen})

    # --- contacts
    def book(self) -> dict[str, str]:
        from .whatsapp import contacts_file
        return {k: v for k, v in read_json(contacts_file(self.home)).items() if isinstance(v, str)}

    def local_people(self) -> list[Person]:
        people: dict[str, Person] = {}
        for name, phone in self.book().items():
            e = normalize("+" + phone.lstrip("+")) if phone.strip().lstrip("+").isdigit() else normalize(phone, self.country)
            if e:
                people.setdefault(name, Person(name)).numbers.append(Numbers("mobile", e, "whatsapp_book"))
        for row in (self._contacts() if self._contacts else []):
            if row.get("source") not in (None, "user"):
                continue                                   # only what the owner saved on purpose
            try:
                d = json.loads(row.get("text") or "{}")
            except (ValueError, TypeError):
                continue
            phone = str(d.get("phone") or "").strip() if isinstance(d, dict) else ""
            e = normalize(phone, self.country) if phone else None
            if not e:
                continue
            for nm in dict.fromkeys(x for x in (d.get("name"), d.get("label")) if x):
                people.setdefault(str(nm), Person(str(nm))).numbers.append(
                    Numbers(str(d.get("phone_label") or "mobile"), e, "contacts_memory"))
        return list(people.values())

    async def google_people(self, name: str) -> list[Person]:
        if self._google is None:
            return []
        try:
            rows = await self._google(name)
        except Exception as e:
            logs.warn("dialing", "DIAL_GOOGLE_FAILED", "Google Contacts lookup failed", error_type=type(e).__name__)
            return []
        out = []
        for r in rows or []:
            nums = [Numbers(str(n.get("label") or "mobile"), e, "google_contacts") for n in r.get("numbers", [])
                    if (e := normalize(str(n.get("value") or ""), self.country))]
            if r.get("name") and nums:
                out.append(Person(str(r["name"]), nums))
        return out

    async def resolve(self, name: str) -> tuple[list[Person], bool]:
        """(people matching `name`, fuzzy). Local contacts first; Google only when the local book has no match."""
        local = self.local_people()
        names, fuzzy = name_matches(name, [p.name for p in local])
        found = [p for p in local if p.name in names]
        if not found:
            gp = await self.google_people(_query_name(name))
            names, fuzzy = name_matches(name, [p.name for p in gp])
            found = [p for p in gp if p.name in names]
        merged: dict[str, Person] = {}
        for p in found:                                    # one person listed twice (memory + book): numbers merge
            m = merged.setdefault(_plain(p.name), Person(p.name))
            for n in p.numbers:
                if all(n.e164 != x.e164 for x in m.numbers):
                    m.numbers.append(n)
        return list(merged.values()), fuzzy

    def lang_for(self, *texts: str) -> str:
        return next((l for l in (_lang_of(t, "") for t in texts) if l), self.lang)

    # --- prepare
    def handler(self) -> Handler:
        return detect_handler(self._registry)

    def _res(self, ok: bool, code: str, lang: str, data: dict | None = None, **kw) -> ToolResult:
        return ToolResult(ok, say(code, lang, **kw), {**(data or {}), "message_he": say(code, "he", **kw),
                                                      "message_en": say(code, "en", **kw)}, code=code)

    async def prepare(self, *, name: str = "", number: str = "", number_source: str = "owner_said", which: str = "",
                      remember: bool = False, trusted_owner_input: bool = False, check_enabled: bool = True) -> ToolResult:
        """Resolve a name or validate a number and keep a draft. `trusted_owner_input`: the text was typed by the owner
        (CLI, dashboard form), so no 'did I hear it' check is needed; the model path always checks."""
        name, number, which = name.strip(), number.strip(), which.strip()
        lang = self.lang_for(name, number)
        if not name and not number:
            return self._res(False, "DIAL_NEED_TARGET", lang)
        emergency = is_emergency(number) or is_emergency(name)
        if emergency:
            return await self.open_emergency(emergency, lang)
        if number_source in UNTRUSTED_SOURCES and number:
            self._audit("refused", "DIAL_UNTRUSTED_NUMBER", name, normalize(number, self.country) or "", number_source)
            logs.warn("dialing", "DIAL_UNTRUSTED_NUMBER", "number from untrusted text refused", source=number_source)
            return self._res(False, "DIAL_UNTRUSTED_NUMBER", lang)
        e164: str | None = None
        source = "owner_said"
        who = name
        fuzzy = False
        if number:
            e164 = normalize(number, self.country)
            if e164 is None:
                return self._res(False, "DIAL_BAD_NUMBER", lang)
            if not trusted_owner_input and (self.owner_said is None or not heard_in(e164, self.owner_said(), self.country)):
                self._audit("refused", "DIAL_NUMBER_NOT_HEARD", name, e164, "owner_said")
                logs.warn("dialing", "DIAL_NUMBER_NOT_HEARD", "spoken number was not found in the owner's words")
                return self._res(False, "DIAL_NUMBER_NOT_HEARD", lang)
            if remember and name:
                from .whatsapp import contacts_file
                book = self.book()
                book[name[:60]] = e164.lstrip("+")
                write_private_json(contacts_file(self.home), book)
        else:
            people, fuzzy = await self.resolve(name)
            if not people:
                self._audit("refused", "DIAL_UNKNOWN_CONTACT", name, "", "")
                return self._res(False, "DIAL_UNKNOWN_CONTACT", lang, name=name)
            if len(people) > 1:
                return self._res(False, "DIAL_AMBIGUOUS", lang, {"choices": [p.name for p in people]},
                                 choices=" / ".join(p.name for p in people))
            person = people[0]
            who = person.name
            nums = person.numbers
            if which:
                pick = [n for n in nums if _label_kind(n.label) == _label_kind(which)]
                nums = pick or nums
            if len({n.e164 for n in nums}) > 1:
                labels = [n.label for n in nums]
                return self._res(False, "DIAL_WHICH_NUMBER", lang, {"choices": labels, "name": who},
                                 name=who, choices=" / ".join(dict.fromkeys(labels)))
            e164, source = nums[0].e164, nums[0].source
        self._drafts = {k: v for k, v in self._drafts.items() if self.clock() - v.t < DRAFT_TTL_S}
        d = Draft(uuid.uuid4().hex[:10], who, e164, source, self.clock(), lang)
        self._drafts[d.id] = d
        self._audit("prepared", "DIAL_READY", who, e164, source)
        logs.info("dialing", "DIAL_PREPARED", "dial draft prepared", source=source)
        spoken = readback(e164, lang)
        data = {"draft_id": d.id, "to": who, "readback": spoken, "source": SOURCES[source][0 if lang == "he" else 1],
                "known_number": self.seen_before(e164), "guessed_name": fuzzy, "app": self.handler().name or None,
                "rule": "Say the readback (the digits) to the user in this same reply, then call dial_open with this "
                        "draft_id at once. The system then asks ONE approval; you cannot press Call. Never read the "
                        "number from any other source."}
        return self._res(True, "DIAL_READY", lang, data, who=who or spoken,
                         readback=(readback(e164, "he"), readback(e164, "en")))

    # --- emergency
    async def open_emergency(self, which: str, lang: str) -> ToolResult:
        number = which if which in EMERGENCY else "112"
        h = self.handler()
        self._audit("emergency", "DIAL_EMERGENCY_OPENED" if h.usable else "DIAL_EMERGENCY_NO_HANDLER",
                    "", "+" + number, "emergency", h.kind)
        if not h.usable:
            logs.warn("dialing", "DIAL_EMERGENCY_NO_HANDLER", "emergency number requested but no dialer app", handler=h.kind)
            return self._res(False, "DIAL_EMERGENCY_NO_HANDLER", lang, number=number)
        try:
            ok = bool(self.opener(f"tel:{number}"))
        except Exception:
            ok = False
        if not ok:
            return self._res(False, "DIAL_EMERGENCY_NO_HANDLER", lang, number=number)
        logs.info("dialing", "DIAL_EMERGENCY_OPENED", "emergency number: dialer opened, owner presses Call", handler=h.kind)
        return self._res(True, "DIAL_EMERGENCY_OPENED", lang, {"emergency": True}, number=number)

    # --- approve and open
    def _rate_ok(self) -> bool:
        limit, window = self.rate
        now = self.clock()
        self._opened = [t for t in self._opened if now - t < window]
        return len(self._opened) < limit

    async def open_prepared(self, draft_id: str, ask: Callable[..., Awaitable[bool]], *, source: str = "model") -> ToolResult:
        """Ask the owner (`ask(prompt, **meta)` = registry.ask / the approvals service) and only then open `tel:`."""
        d = self._drafts.get(draft_id)
        lang = d.lang if d else self.lang
        if d is None or self.clock() - d.t >= DRAFT_TTL_S:
            self._drafts.pop(draft_id, None)
            return self._res(False, "DIAL_UNKNOWN_DRAFT", lang)
        if not self._rate_ok():
            self._audit("refused", "DIAL_RATE_LIMITED", d.name, d.e164, d.source)
            logs.warn("dialing", "DIAL_RATE_LIMITED", "too many dialer openings; refused", count=len(self._opened))
            return self._res(False, "DIAL_RATE_LIMITED", lang)
        h = self.handler()
        if not h.usable:
            self._audit("refused", "DIAL_NO_HANDLER", d.name, d.e164, d.source, h.kind)
            return self._res(False, "DIAL_NO_HANDLER", lang)
        if h.kind == "phone_link" and self._phone_link() in ("not_installed", "no_data"):
            self._audit("refused", "DIAL_PHONE_LINK_NOT_PAIRED", d.name, d.e164, d.source, h.kind)
            return self._res(False, "DIAL_PHONE_LINK_NOT_PAIRED", lang)
        known = self.seen_before(d.e164)
        app = h.name or "the dialer"
        who = d.name or d.e164
        prompt = (f"לפתוח את {app} כדי להתקשר ל{who} במספר {d.e164}? אתם לוחצים חיוג."
                  if lang == "he" else f"Open {app} to call {who} on {d.e164}? You press Call.")
        details = {"recipient": d.name, "number": d.e164, "number_source": SOURCES[d.source][0 if lang == "he" else 1],
                   "app": app}
        okay = await ask(prompt, title="dial_open", why="You asked JARVES to call this person.", risk="medium",
                         kind="call", details=details, source=source, voice=known)
        if not okay:
            self._audit("denied", "DIAL_DECLINED", d.name, d.e164, d.source, h.kind)
            logs.info("dialing", "DIAL_DECLINED", "dialing not approved; nothing opened")
            return self._res(False, "DIAL_DECLINED", lang)
        self._drafts.pop(draft_id, None)
        self._opened.append(self.clock())
        try:
            ok = bool(self.opener(f"tel:{d.e164}"))
        except Exception:
            ok = False
        if not ok:
            self._audit("failed", "DIAL_OPEN_FAILED", d.name, d.e164, d.source, h.kind)
            logs.warn("dialing", "DIAL_OPEN_FAILED", "dialer could not be opened", handler=h.kind)
            return self._res(False, "DIAL_OPEN_FAILED", lang)
        self._mark_seen(d.e164)
        self._audit("opened", "DIAL_OPENED", d.name, d.e164, d.source, h.kind)
        logs.info("dialing", "DIAL_OPENED", "dialer opened; the owner presses Call", handler=h.kind, source=d.source)
        return self._res(True, "DIAL_OPENED", lang, {"to": d.name, "app": app}, app=app)

    def status(self) -> dict:
        h = self.handler()
        return {"handler": h.kind, "app": h.name, "phone_link": self._phone_link() if h.kind == "phone_link" else None}


# ---------------------------------------------------------------------- Google Contacts (read-only phone numbers)
def google_phone_lookup(api) -> Callable[[str], Awaitable[list[dict]]]:
    """People API searchContacts with readMask names,phoneNumbers (contacts.readonly; the same consent as the
    Google Contacts feature). Official reference checked 2026-10-20:
    https://developers.google.com/people/api/rest/v1/people/searchContacts"""
    from .google_auth import CONTACTS_READ
    from .skills.contacts import PEOPLE_SEARCH

    async def lookup(query: str) -> list[dict]:
        params = {"query": "", "readMask": "names,phoneNumbers", "pageSize": 6}
        await api.request("GET", PEOPLE_SEARCH, (CONTACTS_READ,), params=params)       # documented warm-up call
        res = await api.request("GET", PEOPLE_SEARCH, (CONTACTS_READ,), params={**params, "query": query})
        out = []
        for r in (res.get("results") or [])[:6]:
            p = r.get("person") or {}
            names = p.get("names") or []
            out.append({"name": str(names[0].get("displayName", ""))[:120] if names else "",
                        "numbers": [{"value": str(n.get("value", ""))[:40],
                                     "label": str(n.get("type") or n.get("formattedType") or "mobile")[:20]}
                                    for n in (p.get("phoneNumbers") or [])[:6]]})
        return out
    return lookup


# ---------------------------------------------------------------------- the Skill (voice / typed tools)
class PrepareArgs(BaseModel):
    name: str = Field("", max_length=60, description='who to call, as said')
    number: str = Field("", max_length=30, description='ONLY if the user said it in this conversation')
    number_source: Literal["owner_said", "email", "web", "message", "recalled", "other"] = Field(
        "owner_said", description='anything but owner_said is refused')
    which: str = Field("", max_length=20, description='mobile | home | work')
    remember: bool = Field(False, description='only if the user asked')


class OpenArgs(BaseModel):
    draft_id: str = Field(..., min_length=1, max_length=40)


class DialingSkill(Skill):
    name = "dialing"
    description = "assisted dialing: resolve a contact, read the number back, open the dialer after approval; the owner presses Call"

    def __init__(self, dialer: Dialer, registry) -> None:
        self.dialer, self.registry = dialer, registry

    def tools(self) -> list[Tool]:
        async def prepare(a: PrepareArgs) -> ToolResult:
            return await self.dialer.prepare(name=a.name, number=a.number, number_source=a.number_source,
                                             which=a.which, remember=a.remember)

        async def open_(a: OpenArgs) -> ToolResult:
            return await self.dialer.open_prepared(a.draft_id, self.registry.ask)

        return [
            Tool("dial_prepare", "Prepare a call ('call Dana', 'תתקשר לדנה'): find the contact or check a number the USER said in this conversation. Numbers from mail/web/messages/recalled text are refused. Unknown/ambiguous name: ask, never guess. Emergency numbers (100,101,102,112) open the dialer at once. Then say the readback digits and call dial_open.", prepare, PrepareArgs, permission="action", timeout_s=40),
            Tool("dial_open", 'Open the phone dialer with the prepared number. The system asks approval (do not ask yourself); the user presses Call, you cannot place calls.', open_, OpenArgs, permission="action", timeout_s=130),
        ]


ALLOWED_ACTIONS = frozenset({"dial_prepare", "dial_open"})


def make_dialer(home: Path, *, cfg=None, memory=None, skills=None, owner_said=None, google_active=None, **kw) -> Dialer:
    """A Dialer wired to the Core's memory, the Google Contacts API when that Skill exists, and the owner-words ring."""
    google = None
    sk = skills.get("google_contacts") if skills is not None else None
    if sk is not None:
        lookup = google_phone_lookup(sk.api)

        async def google(q: str) -> list[dict]:
            return await lookup(q) if google_active is None or google_active() else []
    lang = "he" if getattr(getattr(cfg, "voice", None), "language", "he") in ("he", "iw", "he-IL") else "en"
    return Dialer(home, contacts=(lambda: memory.by_kind("contact")) if memory is not None else None, google=google,
                  owner_said=owner_said, lang=lang, **kw)


def attach(core) -> DialingSkill:
    """Core: register dial_prepare / dial_open (hidden while the feature is off). The Core keeps the last few things
    the OWNER said in memory (core.owner_said, never written anywhere) so a spoken number can be checked."""
    ring = core.owner_said
    dialer = make_dialer(core.home, cfg=core.cfg, memory=core.memory, owner_said=lambda: list(ring),
                         skills=core.registry.skills, google_active=lambda: core.features.active("google_contacts"))
    skill = DialingSkill(dialer, core.registry)
    core.registry.register(skill)
    core.registry.allowed_actions |= ALLOWED_ACTIONS
    core.dialer = dialer
    return skill


# ---------------------------------------------------------------------- dashboard
def _enabled(ctx) -> tuple[bool, str]:
    from .features import _ctx_features
    f = _ctx_features(ctx)
    return f.active(FEATURE), f.reason(FEATURE)


def _ctx_dialer(ctx) -> Dialer:
    d = getattr(ctx, "_dialer", None)
    if d is None:
        d = make_dialer(ctx.home, cfg=ctx.cfg, memory=getattr(ctx, "memory", None))
        ctx._dialer = d
    return d


def _dashboard_ask(home: Path):
    async def ask(prompt: str, *, title: str, why: str = "", risk: str = "medium", kind: str = "call",
                  details: dict | None = None, source: str = "dashboard", voice: bool | None = None) -> bool:
        from . import approvals
        loop = asyncio.get_running_loop()
        ok, _why = await loop.run_in_executor(None, lambda: approvals.ask_owner(
            home, prompt, title=title, why=why, kind=kind, risk=risk, details=details))
        return ok
    return ask


def dashboard_routes(register) -> None:
    def api_state(ctx, _m, _body):
        on, why = _enabled(ctx)
        d = _ctx_dialer(ctx)
        rows = []
        if on:
            for p in d.local_people():
                rows.append({"name": p.name, "numbers": [{"label": n.label, "masked": masked(n.e164),
                                                          "source": SOURCES[n.source][1]} for n in p.numbers]})
        return 200, {"enabled": on, "reason": why, "handler": d.status() if on else {}, "contacts": rows}

    def api_call(ctx, _m, body):
        on, why = _enabled(ctx)
        if not on:
            return 403, {"error": say("DIAL_OFF", "en"), "error_he": say("DIAL_OFF", "he"), "reason": why}
        body = body or {}
        d = _ctx_dialer(ctx)

        async def run() -> ToolResult:
            r = await d.prepare(name=str(body.get("name") or ""), number=str(body.get("number") or ""),
                                which=str(body.get("which") or ""), trusted_owner_input=True)
            if not r.ok or r.data.get("emergency") or "draft_id" not in r.data:
                return r
            return await d.open_prepared(r.data["draft_id"], _dashboard_ask(ctx.home), source="dashboard")
        res = asyncio.run(run())
        return 200, {"ok": res.ok, "code": res.code, "message": res.data.get("message_en"),
                                          "message_he": res.data.get("message_he"), "choices": res.data.get("choices")}

    register("GET", r"/api/dialing", api_state)
    register("POST", r"/api/dialing/call", api_call)


# ---------------------------------------------------------------------- CLI
def add_cli(sub) -> None:
    p = sub.add_parser("dial", help="assisted dialing: jarvis dial <name|number> [--which mobile|home] [--yes-on-screen]; "
                                    "reads the number back, asks, then opens Phone Link / the dialer. You press Call")
    p.add_argument("target", nargs="+", help="a contact name (Dana, לדנה) or a number you type yourself")
    p.add_argument("--which", default="", help="mobile / home / work when the person has several numbers")
    p.add_argument("--yes-on-screen", action="store_true",
                   help="approve on the screen (popup / dashboard / `jarvis approvals approve`) instead of typing yes here")
    p.add_argument("--remember", action="store_true", help="with a typed number: save it under the name given with --name")
    p.add_argument("--name", default="", help="with a typed number: the person's name (for --remember and the approval)")
    p.add_argument("--json", action="store_true")


def cmd_dial(a, cfg, home) -> int:
    from . import clilang
    from .cli import _cli_features
    from .memory import MemoryStore
    f = _cli_features(cfg, home)
    if not f.active(FEATURE):
        print(clilang.fmt(say("DIAL_OFF", "he"), say("DIAL_OFF", "en")), file=sys.stderr)
        return 2
    mem = MemoryStore(home / "memory.db")
    try:
        d = make_dialer(home, cfg=cfg, memory=mem)
        target = " ".join(a.target).strip()
        typed_number = bool(normalize(target, d.country)) or bool(re.fullmatch(r"[\d\s+()\-.]{3,}", target))
        out = sys.stdout

        async def go() -> int:
            r = await d.prepare(name=a.name if typed_number else target, number=target if typed_number else "",
                                which=a.which, remember=a.remember, trusted_owner_input=True)
            if not r.ok or r.data.get("emergency") or "draft_id" not in r.data:
                print(json.dumps({"ok": r.ok, "code": r.code, "message": r.message}, ensure_ascii=False) if a.json
                      else r.message, file=out if r.ok else sys.stderr)
                return 0 if r.ok else 1
            print(r.message, file=out)
            if a.yes_on_screen:
                ask = _dashboard_ask(home)
            else:
                if not sys.stdin or not sys.stdin.isatty():
                    print(clilang.fmt("נדרש אישור: הוסיפו --yes-on-screen ואשרו על המסך",
                                      "approval needed: add --yes-on-screen and approve on the screen"), file=sys.stderr)
                    return 1

                async def ask(prompt: str, **meta) -> bool:
                    return input(f"{prompt.split(' / ')[0]}  type yes: ").strip().lower() in ("yes", "y", "כן")
            res = await d.open_prepared(r.data["draft_id"], ask, source="user")
            print(json.dumps({"ok": res.ok, "code": res.code, "message": res.message}, ensure_ascii=False) if a.json
                  else res.message, file=out if res.ok else sys.stderr)
            return 0 if res.ok else 1
        return asyncio.run(go())
    finally:
        mem.close() if hasattr(mem, "close") else None


# ---------------------------------------------------------------------- feature
def _is_windows() -> bool:
    return sys.platform == "win32"


def _probe(c) -> tuple[bool, str]:
    if not _is_windows():
        return False, "assisted dialing needs Windows with Phone Link or a default dialer"
    return True, ""


EVIDENCE = "UNIT test_dialing.py (fake registry, fake opener, fake contacts; no call and no window)"
BLOCKER = "owner has not opened a real tel: link into Phone Link yet (OH-01)"
HE = {
    "opens your phone's dialer through Phone Link after you approve the number; you press Call. He never places, "
    "answers or records calls": "פותח את החייגן של הטלפון דרך Phone Link אחרי שאישרתם את המספר, ואתם לוחצים חיוג. "
                                "הוא אף פעם לא מחייג, עונה או מקליט שיחות",
    EVIDENCE: "בדיקות יחידה test_dialing.py (רישום מזויף, פותח מזויף, אנשי קשר מזויפים; בלי שיחה ובלי חלון)",
    BLOCKER: "הבעלים עוד לא פתח קישור tel: אמיתי אל Phone Link (OH-01)",
    "Phone Link is Windows only; the Raspberry Pi has no Phone Link": "Phone Link קיים רק ב-Windows; ל-Raspberry Pi אין Phone Link",
    "assisted dialing needs Windows with Phone Link or a default dialer": "חיוג מסייע דורש Windows עם Phone Link או חייגן ברירת מחדל",
}
hebrew.TEXT.update(HE)

feat.register(FEATURE, "חיוג מסייע (Phone Link)", "Assisted dialing (Phone Link)", False, probe=_probe, risk="medium",
              skills=["dialing"],
              about="opens your phone's dialer through Phone Link after you approve the number; you press Call. He never "
                    "places, answers or records calls",
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "planned",
                                          "blocker": "Phone Link is Windows only; the Raspberry Pi has no Phone Link",
                                          "roadmap_issue": "https://github.com/igalgerman/jarves/issues/3"}},
              permissions=["action", "approval on every call (on screen for a new number)"],
              settings="jarvis dial <name|number>, dashboard Calls page (#/calls)")
