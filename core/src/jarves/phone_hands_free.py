"""Hands-free Drive voice on the phone (V6, issue #219). OFF by default (switch `phone_hands_free`, beta).

Honest scope: a phone web page can use the microphone ONLY while the page is open, visible and the screen is on. So this is a
FOREGROUND session, never background listening and never a wake word: the owner taps "Start Drive voice", the page keeps
listening (voice-activity detection in the page), takes turns automatically and ends the moment the page is hidden, the screen
lock is lost, the audio route (Bluetooth car audio) is lost, drive mode goes off, or a timeout passes. Push-to-talk stays the default.

Server side (this module): the session clock and the rules. Every spoken turn goes through the SAME path as a typed phone
chat message, so every drive-mode Gate rule applies unchanged (read-only answers allowed; consequential tools are queued
until you stop; emergency / hang-up / stop always work; untrusted text never acts). Nothing here approves, calls, sends or
opens anything, and the reply is only ever shown and read aloud, never fed back as an instruction. Sessions are RAM only.
"""
from __future__ import annotations

import re
import time

from . import logs

FEATURE = "phone_hands_free"
MAX_SESSION_S = 1800.0        # hard limit: 30 minutes, then the owner starts a new session on purpose
IDLE_S = 90.0                 # no heard speech for this long ends the session
MAX_TURNS = 80
START_RATE_PER_MIN = 4
TURN_RATE_PER_MIN = 12
STOP_WORDS = {"stop", "stop it", "stop talking", "be quiet", "quiet", "עצור", "עצרי", "די", "שקט", "סטופ"}
END_WORDS = {"hang up", "end session", "end drive voice", "goodbye", "סיים", "סיום", "נתק", "להתראות"}
REASONS = ("user", "route_lost", "wake_lock_lost", "hidden", "mic_lost", "error")      # what the page may report
T = {
    "HF_OFF": ("קול נהיגה ללא ידיים כבוי, או שמצב נהיגה כבוי.", "Hands-free Drive voice is off, or drive mode is off."),
    "HF_NOT_STARTED": ("אין שיחת נהיגה פעילה. התחילו אחת בלחיצה.", "No Drive voice session is running. Start one with a tap."),
    "HF_EXPIRED": ("שיחת הנהיגה הסתיימה (זמן או חוסר פעילות). המיקרופון סגור. התחילו שוב בלחיצה או השתמשו בלחצן הדיבור.",
                   "The Drive voice session ended (time limit or quiet). The microphone is closed. Start again with a tap or use push-to-talk."),
    "HF_DRIVE_OFF": ("מצב נהיגה כבה, ולכן שיחת הנהיגה הסתיימה.", "Drive mode went off, so the Drive voice session ended."),
    "HF_ROUTE_LOST": ("חיבור השמע לרכב נותק, ולכן השיחה ללא ידיים הסתיימה והמיקרופון סגור. השתמשו בלחצן הדיבור.",
                      "The car audio disconnected, so hands-free ended and the microphone is closed. Use push-to-talk."),
    "HF_WAKE_LOST": ("מסך הטלפון כבה או ננעל, ולכן השיחה הסתיימה. הטלפון לא מקשיב ברקע.",
                     "The phone screen locked, so the session ended. The phone does not listen in the background."),
    "HF_HIDDEN": ("העמוד לא על המסך, ולכן השיחה הסתיימה. הטלפון לא מקשיב ברקע.",
                  "The page left the screen, so the session ended. The phone does not listen in the background."),
    "HF_MIC_LOST": ("המיקרופון נעלם, ולכן השיחה הסתיימה.", "The microphone went away, so the session ended."),
    "HF_ENDED": ("שיחת הנהיגה הסתיימה. המיקרופון סגור.", "The Drive voice session ended. The microphone is closed."),
    "HF_NO_STT": ("זיהוי הדיבור לא זמין כרגע.", "Speech recognition is not available right now."),
    "HF_RATE": ("יותר מדי בקשות: חכו דקה", "too many requests: wait a minute"),
}
_END_CODE = {"route_lost": "HF_ROUTE_LOST", "wake_lock_lost": "HF_WAKE_LOST", "hidden": "HF_HIDDEN", "mic_lost": "HF_MIC_LOST"}
clock = time.monotonic                  # tests replace this
_sessions: dict[str, dict] = {}         # device id -> {"t0", "last", "turns"}; RAM only


def msg(code: str, **extra) -> dict:
    he, en = T[code]
    return {"code": code, "error": en, "error_he": he, **extra}


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").lower()).split())


def classify(text: str) -> str:
    """'stop' (stop speaking), 'end' (end the session) or '' for everything else. Short utterances only, so a sentence that
    merely contains the word 'stop' is a normal turn for the Gate to judge."""
    n = norm(text)
    words = n.split()
    if n in STOP_WORDS or (words and words[0] in {"stop", "עצור", "סטופ"} and len(words) <= 2):
        return "stop"
    return "end" if n in END_WORDS else ""


def allowed(f) -> bool:
    from . import drive_mode
    try:
        return all(bool(f.active(x)) for x in (FEATURE, "phone_drive", drive_mode.FEATURE))
    except Exception:
        return False


def clear() -> None:
    _sessions.clear()


def _live(f, dev: str) -> tuple[dict | None, str]:
    """The running session, or (None, code) and it is dropped. Fails closed: the switches are re-checked on every call."""
    s = _sessions.get(dev)
    if s is None:
        return None, "HF_NOT_STARTED"
    if not allowed(f):
        _sessions.pop(dev, None)
        return None, "HF_DRIVE_OFF"
    now = clock()
    if now - s["t0"] > MAX_SESSION_S or now - s["last"] > IDLE_S or s["turns"] >= MAX_TURNS:
        _sessions.pop(dev, None)
        return None, "HF_EXPIRED"
    return s, ""


def start(mob, dev: str) -> tuple[int, dict]:
    from . import mobile_drive
    if not allowed(mobile_drive._features(mob)):
        clear()
        return 409, msg("HF_OFF")
    _sessions[dev] = {"t0": clock(), "last": clock(), "turns": 0}
    return 200, {"ok": True, "max_s": MAX_SESSION_S, "idle_s": IDLE_S, "max_turns": MAX_TURNS,
                 "note": "Foreground only: keep this page open and the screen on. Nothing listens in the background.",
                 "note_he": "בחזית בלבד: השאירו את העמוד פתוח ואת המסך דלוק. שום דבר לא מקשיב ברקע."}


def turn(mob, dev: str, wav: bytes) -> tuple[int, dict]:
    from . import drive_mode, mobile_drive
    s, why = _live(mobile_drive._features(mob), dev)
    if s is None:
        return 409, msg(why)
    tr = getattr(mob.bridge, "voice_transcribe", None) if mob.bridge is not None else None
    if tr is None:
        return 503, msg("HF_NO_STT")
    try:
        text = str(tr(wav, dev) or "").strip()
    except Exception as e:                                       # never echo a provider error
        logs.warn("mobile", "MOBILE_HANDS_FREE_STT_FAILED", "speech recognition failed", error_type=type(e).__name__)
        return 503, msg("HF_NO_STT")
    if not text:
        return 200, {"heard": False, "action": "listen"}         # silence or noise: not a turn, the idle clock keeps running
    s["last"] = clock()
    kind = classify(text)
    if kind == "stop":                                           # 'stop' always stops speech: no model call, nothing else happens
        return 200, {"heard": True, "action": "stop_speech", "transcript": text[:200]}
    if kind == "end":
        _sessions.pop(dev, None)
        return 200, {"heard": True, "action": "end", **msg("HF_ENDED")}
    s["turns"] += 1
    try:
        out = mob.bridge.chat(text, dev)                         # the ordinary phone chat path: the drive-mode Gate decides every tool
    except Exception as e:
        logs.warn("mobile", "MOBILE_HANDS_FREE_FAILED", "a hands-free turn failed", error_type=type(e).__name__)
        return 502, {"error": "JARVES could not answer right now", "error_he": "JARVES לא הצליח לענות כרגע", "code": "HF_FAILED"}
    s["last"] = clock()
    return 200, {"heard": True, "action": "speak", "transcript": text[:200],
                 "reply": drive_mode.shorten(str(out.get("reply", ""))), "ok": bool(out.get("ok", False)),
                 "waiting": len(drive_mode.queue_for(mob.home).items())}


def end(mob, dev: str, reason: str) -> tuple[int, dict]:
    """The page ends the session (and has already closed the microphone) and says why; the answer states it truthfully."""
    _sessions.pop(dev, None)
    reason = reason if reason in REASONS else "user"
    return 200, {"ok": True, "ended": True, "reason": reason, **msg(_END_CODE.get(reason, "HF_ENDED"))}


# ---------------------------------------------------------------------- catalog row
from . import capabilities                       # noqa: E402
from . import features as feat                   # noqa: E402
from . import hebrew                             # noqa: E402

EVIDENCE = "UNIT test_phone_hands_free.py (fake speech-to-text, fake chat, fake clock, car-audio route loss as the page reports it; no real phone or car)"
BLOCKER = "NOT-RUN: never tested on a real Android phone with a Bluetooth car (owner-gated)"
ABOUT = ("a foreground 'Drive voice' session on the phone Drive tab: it keeps listening while the page is open and the screen is on, "
         "takes turns by itself, 'stop' always stops speech, and it ends truthfully when the screen locks, the car audio disconnects, "
         "drive mode ends or a time limit passes. Off by default; push-to-talk stays the default. It never listens in the background")
PI = "the phone page is hosted by the PC in V6; there is no Pi path"
CAR_BLOCKER = "NOT-RUN: no parked-car session yet (owner-gated)"
PERM = "phone microphone while the page is visible; per-phone drive and voice permission"
hebrew.TEXT.update({
    ABOUT: "שיחת 'קול נהיגה' בחזית בלשונית הנהיגה בטלפון: ממשיכה להקשיב כל עוד העמוד פתוח והמסך דלוק, מחליפה תורות לבד, 'עצור' תמיד עוצר "
           "את הדיבור, ומסתיימת בכנות כשהמסך ננעל, השמע ברכב מתנתק, מצב הנהיגה נגמר או שעובר זמן מרבי. כבוי כברירת מחדל; לחצן הדיבור נשאר ברירת המחדל. "
           "אף פעם לא מקשיבה ברקע",
    EVIDENCE: "בדיקות יחידה test_phone_hands_free.py (זיהוי דיבור מדומה, שיחה מדומה, שעון מדומה, ניתוק שמע ברכב כפי שהעמוד מדווח; בלי טלפון או רכב אמיתיים)",
    BLOCKER: "לא הורץ: לא נבדק בטלפון אנדרואיד אמיתי עם רכב Bluetooth (בידי הבעלים)",
    PI: "עמוד הטלפון מתארח במחשב ב-V6; אין נתיב ל-Pi",
    CAR_BLOCKER: "לא הורץ: עוד אין בדיקה ברכב חונה (בידי הבעלים)",
    PERM: "מיקרופון הטלפון כשהעמוד גלוי; הרשאת נהיגה וקול לכל טלפון",
})
feat.register(FEATURE, "קול נהיגה ללא ידיים בטלפון", "Phone hands-free Drive voice", False, risk="high", about=ABOUT,
              needs=["phone_drive", "drive_mode"], stage="beta",
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=[PERM], settings="jarvis features on phone_hands_free")
capabilities.declare_extra(FEATURE, phone={"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER,
                                           "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/219"},
                           car={"status": "implemented_unverified", "evidence": EVIDENCE,
                                "blocker": CAR_BLOCKER})
