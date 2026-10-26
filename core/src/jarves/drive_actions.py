"""Drive Actions from the phone (V6 plan item 10, issue #217): tel: / Maps / Waze / YouTube Music hand-off links.

TAP-ONLY by construction. This module only BUILDS a link string from text the owner typed on the phone. It never opens,
dials, sends or plays anything, never calls a Skill, never approves anything and never touches the Gate (#317/#298): drive
mode, the untrusted-turn rule and the approvals service are unchanged. The phone shows the link as a plain button; the
action happens only when the owner taps it, in the phone's own app (Phone, Maps, Waze, YouTube Music).

A refused request NEVER claims success: it returns ok False, a code, and no link. The number, place and search text are not
stored or logged (the mobile audit holds only the route, the kind and the status).
"""
from __future__ import annotations

from urllib.parse import quote

from . import dialing, journey as jn

FEATURE = "drive_actions"
KINDS = ("call", "navigate", "music")
RATE_PER_MIN = 10
RATE_LIMITED = {"error": "too many requests: wait a minute", "error_he": "יותר מדי בקשות: חכו דקה", "code": "ACTION_RATE_LIMITED"}
MAX_TEXT = 120
LINK_PREFIXES = ("tel:+", "https://www.google.com/maps/dir/?", "https://waze.com/ul?", "https://music.youtube.com/search?q=")

T = {
    "ACTION_BAD_KIND": ("סוג פעולה לא מוכר.", "Unknown action."),
    "ACTION_BAD_TEXT": ("חסר טקסט או שהוא ארוך מדי.", "The text is missing or too long."),
    "ACTION_BAD_NUMBER": ("זה לא נראה כמו מספר טלפון. הקלידו אותו עם קידומת, למשל 054-1234567.",
                          "That does not look like a phone number. Type it with the area code, for example 054-1234567."),
    "ACTION_EMERGENCY": ("מספרי חירום לא נפתחים מכאן. חייגו אותם ישירות בחייגן של הטלפון.",
                         "Emergency numbers are not opened from here. Dial them directly on the phone's own dialer."),
    "ACTION_BAD_PLACE": ("הקלידו יעד. JARVES לא קורא את המיקום שלכם.", "Type a destination. JARVES does not read your location."),
    "ACTION_READY": ("הקישור מוכן. שום דבר לא קרה: הוא נפתח רק כשאתם לוחצים.", "The link is ready. Nothing has happened: it opens only when you tap it."),
}


def say(code: str, lang: str = "en") -> str:
    he, en = T[code]
    return he if lang == "he" else en


def _deny(code: str) -> dict:
    return {"ok": False, "code": code, "error": say(code, "en"), "error_he": say(code, "he")}


def build(kind: str, text: str) -> dict:
    """{"ok": True, "kind", "links": [{"id", "url"}], "note"} or {"ok": False, "code", "error", "error_he"}. Pure: no side effects."""
    if kind not in KINDS:
        return _deny("ACTION_BAD_KIND")
    s = " ".join(str(text or "").split())
    if not s or len(s) > MAX_TEXT or any(ord(c) < 32 for c in s):
        return _deny("ACTION_BAD_TEXT")
    if kind == "call":
        if dialing.is_emergency(s):
            return _deny("ACTION_EMERGENCY")
        e164 = dialing.normalize(s)
        if not e164:
            return _deny("ACTION_BAD_NUMBER")
        if dialing.is_emergency(e164.lstrip("+")):
            return _deny("ACTION_EMERGENCY")
        links = [{"id": "tel", "url": "tel:" + e164}]
    elif kind == "navigate":
        try:
            place = jn.clean_place(s)
        except jn.JourneyError:
            return _deny("ACTION_BAD_PLACE")
        links = [{"id": "maps", "url": jn.maps_link("", place).replace("origin=&", "")}, {"id": "waze", "url": jn.waze_link(place)}]
    else:
        links = [{"id": "music", "url": "https://music.youtube.com/search?q=" + quote(s, safe="")}]
    if not all(l["url"].startswith(LINK_PREFIXES) for l in links):       # defence in depth: never hand out an unknown scheme
        return _deny("ACTION_BAD_TEXT")
    return {"ok": True, "kind": kind, "links": links, "note": say("ACTION_READY", "en"), "note_he": say("ACTION_READY", "he")}


def phone_action(mob, d: dict) -> tuple[int, dict]:
    """The /api/m/drive/action handler body: both switches must be on; the answer is always a link or a refusal."""
    from . import mobile_drive
    try:
        f = mobile_drive._features(mob)
    except Exception:                                            # fail closed: no switch state, no link
        return 503, {"error": "drive state is not available right now", "code": "DRIVE_STATE_UNAVAILABLE"}
    if not mobile_drive._on(f, mobile_drive.FEATURE):
        return 409, {"error": "the Drive screen is switched off on the computer", "code": "DRIVE_SCREEN_OFF"}
    if not mobile_drive._on(f, FEATURE):
        return 409, {"error": "Drive Actions are switched off on the computer", "code": "ACTIONS_OFF"}
    got = build(str(d.get("kind", "")), str(d.get("text", "")))
    return (200 if got["ok"] else 400), got


# ---------------------------------------------------------------------- catalog row
from . import capabilities, features as feat, hebrew    # noqa: E402

ABOUT = ("on the phone Drive tab, big buttons that prepare a call (tel:), a Maps / Waze trip or a YouTube Music search from "
         "text you type. They are links: nothing is dialed, sent or played until you tap one, and emergency numbers are refused")
EVIDENCE = "UNIT test_drive_actions.py (links only, refusals never claim success, switches, scope, no side effects; no real phone)"
BLOCKER = "never tapped on a real phone"
PI = "the phone page is hosted by the PC in V6; there is no Pi path"
hebrew.TEXT.update({
    ABOUT: "בלשונית הנהיגה בטלפון, כפתורים גדולים שמכינים שיחה (tel:), נסיעה ב-Maps או Waze או חיפוש ב-YouTube Music מטקסט שהקלדתם. "
           "אלה קישורים: שום דבר לא מחויג, נשלח או מתנגן עד שאתם לוחצים, ומספרי חירום נדחים",
    EVIDENCE: "בדיקות יחידה test_drive_actions.py (קישורים בלבד, דחייה לא מדווחת כהצלחה, מתגים, הרשאה, בלי תופעות לוואי; בלי טלפון אמיתי)",
    BLOCKER: "מעולם לא נלחץ בטלפון אמיתי",
    "NOT-RUN: " + BLOCKER: "לא הורץ: מעולם לא נלחץ בטלפון אמיתי",
    PI: "עמוד הטלפון מתארח במחשב ב-V6; אין נתיב ל-Pi",
})
feat.register(FEATURE, "פעולות נהיגה בטלפון", "Phone Drive Actions", False, risk="medium", about=ABOUT, needs=[],
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["tap-only links; per-phone drive permission"], settings="jarvis features on drive_actions")
capabilities.declare_extra(FEATURE, phone={"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": "NOT-RUN: " + BLOCKER,
                                           "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/217"})
hebrew.TEXT["tap-only links; per-phone drive permission"] = "קישורים בלחיצה בלבד; הרשאת נהיגה לכל טלפון"
