"""Browser Studio B4 (V7, issue #399): the phone viewer for the pilot's live view and the owner's takeover.

A PAIRED phone that was given the `browser` scope can watch the pilot's local test page and, if the owner allows it, take over
from the phone. It is the SAME LiveView object the dashboard uses (so the same server rules: read-only picture, grant, 5-minute
expiry, local test pages only, owner input to the page only, nothing typed is logged or shown to the assistant). What the phone
path adds, all enforced HERE on the server:
* its own beta switch `browser_phone_view` (OFF by default) on top of `browser_liveview` and, for input, `browser_takeover`;
* a paired device with the `browser` scope (not in the default scopes: granted on the computer, like tasks/prefs/drive);
* HTTPS only (or loopback): keystrokes and pictures never travel over plain http on the LAN;
* it can only VIEW or TAKE OVER a pilot that is already running; it can NOT start a pilot (that is the computer, with a URL typed
  there), it can Stop it;
* a small request rate per device; the audit holds the device and the action kind only.
Routes: GET /api/m/browser; POST /api/m/browser/{start,frame,takeover,return,input,stop}. The grant is in the POST body."""
from __future__ import annotations

from . import browser_liveview as bl
from . import features as feat
from . import logs
from .browser_pilot import PilotError
from .calm import targets

FEATURE = "browser_phone_view"
SCOPE = "browser"
RATE_PER_MIN = 240                 # frames at 1/s plus inputs: well inside; a flood is refused
OPS = ("start", "frame", "takeover", "return", "input", "stop")


def _on(mob, fid: str) -> bool:
    try:
        from .mobile_drive import _features                   # the same helper the Drive tab uses (the real MobileAccess has no drive_features())
        f = _features(mob)
        return bool(f is not None and f.active(fid))
    except Exception:                                       # noqa: BLE001 - unreadable = off
        return False


def status(mob) -> dict:
    v = bl.ACTIVE
    return {"enabled": _on(mob, FEATURE), "session": v is not None, "state": (v.session.state if v is not None else ""),
            "takeover": bool(v is not None and v.takeover_enabled())}


def handle(mob, dev_id: str, op: str, body: dict, scheme: str) -> tuple[int, dict]:
    """(status, json). Never raises. `op` is one of OPS."""
    if not _on(mob, FEATURE) or not _on(mob, bl.FEATURE):
        return 403, {"error": "the phone browser view is off (on the computer: jarvis features on browser_phone_view)", "code": "PHONE_BROWSER_OFF"}
    if scheme != "https":
        return 409, {"error": "the phone browser view needs the secure (https) phone link", "code": "PHONE_BROWSER_NO_TLS"}
    v = bl.ACTIVE
    if v is None:
        return 404, {"error": "no browser pilot is running; start it on the computer", "code": "PHONE_BROWSER_NO_SESSION"}
    if op not in OPS:
        return 404, {"error": "not found"}
    if not mob.rate_ok("browser:" + dev_id, RATE_PER_MIN):
        return 429, {"error": "too many requests", "code": "PHONE_BROWSER_RATE"}
    grant = body.get("grant")
    try:
        if op == "start":
            out = v.start()
        elif op == "frame":
            out = v.frame(grant)
        elif op == "takeover":
            out = v.takeover(grant)
        elif op == "return":
            out = v.give_back(grant, body.get("resume"))
        elif op == "input":
            out = v.human(grant, body.get("kind"), body.get("data"))
        else:
            out = v.stop()
        return 200, out
    except bl.LiveError as e:
        return e.status, {"error": e.message, "code": e.code}
    except PilotError as e:                                  # noqa: BLE001 - fixed words only
        return 409, {"error": e.message, "code": e.code}
    except Exception:                                        # noqa: BLE001
        logs.warn("mobile", "MOBILE_BROWSER_FAILED", "phone browser view failed")
        return 500, {"error": "the browser view failed", "code": "PHONE_BROWSER_FAILED"}


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "תצוגת הדפדפן בטלפון", "Browser view on the phone", False, probe=_probe,
              needs=["browser_liveview", "mobile_access"], risk="high", stage="beta",
              about="watch the browser pilot's local test page from a paired phone (secure link only) and, with the take-over switch, "
                    "click and type there; the phone can stop it but not start it",
              targets=targets("test_mobile_browser.py"),
              permissions=["shows a picture of the pilot page on a paired phone", "your taps and typing on the phone go to the page"],
              settings="jarvis features on browser_phone_view")
