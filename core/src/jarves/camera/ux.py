"""Camera section of the dashboard (V4.5 workstream D, first slice): ONE honest view of the camera, a sensorless
device probe, an explicit local frame test and actionable errors in Hebrew and English.

Rules kept here (docs/CAMERA_SETUP.md): enabling the camera never opens the sensor, so the state says "available",
never "on", until a request really holds the sensor. A frame test is a local-only RAM capture: the picture is
measured (size, brightness, sharpness hint, time to first frame) and dropped, never saved, never sent to a provider,
never shown. Importing this module does not import OpenCV.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np

from .. import logs
from .frames import Frame, luminance
from .service import CameraService, Refusal

STATES = ("off", "available", "previewing", "capturing", "sending", "busy", "error")
# Codes that mean the DEVICE is the problem (state "error"); a failed AI answer leaves the camera "available".
DEVICE_CODES = {"CAMERA_UNAVAILABLE", "CAMERA_OPEN_FAILED", "CAMERA_READ_FAILED", "CAMERA_NO_LIBRARY",
                "CAMERA_SHUTTER_CLOSED", "CAMERA_CLOSED"}
DARK_HINT_MEAN = 40.0           # 0..255 luma: dim enough that answers get unreliable
SOFT_HINT_GRADIENT = 1.2        # mean absolute neighbour difference: a very flat picture may be out of focus

# code -> (what happened HE, EN, what to do HE, EN). Plain words, one next step.
ERRORS: dict[str, tuple[str, str, str, str]] = {
    "CAMERA_UNAVAILABLE": ("לא נמצאה מצלמה.", "No camera was found.",
                           "חברו מצלמה (USB או מובנית) ולחצו \"בדיקת מכשיר\".",
                           "Connect a camera (USB or built-in), then press Check device."),
    "CAMERA_OPEN_FAILED": ("המצלמה לא נפתחה. כנראה תוכנה אחרת משתמשת בה.", "The camera did not open. Another app is probably using it.",
                           "סגרו זום, Teams או דפדפן שמשתמשים במצלמה ונסו שוב.",
                           "Close Zoom, Teams or a browser tab that uses the camera and try again."),
    "CAMERA_READ_FAILED": ("המצלמה נפתחה אבל לא החזירה תמונה.", "The camera opened but returned no picture.",
                           "נתקו וחברו את המצלמה ונסו שוב.", "Unplug and replug the camera, then try again."),
    "CAMERA_NO_LIBRARY": ("חסרה ספריית המצלמה במחשב.", "The camera library is missing on this computer.",
                          "הריצו jarvis features on camera כשהליבה עצורה, או תקנו את ההתקנה.",
                          "Run jarvis features on camera while the Core is stopped, or repair the install."),
    "CAMERA_SHUTTER_CLOSED": ("התמונה שחורה לגמרי.", "The picture is completely black.",
                              "פתחו את התריס או הסירו כיסוי מהעדשה ונסו שוב.",
                              "Open the shutter or remove the lens cover, then try again."),
    "CAMERA_DISABLED": ("המצלמה כבויה.", "The camera is switched off.",
                        "הדליקו את המתג \"מצלמה\" בדף היכולות.", "Turn the Camera switch on in Features."),
    "PREVIEW_DISABLED": ("תצוגת המצלמה כבויה.", "The camera preview is switched off.",
                         "הדליקו את המתג \"מה הוא רואה?\" בדף היכולות.", "Turn on the \"What does he see?\" switch in Features."),
    "CAMERA_BUSY": ("המצלמה עסוקה עכשיו בבקשה אחרת.", "The camera is busy with another request.",
                    "חכו כמה שניות ונסו שוב.", "Wait a few seconds and try again."),
    "VISION_UNAVAILABLE": ("תיאור תמונה לא זמין כרגע.", "Describing pictures is not available right now.",
                           "חברו מפתח OpenAI בדף החיבורים.", "Connect an OpenAI key on the Connections page."),
    "VISION_FAILED": ("לא הצלחתי לבדוק את התמונה.", "I could not check the picture.",
                      "נסו שוב בעוד רגע.", "Try again in a moment."),
    "AI_BUDGET": ("הגעתי למגבלת ההוצאה על בינה מלאכותית.", "The AI spending limit was reached.",
                  "בדקו את התקציב בדף השימוש.", "Check the budget on the Usage page."),
    "NO_FACE": ("לא רואה פנים במצלמה.", "No face is visible.", "עמדו מול המצלמה עם אור על הפנים ונסו שוב.",
                "Stand in front of the camera with light on your face and try again."),
    "ENROLL_ONE_FACE": ("צריך אדם אחד בלבד בתמונה.", "Exactly one person must be in view.",
                        "ודאו שרק אתם מול המצלמה ונסו שוב.", "Make sure only you are in front of the camera and try again."),
    "FACE_MODELS_MISSING": ("חסר מודל זיהוי פנים.", "The face recognition model is missing.",
                            "הריצו jarvis features on face_recognition.", "Run jarvis features on face_recognition."),
    "FACE_RECOGNITION_DISABLED": ("זיהוי פנים כבוי.", "Face recognition is switched off.",
                                  "הדליקו אותו בדף היכולות.", "Turn it on in Features."),
    "CAMERA_CLOUD_LIMIT": ("הגעתי למכסת התמונות היומית.", "The daily picture limit was reached.",
                           "נסו מחר, או הגדילו את camera.daily_cloud_photos.", "Try tomorrow, or raise camera.daily_cloud_photos."),
}
UNKNOWN_ERROR = ("משהו השתבש עם המצלמה.", "Something went wrong with the camera.",
                 "הריצו jarvis camera status ובדקו את היומן.", "Run jarvis camera status and check the log.")


def explain(code: str) -> dict:
    he, en, act_he, act_en = ERRORS.get(code, UNKNOWN_ERROR)
    return {"code": code, "he": he, "en": en, "action_he": act_he, "action_en": act_en}


def frame_quality(frame: Frame) -> dict:
    """Cheap hints from one frame (no picture leaves this function): brightness and a flatness/blur hint."""
    mean, _ = luminance(frame)
    p = frame.pixels[::2, ::2].astype(np.float32)
    y = 0.299 * p[:, :, 0] + 0.587 * p[:, :, 1] + 0.114 * p[:, :, 2]
    grad = float(np.abs(np.diff(y, axis=1)).mean()) if y.shape[1] > 1 else 0.0
    return {"brightness": round(mean, 1), "too_dark": mean < DARK_HINT_MEAN,
            "maybe_blurred": grad < SOFT_HINT_GRADIENT, "sharpness": round(grad, 2)}


def state_of(svc: CameraService) -> str:
    """Off / Available / Previewing / Capturing / Sending to AI / Busy / Error, from the real sensor state."""
    if svc.sending:
        return "sending"
    if svc.capturing:
        return "capturing"
    if svc.preview_active():
        return "previewing"
    if not svc.switches.enabled("camera"):
        return "off"
    ok, _ = svc.available()
    last = svc.last_result
    if not ok or (svc.last_error and svc.last_error.get("code") in DEVICE_CODES) or (
            last and not last["ok"] and last["code"] in DEVICE_CODES):
        return "error"
    if svc._lock.locked():
        return "busy"
    return "available"


def view(svc: CameraService) -> dict:
    """Everything the Camera section shows. No pixels, no device serial numbers."""
    state = state_of(svc)
    ok, detail = svc.available()
    probe = svc.probe()
    last = dict(svc.last_result) if svc.last_result else None
    error = None
    if state == "error":
        if not ok:
            error = explain("CAMERA_UNAVAILABLE") | {"detail": detail}
        elif last and not last["ok"] and last["code"] in DEVICE_CODES:
            error = explain(last["code"])
        elif svc.last_error:
            error = explain(svc.last_error["code"]) | {"detail": svc.last_error.get("detail", "")}
    if last and not last["ok"]:
        last["error"] = explain(last["code"])
    refusal = svc.preview_refusal()
    left = round(max(0.0, svc.preview_until - svc._clock()), 1) if svc.preview_active() else 0.0
    used = svc.memory.usage_today("camera_photos") if svc.memory is not None else 0
    return {
        "state": state, "enabled": svc.switches.enabled("camera"), "available": ok,
        "device": {"detail": detail, "backend": probe.backend, "name": getattr(svc.cam, "name", None) or probe.backend},
        "sensor_open": svc.cam is not None, "indicator": svc.indicator,
        "preview": {"active": svc.preview_active(), "seconds_left": left, "can_start": not refusal, "refusal": refusal,
                    "max_seconds": 20},
        "data_flow": "sending_to_provider" if svc.sending else "local_only",
        "provider": {"vision_ready": svc.vision is not None, "model": getattr(svc.vision, "model", "") or "",
                     "photos_today": used, "daily_limit": svc.cfg.daily_cloud_photos},
        "last_result": last, "error": error, "ts": round(time.time(), 1),
    }


async def probe_test(svc: CameraService) -> dict:
    """Device probe: is a camera listed? Opens NOTHING, takes no picture."""
    p = await asyncio.to_thread(svc.probe, True)
    svc.record_result("probe", p.available, "" if p.available else "CAMERA_UNAVAILABLE", sent_to_provider=False,
                      local_only=True, detail=p.detail)
    logs.info("camera", "CAMERA_TEST_PROBE", "device probe from the dashboard (sensor not opened)", available=p.available)
    return view(svc) | {"test": {"mode": "probe", "ok": p.available, "opened_sensor": False, "detail": p.detail}}


async def frame_test(svc: CameraService) -> dict:
    """Frame test: open the sensor ONCE, read ONE still into RAM, measure it, drop it, close the sensor.
    Local only: the picture is never saved, shown or sent anywhere."""
    if svc.preview_active() or svc.capturing or svc.sending or svc._lock.locked():
        svc.record_result("frame", False, "CAMERA_BUSY", sent_to_provider=False, local_only=True)
        return view(svc) | {"test": {"mode": "frame", "ok": False, "code": "CAMERA_BUSY", **explain("CAMERA_BUSY")}}
    t0 = time.monotonic()
    try:
        frame = await svc.capture_still("dashboard frame test")
    except Refusal as e:
        svc.record_result("frame", False, e.code, sent_to_provider=False, local_only=True)
        return view(svc) | {"test": {"mode": "frame", "ok": False, "code": e.code, **explain(e.code)}}
    ms = round((time.monotonic() - t0) * 1000)
    try:
        q = frame_quality(frame)
        w, h = frame.width, frame.height
    finally:
        frame.drop()
    hints = [k for k, bad in (("too_dark", q["too_dark"]), ("maybe_blurred", q["maybe_blurred"])) if bad]
    svc.record_result("frame", True, sent_to_provider=False, local_only=True, width=w, height=h, ms=ms, hints=hints)
    logs.info("camera", "CAMERA_TEST_FRAME", "dashboard frame test (RAM only, dropped, not sent)", width=w, height=h,
              ms=ms, hints=",".join(hints))
    return view(svc) | {"test": {"mode": "frame", "ok": True, "width": w, "height": h, "ms": ms, "hints": hints,
                                 "quality": q, "opened_sensor": True, "sensor_closed": svc.cam is None}}


def result_line(r: dict[str, Any] | None) -> tuple[str, str]:
    """(he, en) one-line summary of a last_result for the page and the CLI."""
    if not r:
        return "עוד לא הייתה בקשה.", "No request yet."
    kind = {"probe": ("בדיקת מכשיר", "Device check"), "frame": ("בדיקת תמונה", "Frame test"),
            "describe": ("תיאור תמונה", "Describe"), "read_text": ("קריאת טקסט", "Read text")}.get(r["kind"], (r["kind"],) * 2)
    if r["ok"]:
        return f"{kind[0]}: הצליח", f"{kind[1]}: OK"
    e = explain(r["code"])
    return f"{kind[0]}: {e['he']}", f"{kind[1]}: {e['en']}"
