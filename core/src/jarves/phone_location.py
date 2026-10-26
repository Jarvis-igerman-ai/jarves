"""Opt-in phone location while driving (V6, issue #339). OFF by default (switch `phone_location`).

The phone page asks the Android OS for permission (browser Geolocation API) and posts a fix to `/api/m/drive/location`
(phone token, scope `drive`). What happens to it, and what never happens:

  * only while drive mode is ON and `phone_location` is on; otherwise the fix is refused and anything held is dropped (fails closed);
  * the coordinates are ROUNDED to 3 decimals (~100 m) before they are kept or used, one fix per phone, IN MEMORY only: never written
    to disk, the audit (which holds the kind only), logs, memory or the ledger; cleared at drive off, on "stop sharing" and after 2 minutes;
  * used for two things only: the ETA from here (the existing free OSRM call, with the rounded point) and "where am I" (one free
    Nominatim reverse lookup with the rounded point). No other provider, no tool, nothing a text turn or the model can reach.
"""
from __future__ import annotations

import math
import os
import threading
import time

import httpx

from . import logs

FEATURE = "phone_location"
DECIMALS = 3
TTL_S = 120.0                 # a fix older than this is gone; the page re-sends every ~15 s while sharing
MAX_AGE_S = 60.0              # a fix whose own timestamp is older (or newer) than this is stale or forged
MAX_ACCURACY_M = 500.0        # a fix less accurate than this is useless for a route and is refused
FIX_RATE_PER_MIN = 12         # per phone
WHERE_RATE_PER_MIN = 6        # per phone, and WHERE_ALL_PER_MIN across phones (free reverse geocoder)
WHERE_ALL_PER_MIN = 10
HTTP_TIMEOUT_S = 8.0
USER_AGENT = "JARVES-journey/1.0 (personal assistant; contact via GitHub igalgerman/jarves)"
RATE_LIMITED = {"error": "too many requests: wait a minute", "error_he": "יותר מדי בקשות: חכו דקה", "code": "DRIVE_RATE_LIMITED"}
OFF = {"error": "phone location is switched off or drive mode is off", "error_he": "מיקום הטלפון כבוי או שמצב נהיגה כבוי", "code": "LOCATION_OFF"}
BAD = {"error": "that position was not usable", "error_he": "המיקום לא היה שמיש", "code": "LOCATION_BAD"}
NONE = {"error": "no current position: share it from the Drive tab first", "error_he": "אין מיקום עדכני: שתפו אותו מלשונית הנהיגה", "code": "LOCATION_NONE"}

_lock = threading.Lock()
_fixes: dict[str, tuple[float, float, float]] = {}      # device id -> (lat, lon, monotonic time); RAM only
clock = time.monotonic                                  # tests replace these
wall = time.time


def _num(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def parse(d: dict, now_ms: float | None = None) -> tuple[float, float] | None:
    """A browser Geolocation payload {lat, lon, accuracy, ts (ms)} -> rounded (lat, lon), or None when anything is off."""
    if not isinstance(d, dict):
        return None
    lat, lon, acc, ts = (_num(d.get(k)) for k in ("lat", "lon", "accuracy", "ts"))
    if None in (lat, lon, acc, ts) or not (-90 <= lat <= 90 and -180 <= lon <= 180) or not (0 <= acc <= MAX_ACCURACY_M):
        return None
    if abs((now_ms if now_ms is not None else wall() * 1000) - ts) > MAX_AGE_S * 1000:
        return None
    return round(lat, DECIMALS), round(lon, DECIMALS)


def allowed(f) -> bool:
    from . import drive_mode
    try:
        return bool(f.active(FEATURE)) and bool(f.active(drive_mode.FEATURE))
    except Exception:
        return False


def clear(dev: str | None = None) -> None:
    with _lock:
        if dev is None:
            _fixes.clear()
        else:
            _fixes.pop(dev, None)


def get(f, dev: str) -> tuple[float, float] | None:
    """The rounded fix of this phone, only while both switches are on and it is fresh; otherwise nothing (and it is dropped)."""
    if not allowed(f):
        clear()
        return None
    with _lock:
        got = _fixes.get(dev)
        if got and clock() - got[2] > TTL_S:
            _fixes.pop(dev, None)
            got = None
    return (got[0], got[1]) if got else None


def post(mob, dev: str, d: dict) -> tuple[int, dict]:
    from . import mobile_drive
    f = mobile_drive._features(mob)
    if d.get("clear") is True:
        clear(dev)
        return 200, {"ok": True, "cleared": True}
    if not allowed(f):
        clear()
        return 409, dict(OFF)
    fix = parse(d)
    if fix is None:
        clear(dev)
        return 400, dict(BAD)
    with _lock:
        now = clock()
        for k in [k for k, v in _fixes.items() if now - v[2] > TTL_S]:      # expired fixes of any phone are dropped here too
            del _fixes[k]
        _fixes[dev] = (fix[0], fix[1], now)
    return 200, {"ok": True}


def where(mob, dev: str) -> tuple[int, dict]:
    """Where am I: one free reverse lookup with the rounded point; the answer is a place name, never coordinates."""
    from . import mobile_drive
    fix = get(mobile_drive._features(mob), dev)
    if fix is None:
        return 409, dict(NONE)
    base = os.environ.get("JARVES_GEOCODE_API_BASE", "https://nominatim.openstreetmap.org")
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S) as c:
            r = c.get(f"{base}/reverse", params={"lat": f"{fix[0]:.{DECIMALS}f}", "lon": f"{fix[1]:.{DECIMALS}f}", "zoom": 16, "format": "json"},
                      headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            name = str(r.json().get("display_name") or "")[:200]
    except Exception as e:                                       # never echo a provider error or the point
        logs.warn("mobile", "MOBILE_DRIVE_WHERE_FAILED", "place name could not be found", error_type=type(e).__name__)
        return 503, {"error": "the place name is not available right now", "error_he": "שם המקום לא זמין כרגע"}
    return 200, {"place": name}


# ---------------------------------------------------------------------- catalog row
from . import capabilities                       # noqa: E402
from . import features as feat                   # noqa: E402
from . import hebrew                             # noqa: E402

EVIDENCE = "UNIT test_phone_location.py (fake geolocation payloads, fake providers; no real phone, no Android permission prompt)"
BLOCKER = "NOT-RUN: never tested on a real Android phone (owner-gated)"
ABOUT = ("lets the phone Drive tab share the phone's position, only while drive mode is on and only for the current trip: "
         "an ETA from here and 'where am I'. Off by default; the phone's own permission prompt asks you; the position is rounded, "
         "kept in memory only and never stored, logged or sent anywhere except the free route and place-name services")
PI = "the phone page is hosted by the PC in V6; there is no Pi path"
PERM = "phone position, in memory only; per-phone drive permission"
hebrew.TEXT.update({
    ABOUT: "מאפשר ללשונית הנהיגה בטלפון לשתף את מיקום הטלפון, רק כשמצב נהיגה דלוק ורק לנסיעה הנוכחית: זמן הגעה מכאן ו'איפה אני'. "
           "כבוי כברירת מחדל; הטלפון עצמו שואל אתכם; המיקום מעוגל, נשמר בזיכרון בלבד ואף פעם לא נשמר, נרשם או נשלח, חוץ משירותי המסלול ושם המקום החינמיים",
    EVIDENCE: "בדיקות יחידה test_phone_location.py (מיקומים מדומים וספקים מדומים; בלי טלפון אמיתי ובלי בקשת הרשאה של אנדרואיד)",
    BLOCKER: "לא הורץ: לא נבדק בטלפון אנדרואיד אמיתי (בידי הבעלים)",
    PI: "עמוד הטלפון מתארח במחשב ב-V6; אין נתיב ל-Pi",
    PERM: "מיקום הטלפון, בזיכרון בלבד; הרשאת נהיגה לכל טלפון",
})
feat.register(FEATURE, "מיקום הטלפון בנהיגה", "Phone location while driving", False, risk="high", about=ABOUT, needs=["phone_drive", "drive_mode"],
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=[PERM], settings="jarvis features on phone_location")
capabilities.declare_extra(FEATURE, phone={"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER,
                                           "roadmap_issue": "https://github.com/Jarvis-igerman-ai/jarves/issues/339"})
