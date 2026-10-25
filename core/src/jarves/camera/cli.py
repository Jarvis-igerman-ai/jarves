"""`jarvis camera status | probe | test-frame`: the terminal twin of the dashboard Camera section (V4.5 D).

status      live state from the running Core (Off / Available / Previewing / ...), else a sensorless probe
probe       is a camera listed? Opens NOTHING, takes no picture (works with the Core stopped)
test-frame  asks the running Core for ONE local test picture: measured and dropped, never saved or sent
Preview is a dashboard/face feature (it needs a screen); enrolment and face tools stay in `python -m jarves.camera`.
"""
from __future__ import annotations

import sys

from .. import clilang


def add_cli(sub) -> None:
    p = sub.add_parser("camera", help="camera: status / probe (device check, opens nothing) / test-frame (one local "
                                      "test picture, never sent anywhere)")
    p.add_argument("action", nargs="?", default="status", choices=["status", "probe", "test-frame"])


def _fmt(he: str, en: str) -> str:
    return clilang.fmt(he, en)


def _print_view(v: dict) -> None:
    from .ux import explain, result_line
    names = {"off": ("כבויה", "Off"), "available": ("זמינה (החיישן סגור)", "Available (sensor closed)"),
             "previewing": ("תצוגה מקומית", "Previewing locally"), "capturing": ("מצלמת עכשיו", "Capturing"),
             "sending": ("שולח תמונה ל-AI", "Sending to AI"), "busy": ("עסוקה", "Busy"), "error": ("שגיאה", "Error")}
    he, en = names.get(v["state"], (v["state"], v["state"]))
    print(_fmt(f"מצלמה: {he}", f"Camera: {en}"))
    print(_fmt(f"מכשיר: {v['device']['detail']}", f"Device: {v['device']['detail']}"))
    print(_fmt("תמונה: נשארת במחשב (לא נשלחת)" if v["data_flow"] == "local_only" else "תמונה: נשלחת עכשיו לספק AI",
               "Picture: stays on this computer (not sent)" if v["data_flow"] == "local_only"
               else "Picture: being sent to the AI provider now"))
    r_he, r_en = result_line(v.get("last_result"))
    print(_fmt("בקשה אחרונה: " + r_he, "Last request: " + r_en))
    if v.get("error"):
        e = v["error"]
        print(_fmt(f"{e['he']} {e['action_he']}", f"{e['en']} {e['action_en']}"))
    elif v.get("last_result") and v["last_result"].get("error"):
        e = v["last_result"]["error"]
        print(_fmt(f"{e['he']} {e['action_he']}", f"{e['en']} {e['action_en']}"))
    if v.get("core_running") is False:
        print(_fmt("JARVES לא רץ: זו בדיקה בלי לפתוח את החיישן. להפעלה: jarvis start",
                   "JARVES is not running: this is a sensorless check. Start it with: jarvis start"))


def cmd_camera(a, cfg, home) -> int:
    from ..cli import _ws_call
    if a.action == "probe":
        from .probe import probe
        p = probe(cfg.camera.backend)
        print(_fmt(f"{'נמצאה מצלמה' if p.available else 'לא נמצאה מצלמה'}: {p.detail}",
                   f"{'Camera listed' if p.available else 'No camera'}: {p.detail}") + (f" ({p.action})" if p.action else ""))
        print(_fmt("לא נפתח חיישן ולא צולמה תמונה.", "No sensor was opened and no picture was taken."))
        return 0 if p.available else 1
    if a.action == "test-frame":
        r = _ws_call(home, cfg.link.port, {"type": "camera.test", "payload": {"mode": "frame"}}, "camera.view", timeout=20)
        if r is None:
            print(_fmt("JARVES לא רץ. הפעלה: jarvis start", "JARVES is not running. Start it with: jarvis start"),
                  file=sys.stderr)
            return 1
        t = r.get("test") or {}
        if not t.get("ok"):
            print(_fmt(f"{t.get('he', '')} {t.get('action_he', '')}", f"{t.get('en', '')} {t.get('action_en', '')}"))
            return 1
        print(_fmt(f"תמונת בדיקה התקבלה: {t['width']}x{t['height']} תוך {t['ms']} מ\"ש. נמדדה ונמחקה, לא נשמרה ולא נשלחה.",
                   f"Test picture received: {t['width']}x{t['height']} in {t['ms']} ms. Measured and dropped; not saved, not sent."))
        for h in t.get("hints", []):
            print(_fmt("רמז: " + ("חשוך מדי, הוסיפו אור" if h == "too_dark" else "אולי לא בפוקוס, נקו את העדשה"),
                       "Hint: " + ("too dark, add light" if h == "too_dark" else "maybe out of focus, clean the lens")))
        return 0
    r = _ws_call(home, cfg.link.port, {"type": "camera.status", "payload": {}}, "camera.view", timeout=5)
    if r is None:
        from .surface import offline_view

        class _Ctx:
            pass
        c = _Ctx()
        c.cfg, c.home = cfg, home
        _print_view(offline_view(c))
        return 0
    _print_view({"core_running": True, **r})
    return 0


COMMANDS = {"camera": cmd_camera}
