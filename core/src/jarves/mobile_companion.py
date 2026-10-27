"""Companion API v1 (V7-A1): the read-only `GET /api/m/companion/info` of the phone API plus the catalog row.
The contract itself is docs/api/companion_v1.md and docs/api/companion_v1.json (guarded by test_companion_contract.py).
No new write capability, no new auth: any valid paired token may read it."""
from __future__ import annotations

import time

from . import capabilities, hebrew
from . import features as feat

FEATURE = "companion_api"
API_VERSION = 1
API_HEADER = "X-Jarves-Api"
INFO_PATH = "/api/m/companion/info"
# Features an app cares about (only those that are registered are reported).
APP_FEATURES = ("companion_api", "mobile_access", "goal_plans", "preferences", "drive_mode", "drive_briefing")
SUPPORTED_SCOPES = ("chat", "voice", "status", "approvals", "tasks", "prefs", "drive", "browser")


def _features(mob):
    from .mobile_drive import _features as f
    return f(mob)


def info(mob, dev: dict) -> dict:
    """What an app needs before it does anything else. 'pc' tells the app whether the brain answers right now:
    the Core being unreachable is "offline" (the app shows its offline state, it never queues writes by itself)."""
    from . import __version__
    try:
        core = (mob.bridge.status() if mob.bridge else {"core": "unknown"}).get("core")
    except Exception:
        core = "unreachable"
    flags: dict = {}
    try:
        f = _features(mob)
        for fid in APP_FEATURES:
            if fid in feat.CATALOG:
                flags[fid] = {"stage": feat.stage_of(fid), "enabled": bool(f.enabled(fid))}
        beta = bool(f.beta_on())
    except Exception:
        beta = False
    return {"api": API_VERSION, "api_min": 1, "core_version": __version__,
            "server_time": time.time(), "pc": {"state": "online" if core in ("running", "ok") else "offline", "core": core},
            "scopes_supported": list(SUPPORTED_SCOPES), "scopes": dev["scopes"],
            "phone_approvals": mob.settings()["phone_approvals"], "beta_features": beta, "features": flags}


ABOUT = ("a versioned, documented contract for the phone API so a future Android app can pair, chat, list tasks and approvals "
         "and pause or cancel plans. It adds one read-only route (/api/m/companion/info) and no new permission: the PC stays the "
         "brain, approvals from a phone stay off unless you turn them on, and GitHub and PC-only plan steps stay on the computer")
EVIDENCE = "UNIT test_companion_contract.py (contract vs routes vs the PWA, fake Core over HTTP; no Android client exists yet)"
BLOCKER = "no Android app has used it yet"
PI = "the phone API is hosted by the PC; there is no Pi path"
hebrew.TEXT.update({
    ABOUT: "חוזה מתועד ומגורסא ל-API של הטלפון, כדי שאפליקציית אנדרואיד עתידית תוכל להצטמד, לשוחח, לראות משימות ואישורים ולהשהות או לבטל תוכניות. "
           "נוסף נתיב אחד לקריאה בלבד (/api/m/companion/info) וללא הרשאה חדשה: המחשב נשאר המוח, אישורים מהטלפון נשארים כבויים אלא אם הדלקתם אותם, "
           "ואישורי GitHub וצעדי תוכנית של המחשב בלבד נשארים במחשב",
    EVIDENCE: "בדיקות יחידה test_companion_contract.py (חוזה מול נתיבים מול ה-PWA, ליבה מדומה מעל HTTP; עוד אין לקוח אנדרואיד)",
    BLOCKER: "אף אפליקציית אנדרואיד עוד לא השתמשה בו",
    PI: "ה-API של הטלפון מתארח במחשב; אין נתיב ל-Pi",
    "read; any paired device": "קריאה; כל מכשיר מצומד",
})
feat.register(FEATURE, "ממשק אפליקציית הטלפון (חוזה)", "Phone companion API (contract)", True, risk="low", about=ABOUT, needs=[],
              targets={"windows": {"status": "implemented_unverified", "evidence": EVIDENCE, "blocker": BLOCKER},
                       "linux_arm64_pi": {"status": "not_applicable", "blocker": PI}},
              permissions=["read; any paired device"], settings="jarvis phone")
