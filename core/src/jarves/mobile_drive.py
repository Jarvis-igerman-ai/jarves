"""Phone Drive tab (V6 plan item 11, issue #218): a read-mostly driving screen on a paired phone.

Needs the `drive` scope (off until the owner grants it with `jarvis phone scopes <id> --grant drive`) and the phone token
like every other `/api/m/` route: there is no new unauthenticated route. What it does, and what it never does:

  * drive mode: READ-ONLY. It shows on/off and how many "when you stop" items wait, with the TOOL NAME of each
    (never the text, never a contact name). The phone cannot switch drive mode, approve, call or message anything.
  * pre-drive briefing: the phone can ANSWER "is anyone in the car?" (alone / someone is with me) for that one briefing.
    The answer is never stored. It can never change the persistent `drive_briefing.passenger` setting, and it can never be
    LESS private than that setting (a setting of "present" cannot be overridden by "alone" from the phone).
  * journey: the owner types two places; JARVES returns the estimate (free providers, no live traffic, labelled) and
    the Google Maps / Waze links. The links are plain buttons on the phone: opening one is the owner's tap, never automatic.
  * no location is read or sent, ever. Places are not stored, logged or audited (the audit holds the route and status).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import logs

SCOPE = "drive"
FEATURE = "phone_drive"
JOURNEY_RATE_PER_MIN = 6
JOURNEY_ALL_PHONES_PER_MIN = 10     # one shared bucket: several paired phones cannot multiply the load on the free providers

BRIEFING_RATE_PER_MIN = 6           # each tap calls Google and the weather service and can hold a dashboard thread up to 60 s
RATE_LIMITED = {"error": "too many requests: wait a minute", "error_he": "יותר מדי בקשות: חכו דקה", "code": "DRIVE_RATE_LIMITED"}
LINK_PREFIXES = ("https://www.google.com/maps/dir/?", "https://waze.com/ul?")
_TOOL = re.compile(r"[a-z][a-z0-9_]{0,39}")
MODES = ("ask", "present", "alone")
PRIVATE_ORDER = {"ask": 0, "alone": 1, "present": 2}     # higher = more private (a passenger can hear)


JOURNEY_BUSY = {"error": "the trip service is busy: wait a minute", "error_he": "שירות הנסיעות עמוס: חכו דקה", "code": "JOURNEY_BUSY"}


def tool_name(text: str) -> str:
    """The tool of a queue label such as 'whatsapp_send -> Dana': the name only, never the recipient or any text."""
    head = str(text or "").split(" ", 1)[0]
    return head if _TOOL.fullmatch(head) else "other"


def effective_mode(setting: str, answer: str | None) -> str:
    """Which passenger mode a briefing runs in. The phone's answer applies to this briefing only and can only make it
    MORE private than the owner's persistent setting, never less."""
    setting = setting if setting in MODES else "ask"
    if answer not in ("alone", "present"):
        return setting
    if setting == "ask":
        return answer
    return answer if PRIVATE_ORDER[answer] >= PRIVATE_ORDER[setting] else setting


def _features(mob):
    fn = getattr(mob.bridge, "drive_features", None) if mob.bridge is not None else None
    f = fn() if fn else None
    if f is None:
        f = getattr(mob, "_drive_features", None)
        if f is None:
            from .config import Config
            from .features import Features
            f = mob._drive_features = Features(Config(), mob.home)
    return f


def _setting(mob) -> str:
    fn = getattr(mob.bridge, "drive_passenger", None) if mob.bridge is not None else None
    try:
        if fn:
            return fn()
        from .userdata import open_db
        return str(open_db(mob.home).setting("drive_briefing.passenger") or "ask")
    except Exception:
        return "ask"


def _on(f, fid: str) -> bool:
    try:
        return bool(f.active(fid))
    except Exception:
        return False


def view(mob) -> dict:
    from . import drive_mode
    f = _features(mob)
    setting = _setting(mob)
    items = drive_mode.queue_for(mob.home).items()
    return {"enabled": _on(f, FEATURE), "drive_mode": _on(f, drive_mode.FEATURE), "waiting": len(items),
            "queue": [{"tool": tool_name(i.text)} for i in items],
            "briefing_on": _on(f, "drive_briefing"), "passenger_setting": setting if setting in MODES else "ask",
            "journey_on": _on(f, "journey"), "privacy": bool(logs.privacy())}


def briefing(mob, answer: str | None) -> tuple[int, dict]:
    v = view(mob)
    if not v["enabled"]:
        return 409, {"error": "the Drive screen is switched off on the computer", "code": "DRIVE_SCREEN_OFF"}
    if not v["briefing_on"]:
        return 409, {"error": "the pre-drive briefing is switched off on the computer", "code": "BRIEFING_OFF"}
    if answer not in (None, "", "alone", "present"):
        return 400, {"error": "passenger must be alone or present"}
    mode = effective_mode(v["passenger_setting"], answer or None)
    if mode == "ask":
        return 200, {"ask": True, "mode": "ask"}
    fn = getattr(mob.bridge, "drive_briefing", None) if mob.bridge is not None else None
    if fn is None:
        return 503, {"error": "the briefing is not available right now"}
    try:
        got = fn(mode)
    except Exception as e:
        logs.warn("mobile", "MOBILE_DRIVE_BRIEFING_FAILED", "briefing could not be built", error_type=type(e).__name__)
        return 503, {"error": "the briefing is not available right now"}
    secs = [{"id": str(s.get("id", ""))[:20], "ok": bool(s.get("ok")), "text": str(s.get("text", ""))[:400]}
            for s in (got.get("sections") or [])[:6] if isinstance(s, dict)]
    return 200, {"ask": False, "mode": mode, "ok": bool(got.get("ok")), "sections": secs,
                 "held_by_setting": bool(answer) and answer != mode, "text": str(got.get("text", ""))[:600] if not secs else ""}


def journey(mob, d: dict) -> tuple[int, dict]:
    from . import journey as jn
    v = view(mob)
    if not v["enabled"]:
        return 409, {"error": "the Drive screen is switched off on the computer", "code": "DRIVE_SCREEN_OFF"}
    if not v["journey_on"]:
        return 409, {"error": "journey planning is switched off on the computer", "code": "JOURNEY_OFF"}
    if not mob.rate_ok("drive-journey:*", JOURNEY_ALL_PHONES_PER_MIN):          # shared by all phones (free providers)
        return 429, dict(JOURNEY_BUSY)
    try:
        tp = jn.plan(str(d.get("origin", "")), str(d.get("destination", "")))
    except jn.JourneyError as e:
        return 400, {"error": jn.say(e.code, "en", **e.kw), "error_he": jn.say(e.code, "he", **e.kw), "code": e.code}
    except Exception as e:                                       # never echo a place or a URL from a provider error
        logs.warn("mobile", "MOBILE_DRIVE_JOURNEY_FAILED", "journey could not be planned", error_type=type(e).__name__)
        return 503, {"error": "the trip could not be planned right now"}
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    out = tp.as_dict()
    links = {k: out[k] for k in ("maps_url", "waze_url") if str(out[k]).startswith(LINK_PREFIXES)}
    return 200, {"lines": {"en": tp.lines(now, "en"), "he": tp.lines(now, "he")}, **links,
                 "estimate": out["estimate"], "notes": out["notes"]}


# ---------------------------------------------------------------------- catalog row (the scope is the per-phone switch)
from . import capabilities                       # noqa: E402
from . import features as feat                   # noqa: E402
from . import hebrew                             # noqa: E402

EVIDENCE = "UNIT test_mobile_drive.py + emulated phone test_mobile_phone_e2e.py (scope gating, EN/HE, 390 px, links as buttons; no real phone)"
BLOCKER = "never paired with a real phone"
ABOUT = ("a Drive tab on a paired phone, only for phones you give the drive permission: shows drive mode and what waits for when "
         "you stop (read-only), answers the pre-drive briefing question, and plans a typed trip with Maps and Waze buttons. "
         "The phone cannot switch drive mode or approve, call or message anything, and no location is read")
PI = "the phone page is hosted by the PC in V6; there is no Pi path"
PHONE_BLOCKER = "NOT-RUN: never paired with a real phone"
CAR_BLOCKER = "NOT-RUN: no parked-car session with a real phone and Bluetooth yet (owner-gated, V6 plan G11)"
PHONE_EVIDENCE = "UNIT test_mobile_drive.py + emulated phone test_mobile_phone_e2e.py (390 px, EN + HE; no real phone)"
CAR_EVIDENCE = "UNIT test_mobile_drive.py (the Drive tab data the mounted phone would show; no car session)"
hebrew.TEXT.update({
    ABOUT: "לשונית נהיגה בטלפון מצומד, רק לטלפונים שנתתם להם הרשאת נהיגה: מציגה את מצב הנהיגה ומה ממתין לשתעצרו (לקריאה בלבד), "
           "עונה על שאלת התדריך לפני נסיעה, ומתכננת נסיעה שהוקלדה עם כפתורי Maps ו-Waze. הטלפון לא יכול לשנות את מצב הנהיגה "
           "או לאשר, להתקשר או לשלוח הודעה, ושום מיקום לא נקרא",
    EVIDENCE: "בדיקות יחידה test_mobile_drive.py וטלפון מדומה test_mobile_phone_e2e.py (בדיקת הרשאה, עברית ואנגלית, 390 פיקסלים, קישורים ככפתורים; בלי טלפון אמיתי)",
    BLOCKER: "מעולם לא צומד לטלפון אמיתי",
    PI: "עמוד הטלפון מתארח במחשב ב-V6; אין נתיב ל-Pi",
    PHONE_BLOCKER: "לא הורץ: מעולם לא צומד לטלפון אמיתי",
    CAR_BLOCKER: "לא הורץ: עוד אין בדיקה ברכב חונה עם טלפון אמיתי ו-Bluetooth (בידי הבעלים, V6 G11)",
    PHONE_EVIDENCE: "בדיקות יחידה test_mobile_drive.py וטלפון מדומה test_mobile_phone_e2e.py (390 פיקסלים, עברית ואנגלית; בלי טלפון אמיתי)",
    CAR_EVIDENCE: "בדיקות יחידה test_mobile_drive.py (הנתונים שהטלפון על המעמד היה מציג; בלי בדיקה ברכב)",
    "read; per-phone drive permission": "קריאה; הרשאת נהיגה לכל טלפון",
})

feat.register(FEATURE, "מסך נהיגה בטלפון", "Phone Drive screen", True, risk="low", about=ABOUT, needs=[],
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["read; per-phone drive permission"],
              settings="jarvis phone scopes <id> --grant drive")
capabilities.declare_extra(FEATURE, phone={"status": "implemented_unverified", "evidence": PHONE_EVIDENCE, "blocker": PHONE_BLOCKER,
                                           "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/218"},
                           car={"status": "implemented_unverified", "evidence": CAR_EVIDENCE, "blocker": CAR_BLOCKER})
