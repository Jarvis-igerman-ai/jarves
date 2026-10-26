"""Journey planning (V6 plan item 3, issue #210): a route/time estimate and Maps/Waze links from places the OWNER typed.

* Origin and destination are typed or spoken text only. There is NO phone or PC location access: "here", "my location",
  "כאן" are refused with a request to name the place.
* Free providers only (Nominatim to find the place, OSRM for the route). They have no traffic data, so every estimate is
  labelled "no live traffic" and carries its provider name and the time it was fetched; a data-age label says how old
  it is. Traffic is never invented.
* Providers are small objects with `estimate(origin, destination, client)`; the plan tries them in order and falls back
  to "links only" when all fail. Tests pass a fake provider or an httpx MockTransport: no network call in tests.
* Links (Google Maps, Waze) are plain strings. Opening one, or sending it to a phone or a contact, is a side effect that
  goes through the normal approvals; this module never opens, sends or dials anything.
Default OFF (feature `journey`).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx

from . import clilang
from . import features as feat
from . import hebrew

FEATURE = "journey"
NO_TRAFFIC = "no live traffic"
MAX_PLACE = 120
STALE_AFTER_S = 15 * 60
HTTP_TIMEOUT_S = 8.0
NOMINATIM_ENV = "JARVES_GEOCODE_API_BASE"
OSRM_ENV = "JARVES_ROUTE_API_BASE"
USER_AGENT = "JARVES-journey/1.0 (personal assistant; contact via GitHub igalgerman/jarves)"
CURRENT_LOCATION = re.compile(r"^(here|my location|current location|where i am|me|כאן|פה|המיקום שלי|המיקום הנוכחי|איפה שאני)$", re.I)


GEOCODE_MIN_INTERVAL_S = 1.0         # Nominatim usage policy: at most 1 request per second, one User-Agent that identifies us
GEOCODE_CACHE_TTL_S = 600.0          # identical queries are answered from memory for ~10 minutes (the policy expects caching)
GEOCODE_CACHE_MAX = 256
_clock = time.monotonic              # tests replace these two with a fake clock
_sleep = time.sleep


class _Geocoder:
    """Process-wide, shared by every phone and the PC: a short geocode cache and the 1 request/s spacing."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, tuple[float, float]]] = {}
        self.last = -1e9

    def get(self, key: str):
        with self.lock:
            hit = self.cache.get(key)
            if hit and _clock() - hit[0] < GEOCODE_CACHE_TTL_S:
                return hit[1]
            self.cache.pop(key, None)
            return None

    def put(self, key: str, value: tuple[float, float]) -> None:
        with self.lock:
            if len(self.cache) >= GEOCODE_CACHE_MAX:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (_clock(), value)

    def wait_turn(self) -> None:
        """Block until at least GEOCODE_MIN_INTERVAL_S has passed since the previous request (the lock serialises phones)."""
        with self.lock:
            gap = GEOCODE_MIN_INTERVAL_S - (_clock() - self.last)
            if gap > 0:
                _sleep(gap)
            self.last = _clock()


GEOCODER = _Geocoder()


class JourneyError(Exception):
    """A user-facing problem; `code` is a key of T."""

    def __init__(self, code: str, **kw) -> None:
        super().__init__(code)
        self.code, self.kw = code, kw


T: dict[str, tuple[str, str]] = {
    "JOURNEY_OFF": ("תכנון נסיעה כבוי. להפעלה: jarvis features on journey", "Journey planning is off. Turn it on with: jarvis features on journey"),
    "JOURNEY_NEED_PLACE": ("אמרו או הקלידו מאיפה ולאן. אני לא ניגש למיקום של הטלפון או המחשב.",
                           "Say or type where from and where to. I do not read your phone or PC location."),
    "JOURNEY_BAD_PLACE": ("שם המקום ריק, ארוך מדי או מכיל תווים לא תקינים.", "The place is empty, too long or has invalid characters."),
    "JOURNEY_NOT_FOUND": ("לא מצאתי את המקום \"{place}\". נסו כתובת מלאה יותר.", "I could not find \"{place}\". Try a fuller address."),
    "JOURNEY_PROVIDER_DOWN": ("שירות המסלולים לא זמין כרגע. הקישורים עדיין עובדים.", "The route service is not available now. The links still work."),
    "JOURNEY_BAD_TIME": ("שעה לא תקינה. כתבו HH:MM.", "Invalid time. Write HH:MM."),
    "JOURNEY_LINE": ("{origin} אל {destination}: כ-{minutes} דקות, {km} ק\"מ ({provider}, {traffic}, {age})",
                     "{origin} to {destination}: about {minutes} min, {km} km ({provider}, {traffic}, {age})"),
    "JOURNEY_LINKS_ONLY": ("אין הערכת זמן. אפשר לפתוח את הניווט בקישורים.", "No time estimate. You can open navigation with the links."),
    "JOURNEY_ARRIVE": ("אם תצאו ב-{depart} תגיעו בסביבות {arrive}.", "Leaving at {depart} you arrive around {arrive}."),
    "AGE_NOW": ("עכשיו", "just now"),
    "AGE_MIN": ("לפני {n} דקות", "{n} min ago"),
    "AGE_STALE": ("ישן, לפני {n} דקות", "stale, {n} min ago"),
    "TRAFFIC_NONE": ("בלי תנועה בזמן אמת", NO_TRAFFIC),
}


def say(code: str, lang: str = "en", **kw) -> str:
    he, en = T[code]
    return (he if lang == "he" else en).format(**kw)


# ---------------------------------------------------------------------- input
def clean_place(text: str) -> str:
    """One typed place: whitespace folded, bidi/invisible format characters removed, no control characters, at most MAX_PLACE. Raises JourneyError."""
    from .drive_mode import strip_invisible          # one sanitiser with the drive-mode queue (bidi / zero-width removed)
    s = " ".join(strip_invisible(text or "").split())
    if not s or len(s) > MAX_PLACE or any(ord(c) < 32 for c in s):
        raise JourneyError("JOURNEY_BAD_PLACE")
    if CURRENT_LOCATION.match(s):
        raise JourneyError("JOURNEY_NEED_PLACE")          # there is no location access, by design
    return s


def parse_hhmm(text: str, today: datetime) -> datetime:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (text or "").strip())
    if not m:
        raise JourneyError("JOURNEY_BAD_TIME")
    t = today.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    return t if t >= today else t + timedelta(days=1)


# ---------------------------------------------------------------------- links
def maps_link(origin: str, destination: str) -> str:
    """Google Maps directions URL (https://developers.google.com/maps/documentation/urls/get-started, api=1).
    An empty origin is left out: the Maps app then starts from where the phone is."""
    args = {"api": 1, "origin": origin, "destination": destination, "travelmode": "driving"}
    q = urlencode({k: v for k, v in args.items() if v != ""}, quote_via=quote)
    return "https://www.google.com/maps/dir/?" + q


def waze_link(destination: str) -> str:
    """Waze deep link by destination text; Waze starts from where the phone is, which only the phone's own app reads."""
    return "https://waze.com/ul?" + urlencode({"q": destination, "navigate": "yes"}, quote_via=quote)


# ---------------------------------------------------------------------- estimate + labels
@dataclass(frozen=True)
class RouteEstimate:
    origin: str
    destination: str
    distance_m: float
    duration_s: float
    provider: str
    fetched_at: datetime            # timezone-aware UTC
    live_traffic: bool = False

    @property
    def minutes(self) -> int:
        return max(1, round(self.duration_s / 60))

    @property
    def km(self) -> float:
        return round(self.distance_m / 1000, 1)


def age_label(fetched_at: datetime, now: datetime, lang: str = "en") -> str:
    n = max(0, int((now - fetched_at).total_seconds() // 60))
    if n < 1:
        return say("AGE_NOW", lang)
    return say("AGE_STALE" if n * 60 > STALE_AFTER_S else "AGE_MIN", lang, n=n)


def label(est: RouteEstimate, now: datetime, lang: str = "en") -> str:
    traffic = say("TRAFFIC_NONE", lang) if not est.live_traffic else "live traffic"
    return say("JOURNEY_LINE", lang, origin=est.origin, destination=est.destination, minutes=est.minutes, km=est.km,
               provider=est.provider, traffic=traffic, age=age_label(est.fetched_at, now, lang))


class OsrmProvider:
    """Nominatim (find the place) + the OSRM demo server (route). Both free, neither has traffic, so live_traffic is
    always False. Base URLs can be changed with JARVES_GEOCODE_API_BASE / JARVES_ROUTE_API_BASE (tests and self-hosting)."""
    name = "OpenStreetMap (Nominatim + OSRM)"

    def __init__(self, origin_fix: tuple[float, float] | None = None) -> None:
        self.origin_fix = origin_fix          # (lat, lon) ROUNDED by phone_location (#339): only the start; never stored here

    def _geocode(self, place: str, client: httpx.Client) -> tuple[float, float]:
        key = place.casefold()
        cached = GEOCODER.get(key)
        if cached is not None:
            return cached
        GEOCODER.wait_turn()
        base = os.environ.get(NOMINATIM_ENV, "https://nominatim.openstreetmap.org")
        r = client.get(f"{base}/search", params={"q": place, "format": "json", "limit": 1}, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        rows = r.json()
        if not rows:
            raise JourneyError("JOURNEY_NOT_FOUND", place=place)
        got = (float(rows[0]["lon"]), float(rows[0]["lat"]))
        GEOCODER.put(key, got)
        return got

    def estimate(self, origin: str, destination: str, client: httpx.Client, now: datetime) -> RouteEstimate:
        a = (self.origin_fix[1], self.origin_fix[0]) if self.origin_fix else self._geocode(origin, client)
        b = self._geocode(destination, client)
        base = os.environ.get(OSRM_ENV, "https://router.project-osrm.org")
        r = client.get(f"{base}/route/v1/driving/{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}", params={"overview": "false"},
                       headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        route = r.json()["routes"][0]
        return RouteEstimate(origin, destination, float(route["distance"]), float(route["duration"]), self.name, now, False)


@dataclass
class TripPlan:
    origin: str
    destination: str
    estimate: RouteEstimate | None
    maps_url: str
    waze_url: str
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    notes: list[str] = field(default_factory=list)       # codes of T (provider failures, links only)

    def lines(self, now: datetime, lang: str = "en") -> list[str]:
        out = [label(self.estimate, now, lang)] if self.estimate else [say("JOURNEY_LINKS_ONLY", lang)]
        out += [say(c, lang) for c in self.notes]
        if self.estimate and self.depart_at and self.arrive_at:
            out.append(say("JOURNEY_ARRIVE", lang, depart=self.depart_at.strftime("%H:%M"), arrive=self.arrive_at.strftime("%H:%M")))
        return out

    def as_dict(self) -> dict:
        e = self.estimate
        return {"origin": self.origin, "destination": self.destination, "maps_url": self.maps_url, "waze_url": self.waze_url,
                "estimate": None if e is None else {"minutes": e.minutes, "km": e.km, "provider": e.provider,
                                                    "fetched_at": e.fetched_at.isoformat(), "live_traffic": e.live_traffic,
                                                    "traffic_label": NO_TRAFFIC if not e.live_traffic else "live traffic"},
                "depart_at": self.depart_at.isoformat() if self.depart_at else None,
                "arrive_at": self.arrive_at.isoformat() if self.arrive_at else None, "notes": self.notes}


def plan(origin: str, destination: str, *, providers=None, client: httpx.Client | None = None, now: datetime | None = None,
         depart_at: datetime | None = None, origin_fix: tuple[float, float] | None = None) -> TripPlan:
    """Plan one trip from two typed places. Provider failures never raise: the plan falls back to links only.
    `origin_fix` (rounded phone position, #339) replaces the typed origin; the Maps link then has no origin (the app uses the phone's)."""
    o, d = ("here" if origin_fix else clean_place(origin)), clean_place(destination)
    now = now or datetime.now(timezone.utc)
    own = client is None
    client = client or httpx.Client(timeout=HTTP_TIMEOUT_S)
    est, notes = None, []
    try:
        for p in (providers if providers is not None else [OsrmProvider(origin_fix)]):
            try:
                est = p.estimate(o, d, client, now)
                break
            except JourneyError as e:
                if e.code == "JOURNEY_NOT_FOUND":
                    raise
                notes.append(e.code)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
                notes = [c for c in notes if c != "JOURNEY_PROVIDER_DOWN"] + ["JOURNEY_PROVIDER_DOWN"]
    finally:
        if own:
            client.close()
    arrive = (depart_at + timedelta(seconds=est.duration_s)) if est and depart_at else None
    return TripPlan(o, d, est, maps_link("" if origin_fix else o, d), waze_link(d), depart_at if est else None, arrive, notes)


# ---------------------------------------------------------------------- CLI: jarvis journey plan --from A --to B [--depart HH:MM] [--links-only]
def add_cli(sub) -> None:
    p = sub.add_parser("journey", help="journey: jarvis journey plan --from <place> --to <place> [--depart HH:MM] [--links-only] "
                                       "[--json]. Places are what you type; no location is read; free providers, no live traffic")
    p.add_argument("action", nargs="?", default="plan", choices=["plan", "links"])
    p.add_argument("--from", dest="origin", default="", help="where you start (typed)")
    p.add_argument("--to", dest="destination", default="", help="where you go (typed)")
    p.add_argument("--depart", default="", help="HH:MM, to get an arrival time")
    p.add_argument("--links-only", action="store_true", help="no network at all: only the Maps and Waze links")
    p.add_argument("--json", action="store_true")


def cmd_journey(a, cfg, home) -> int:
    import sys
    from .cli import _cli_features
    lang = "he" if clilang.fmt("he", "en") == "he" else "en"
    if not _cli_features(cfg, home).active(FEATURE):
        print(clilang.fmt(say("JOURNEY_OFF", "he"), say("JOURNEY_OFF", "en")), file=sys.stderr)
        return 2
    now = datetime.now(timezone.utc)
    try:
        depart = parse_hhmm(a.depart, datetime.now().astimezone()) if a.depart else None
        providers = [] if (a.links_only or a.action == "links") else None
        tp = plan(a.origin, a.destination, providers=providers, now=now, depart_at=depart)
    except JourneyError as e:
        print(say(e.code, lang, **e.kw), file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(tp.as_dict(), ensure_ascii=False))
    else:
        for line in tp.lines(now, lang):
            print(line)
        print("Google Maps:", tp.maps_url)
        print("Waze:", tp.waze_url)
    return 0


COMMANDS = {"journey": cmd_journey}


# ---------------------------------------------------------------------- catalog (default OFF)
EVIDENCE = "UNIT test_v6_journey.py (fake providers and httpx MockTransport; no network, no location)"
BLOCKER = "no real Nominatim/OSRM run recorded; no phone or Maps/Waze app opened by the owner"
ABOUT = ("a trip estimate from places you type: minutes and km from free map services (no live traffic, labelled), "
         "the age of the data, and Google Maps / Waze links. It never reads your location and never opens a link by itself")
PI = "no vehicle path on the Raspberry Pi in V6 (GitHub issue #210 tracks the PC module)"
hebrew.TEXT.update({
    ABOUT: "הערכת נסיעה ממקומות שאתם מקלידים: דקות וק\"מ משירותי מפות חינמיים (בלי תנועה בזמן אמת, מסומן), גיל הנתונים, "
           "וקישורים ל-Google Maps ול-Waze. הוא אף פעם לא קורא את המיקום שלכם ולא פותח קישור מעצמו",
    EVIDENCE: "בדיקות יחידה test_v6_journey.py (ספקים מזויפים ו-httpx MockTransport; בלי רשת, בלי מיקום)",
    BLOCKER: "לא תועדה הרצה אמיתית של Nominatim/OSRM; הבעלים עוד לא פתח אפליקציית Maps/Waze בטלפון",
    "network to free map services only when you ask; no location": "רשת לשירותי מפות חינמיים רק כשמבקשים; בלי מיקום",
    PI: "אין נתיב רכב על ה-Raspberry Pi ב-V6 (נושא #210 עוקב אחר מודול ה-PC)",
})

feat.register(FEATURE, "תכנון נסיעה", "Journey planning", False, risk="low", about=ABOUT,
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["read", "network to free map services only when you ask; no location"],
              settings="jarvis journey plan --from A --to B")
