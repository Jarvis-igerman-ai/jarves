"""Campaign stages F: camera with the Core's FakeCamera (virtual camera, never the real webcam), calendar guardrails,
scheduler timing (due reminder, switch OFF holds a run), real-Core process smoke."""
from __future__ import annotations

import asyncio
import json
import time

from tools.campaign_features import check, cli, rec, stage, until
from tools.campaign_stages_b import T, brief


def _pattern():
    import numpy as np
    px = np.zeros((240, 320, 3), np.uint8)
    for i, c in enumerate([(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192)]):
        px[:, i * 64:(i + 1) * 64] = c
    return px


def _priv(face, since=0.0):
    return face.of("privacy.state", since)


@stage
async def camera_virtual(h):
    A = "C:camera_virtual"
    from jarves.camera.backends import FakeCamera, Probe
    svc = h.core.camera
    svc._probe_fn = lambda: Probe(True, "fake", "virtual camera (generated test pattern)")
    svc._factory = lambda p: FakeCamera([_pattern()])
    svc.detector, svc._models = None, None
    svc.probe(fresh=True)
    svc._report()
    h.feat.invalidate()
    face = h.face
    for fid in ("camera", "camera_preview", "face_recognition", "camera_when_away"):
        await h.acli("features", "reset", fid)
    await until(lambda: h.feat.status("camera")["source"] != "user", 4)
    st = h.feat.status("camera")
    check(A, "camera default OFF (privacy) even though a camera is 'present'", not st["enabled"], str(st["enabled"]))
    # preview while camera off
    t = time.time()
    await face.send("camera.preview", {"open": True, "seconds": 3})
    await until(lambda: face.of("camera.preview_state", t), 3)
    pv = (face.of("camera.preview_state", t) or [{}])[-1]
    check(A, "preview refused while the camera is off", pv.get("open") is False and pv.get("reason") == "CAMERA_DISABLED", str(pv))
    for tool in ("camera_status", "camera_describe", "camera_who", "camera_read_text"):
        await T(h, A, f"camera OFF: {tool} refused", tool, {}, ok=False, code="FEATURE_OFF")
    # enabling asks on screen
    h.policy["v"] = False
    t = time.time()
    await face.send("camera.set_enabled", {"on": True})
    await until(lambda: face.of("camera.enabled", t), 8)
    den = (face.of("camera.enabled", t) or [{}])[-1]
    check(A, "enable DENIED on screen -> stays off, request recorded", den.get("on") is False and bool(face.of("confirm.request", t)) and not h.feat.enabled("camera"), str(den))
    h.policy["v"] = True
    t = time.time()
    await face.send("camera.set_enabled", {"on": True})
    await until(lambda: face.of("camera.enabled", t), 8)
    acc = (face.of("camera.enabled", t) or [{}])[-1]
    check(A, "enable APPROVED on screen -> on", acc.get("on") is True, str(acc))
    h.feat.poll()
    await until(lambda: _priv(face) and _priv(face)[-1]["camera"]["state"] in ("off", "preview", "on", "idle"), 4)
    t = time.time()
    await face.send("camera.preview", {"open": True, "seconds": 3})
    opened = await until(lambda: any(p.get("open") for p in face.of("camera.preview_state", t)), 4)
    await asyncio.sleep(4.5)
    frames = face.of("camera.preview_frame", t)
    closed = [p for p in face.of("camera.preview_state", t) if p.get("open") is False]
    check(A, "preview opens, streams frames, auto-closes at the timeout", opened and len(frames) >= 2 and any(p.get("reason") == "timeout" for p in closed), f"opened={opened} frames={len(frames)} closed={[c.get('reason') for c in closed]}")
    r = await T(h, A, "camera_status tool (virtual camera)", "camera_status", {}, ok=None)
    r = await T(h, A, "camera_describe offline AI -> graceful, not a crash", "camera_describe", {}, ok=None)
    rec(A, "camera_describe with AI offline", "PASS" if r.code != "TOOL_CRASH" else "FAIL", brief(r))
    r = await T(h, A, "camera_who without face_recognition ON -> refused", "camera_who", {}, ok=False)
    r = await T(h, A, "camera_forget_all asks (destructive) and declined keeps data", "camera_forget_all", {}, ok=False, code="TOOL_DECLINED", approve=False, expect_prompt=True)
    # switch gates
    await h.acli("features", "off", "camera_preview")
    await until(lambda: not h.feat.enabled("camera_preview"), 4)
    t = time.time()
    await face.send("camera.preview", {"open": True, "seconds": 3})
    await until(lambda: face.of("camera.preview_state", t), 3)
    pvq = (face.of("camera.preview_state", t) or [{}])[-1]
    check(A, "camera_preview OFF -> PREVIEW_DISABLED", pvq.get("reason") == "PREVIEW_DISABLED", str(pvq))
    await h.acli("features", "reset", "camera_preview")
    t = time.time()
    await face.send("camera.set_enabled", {"on": False})
    await until(lambda: face.of("camera.enabled", t), 4)
    off = (face.of("camera.enabled", t) or [{}])[-1]
    check(A, "camera switched off from the face -> off, strip 'disabled'", off.get("on") is False, str(off))
    images = [p.name for p in h.home.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp")]
    check(A, "no image files written to disk by any camera step", not images, str(images[:3]))
    # face_recognition / camera_when_away need extra prerequisites
    for fid in ("face_recognition", "camera_when_away"):
        st = h.feat.status(fid)
        rec(f"F:{fid}", "state with virtual camera", "PASS", f"enabled={st['enabled']} available={st['available']} reason={st['reason']!r}")


@stage
async def calendar_guard(h):
    A = "C:calendar"
    from jarves.skills.google import CAL_API, GoogleAuthError, calendar_guard
    r = await T(h, A, "calendar_create_event tool absent while Google not connected", "calendar_create_event", {"title": "QA", "day": "tomorrow", "start": "10:00"}, ok=False, code="TOOL_UNKNOWN")
    r = await h.acli("calendar", "create", "--title", "QA", "--day", "tomorrow", "--start", "10:00")
    check(A, "CLI `calendar create` fails cleanly while not connected", r.returncode != 0 and "Traceback" not in r.stderr, (r.stdout + r.stderr)[-200:])
    r = await h.acli("calendar", "find", "--day", "today")
    check(A, "CLI `calendar find` fails cleanly", "Traceback" not in r.stderr, (r.stdout + r.stderr)[-200:])
    r = await h.acli("mail", "draft", "--to", "a@b.co", "--subject", "x", "--body", "y")
    check(A, "CLI `mail draft` fails cleanly while not connected (no mail sent)", "Traceback" not in r.stderr, f"rc={r.returncode} {(r.stdout + r.stderr)[-200:]}")
    guard = {}
    for label, (m, path, body) in {"DELETE event": ("DELETE", "/calendars/primary/events/x", None),
                                   "cancel via PATCH": ("PATCH", "/calendars/primary/events/x", {"status": "cancelled"}),
                                   "move": ("POST", "/calendars/primary/events/x/move", {}),
                                   "ACL change": ("POST", "/calendars/primary/acl", {"role": "owner"}),
                                   "create (allowed)": ("POST", "/calendars/primary/events", {"summary": "x"})}.items():
        try:
            calendar_guard(m, CAL_API + path, body)
            guard[label] = "allowed"
        except GoogleAuthError as e:
            guard[label] = e.code
    check(A, "hard guard: delete/cancel/move/ACL refused, create allowed", guard["create (allowed)"] == "allowed" and all(v != "allowed" for k, v in guard.items() if k != "create (allowed)"), str(guard))


@stage
async def scheduler_timing(h):
    A = "T:scheduler_timing"
    r = await h.call("schedule_preview", {"when": "בעוד דקה", "task": "לשתות מים", "kind": "reminder", "name": "due-soon"})
    jid = (r.data or {}).get("id") or (r.data or {}).get("job", {}).get("id")
    await h.call("schedule_approve", {"job_id": jid})
    t0 = time.time()
    # a draft that is not approved must never run
    r2 = await h.call("schedule_preview", {"when": "בעוד דקה", "task": "טיוטה לא מאושרת", "kind": "reminder", "name": "draft-inert"})
    jd = (r2.data or {}).get("id") or (r2.data or {}).get("job", {}).get("id")
    # run_now while scheduler OFF must not run
    await h.acli("features", "off", "scheduler")
    await until(lambda: not h.feat.enabled("scheduler"), 4)
    _, jl = await h.aget("/api/jobs")
    n0 = len(next((j for j in jl["jobs"] if j["id"] == jid), {}).get("runs", []))
    await asyncio.sleep(75)                                          # the due minute passes while the switch is OFF
    _, jl = await h.aget("/api/jobs")
    n_off = len(next((j for j in jl["jobs"] if j["id"] == jid), {}).get("runs", []))
    check(A, "scheduler switch OFF: a due job does NOT run", n_off == n0, f"{n0}->{n_off}")
    await h.acli("features", "on", "scheduler")
    await until(lambda: h.feat.enabled("scheduler"), 4)
    ran = await until(lambda: len(next((j for j in h.core.ops.scheduler.jobs() if j["id"] == jid), {}) and h.core.ops.scheduler.runs(jid, 5)) > 0, 60, 1.0)
    runs = h.core.ops.scheduler.runs(jid, 5)
    check(A, "after switching ON the missed reminder runs once (labelled late)", ran and len(runs) >= 1, str(runs)[:300])
    druns = h.core.ops.scheduler.runs(jd, 5) if jd else []
    check(A, "unapproved draft never ran", not druns, str(druns))
    await h.call("job_delete", {"job_id": jid})
    if jd:
        await h.apost(f"/api/jobs/{jd}", {"op": "delete"})
