"""Privacy strip (owner report 19 Oct: "I can't see if the mic or camera is on"; "show what he sees"). Offline, fake
camera only, no hardware, no network. Covers: the consolidated privacy.state is derived from the real state (mic /
camera / speaker / cloud audio, conservative when unreadable), the "What does he see?" preview (gated by the switches,
low fps, small JPEG, RAM + local link only, auto-close, lease for the dashboard), "taking a photo" and the last
description, and the Core wiring over a real WebSocket (privacy.state on connect and on mute, camera on needs the
on-screen confirmation, role allow-lists)."""
import asyncio
import base64
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from jarves.camera.service import PHOTO_SHOW_S, PREVIEW_MAX_S, PREVIEW_SIDE
from jarves.camera.vision import Vision
from jarves.health import Health
from jarves.runtime.privacy_state import CAMERA, CLOUD, MIC, SPEAKER, derive

from test_camera import FakeResponses, make, room


# ------------------------------------------------------------------ derive(): truthful, never optimistic
class FakeLive:
    def __init__(self, gated=False, presented=True):
        self.gated, self._presented = gated, asyncio.Event()
        if presented:
            self._presented.set()
        self._speaking_until = 0.0

    def _gated(self):
        return self.gated


def fake_core(**kw):
    h = Health()
    h.set("audio", kw.pop("audio", "ok"), "x")
    dev = SimpleNamespace(mic_muted=kw.pop("muted", False), face_state=kw.pop("face_state", "idle"), audio=object())
    return SimpleNamespace(health=h, dev=dev, live=kw.pop("live", None), listening=kw.pop("listening", False),
                           _wake_mic_active=kw.pop("wake", False), _voice_connecting=kw.pop("connecting", False),
                           camera=kw.pop("camera", None), cfg=SimpleNamespace(voice=SimpleNamespace(language="he")))


def test_mic_cloud_speaker_states_follow_the_real_state():
    cases = [
        (dict(), "idle", "off"),
        (dict(wake=True), "wake_only", "off"),                         # local wake word only: nothing leaves
        (dict(listening=True), "listening", "off"),                    # push-to-talk: local STT, no cloud audio
        (dict(connecting=True, wake=True), "wake_only", "connecting"),
        (dict(live=FakeLive()), "listening", "sending"),
        (dict(live=FakeLive(gated=True)), "gated", "connected"),       # silence replaces the mic while he speaks
        (dict(live=FakeLive(presented=False)), "idle", "connected"),   # greeting held: physical mic not opened yet
        (dict(live=FakeLive(), muted=True), "muted", "connected"),
        (dict(audio="unavailable", wake=True), "unavailable", "off"),
    ]
    for kw, mic, cloud in cases:
        st = derive(fake_core(**kw))
        assert (st["mic"]["state"], st["cloud_audio"]["state"]) == (mic, cloud), kw
        assert st["cloud_audio"]["on"] is (cloud == "sending")
        assert st["mic"]["state"] in MIC and st["cloud_audio"]["state"] in CLOUD
    assert derive(fake_core(face_state="speaking"))["speaker"]["state"] == "speaking"
    assert derive(fake_core(audio="disabled"))["speaker"]["state"] == "unavailable"
    st = derive(fake_core())
    assert st["speaker"]["state"] in SPEAKER and st["camera"]["state"] == "unavailable" and st["locale"] == "he"
    json.dumps(st)                                                       # the face gets plain JSON


def test_unreadable_gate_counts_as_sending():
    class Broken:
        def _gated(self):
            raise RuntimeError("boom")
    st = derive(fake_core(live=Broken()))
    assert st["mic"]["state"] == "listening" and st["cloud_audio"]["on"] is True


# ------------------------------------------------------------------ camera states + preview (fake camera)
async def test_camera_states_off_disabled_presence_unavailable(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, on=())
    assert svc.privacy_state() == "disabled"
    mem.set_setting("feature:camera", "on")
    svc.away = True                                                     # switch on, nothing needs the sensor
    await svc.tick()
    assert svc.privacy_state() == "off" and cam.opened == 0
    svc.user_activity()
    await svc.tick()
    assert svc.privacy_state() == "presence" and cam.is_open
    assert derive(fake_core(camera=svc))["camera"]["state"] == "presence"
    (tmp_path / "b").mkdir()
    svc2, *_, mem2 = make(tmp_path / "b", available=False, on=("camera",))
    assert svc2.privacy_state() == "unavailable" and svc2.preview_open(10)["reason"] == "CAMERA_UNAVAILABLE"
    assert all(s in CAMERA for s in ("presence", "off", "disabled", "unavailable"))
    await svc.stop()
    mem.close()
    mem2.close()


async def test_preview_refused_while_switches_off_sensor_never_opened(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, on=())
    assert svc.preview_open(20) == {"open": False, "reason": "CAMERA_DISABLED"}
    await svc.tick()
    mem.set_setting("feature:camera", "on")
    mem.set_setting("feature:camera_preview", "off")
    assert svc.preview_open(20)["reason"] == "PREVIEW_DISABLED"
    svc.away = True
    await svc.tick()
    assert cam.opened == 0 and not sent.of("camera.preview_frame") and not svc.indicator
    mem.close()


async def test_preview_streams_small_frames_rate_limited_ram_only_and_closes(tmp_path):
    before = {p.name for p in tmp_path.rglob("*")}
    svc, cam, sent, clock, mem = make(tmp_path, frames=[room(size=(480, 640))], on=("camera",), detector=False)
    assert svc.privacy_state() == "off"                                 # no detector: nothing opens by itself
    st = svc.preview_open(20)
    assert st["open"] and st["seconds_left"] == 20.0
    await svc.tick()
    assert cam.is_open and svc.indicator and svc.privacy_state() == "preview"
    await svc.tick()                                                    # same instant: rate limit holds
    frames = sent.of("camera.preview_frame")
    assert len(frames) == 1
    f = frames[0]
    data = base64.b64decode(f["data"])
    assert f["mime"] in ("image/jpeg", "image/png") and max(f["width"], f["height"]) <= PREVIEW_SIDE
    assert len(data) < 60_000 and f["seq"] == 1
    try:
        import cv2
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert img.shape[1] == f["width"] == 320 and img.shape[0] == f["height"] == 240
    except ImportError:
        assert data[:4] == b"\x89PNG"
    for _ in range(9):                                                  # 3 s at the supervisor's pace
        clock.t += 1 / 3
        await svc.tick()
    assert 9 <= len(sent.of("camera.preview_frame")) <= 10
    clock.t += 20                                                       # auto-close
    await svc.tick()
    assert sent.of("camera.preview_state")[-1] == {"open": False, "reason": "timeout"}
    await svc.tick()
    assert not cam.is_open and not svc.indicator and svc.preview_frame is None and svc.privacy_state() == "off"
    n = len(sent.of("camera.preview_frame"))
    clock.t += 1
    await svc.tick()
    assert len(sent.of("camera.preview_frame")) == n                    # nothing after the close
    after = {p.name for p in tmp_path.rglob("*")}
    assert not [x for x in after - before if x.lower().endswith((".jpg", ".jpeg", ".png", ".npy", ".bmp"))]
    mem.close()


async def test_preview_hard_cap_lease_and_switch_off_mid_preview(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, on=("camera",), detector=False)
    t0 = clock.t
    svc.preview_open(5, by="cli")                                       # dashboard lease
    for _ in range(int(PREVIEW_MAX_S) + 10):                            # keeps polling: still capped
        svc.preview_open(5, by="cli")
        clock.t += 1
        await svc.tick()
        if not svc.preview_active():
            break
    assert clock.t - t0 <= PREVIEW_MAX_S + 1 and sent.of("camera.preview_state")[-1]["reason"] == "timeout"
    clock.t += 1
    await svc.tick()
    assert not cam.is_open
    # without a poll the lease ends after a few seconds
    svc.preview_open(5, by="cli")
    for _ in range(6):
        clock.t += 1
        await svc.tick()
    assert not svc.preview_active()
    svc.preview_open(20)
    await svc.tick()
    assert cam.is_open
    mem.set_setting("feature:camera", "off")                            # the switch wins at once
    await svc.tick()
    assert sent.of("camera.preview_state")[-1] == {"open": False, "reason": "CAMERA_DISABLED"}
    await svc.tick()
    assert not cam.is_open and svc.privacy_state() == "disabled"
    mem.close()


async def test_photo_state_and_last_description(tmp_path):
    resp = FakeResponses("זה ספל קפה לבן.")
    svc, cam, sent, clock, mem = make(tmp_path, stills=[room((0.5, 0.5), colour=(240, 200, 200))],
                                      on=("camera",), detector=False)
    from jarves.budget import Budget
    from jarves.config import AIConfig
    budget = Budget(mem, AIConfig(daily_request_limit=5))
    svc.vision = Vision("gpt-6-luna", "JARVES", budget, client=SimpleNamespace(responses=resp), reasoning_effort="none")
    seen = []
    orig = cam.still

    def still():
        seen.append(svc.privacy_state())                                # while the sensor takes it
        return orig()
    cam.still = still
    assert svc.last_seen is None
    text, _ = await svc.look("describe", "מה זה?")
    assert seen == ["photo"] and svc.privacy_state() == "photo"         # still shown for a moment afterwards
    clock.t += PHOTO_SHOW_S + 0.1
    assert svc.privacy_state() == "off"
    assert svc.last_seen["text"] == text == "זה ספל קפה לבן." and svc.last_seen["mode"] == "describe"
    assert derive(fake_core(camera=svc))["camera"]["last_seen"]["text"] == text
    budget.close()
    mem.close()


# ------------------------------------------------------------------ Core over a real WebSocket
async def test_core_sends_privacy_state_and_camera_controls(core):
    from conftest import open_face
    face = await open_face(core)
    st = await face.wait("privacy.state")                               # at once to a newly connected face
    assert st["v"] == 1 and st["mic"]["state"] in MIC and st["camera"]["state"] in ("unavailable", "disabled")
    assert st["cloud_audio"] == {"on": False, "state": "off"}
    assert core.snapshot()["privacy"]["mic"]["state"] == st["mic"]["state"]
    await face.send("mic.set_mute", {"muted": True})
    st = await face.wait("privacy.state", lambda p: p["mic"]["state"] == "muted")
    assert st["cloud_audio"]["on"] is False
    await face.send("mic.set_mute", {"muted": False})
    await face.wait("privacy.state", lambda p: p["mic"]["state"] != "muted")
    # camera on from the face: asks on the screen first; declined -> stays off and is listed for approval
    await face.send("camera.set_enabled", {"on": True})
    req = await face.wait("confirm.request")
    assert "מצלמה" in req["prompt"]
    await face.send("confirm.reply", {"id": req["id"], "approved": False})
    r = await face.wait("camera.enabled")
    assert r["on"] is False and core.features.enabled("camera") is False
    assert core.features.status("camera")["requested"]
    await face.send("camera.set_enabled", {"on": True})
    req = await face.wait("confirm.request")
    await face.send("confirm.reply", {"id": req["id"], "approved": True})
    r = await face.wait("camera.enabled")
    assert r["on"] is True and core.features.enabled("camera") is True
    await face.send("camera.set_enabled", {"on": False})                # off: at once, no question
    r = await face.wait("camera.enabled")
    assert r["on"] is False and core.features.enabled("camera") is False
    await face.send("camera.set_enabled", {"on": "yes"})
    assert (await face.wait("error"))["code"] == "LINK_BAD_MESSAGE"
    # preview while the camera is off: refused with the reason, no frame ever
    await face.send("camera.preview", {"open": True, "seconds": "lots"})
    ps = await face.wait("camera.preview_state")
    assert ps["open"] is False and ps["reason"] in ("CAMERA_DISABLED", "CAMERA_UNAVAILABLE") and "last_seen" in ps
    await face.send("camera.preview", {"open": False})
    await face.wait("camera.preview_state", lambda p: p["reason"] == "closed")
    assert not [m for m in face.msgs if m["type"] == "camera.preview_frame"]
    await face.ws.close()


async def test_cli_may_poll_the_preview_but_not_switch_the_camera(core):
    from websockets.asyncio.client import connect
    ws = await connect(f"ws://127.0.0.1:{core.cfg.link.port}")
    await ws.send(json.dumps({"type": "hello", "payload": {"token": core.link.token, "client": "cli"}}))
    assert json.loads(await ws.recv())["type"] == "hello.ok"
    await ws.send(json.dumps({"type": "camera.preview", "payload": {"open": True}}))
    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert m["type"] == "camera.preview_state" and m["payload"] == {"open": False, "reason": "timeout",
                                                                     "last_seen": None}   # a poll never opens
    await ws.send(json.dumps({"type": "camera.preview", "payload": {"open": True, "start": True}}))
    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert m["payload"]["open"] is False and m["payload"]["reason"] in ("CAMERA_DISABLED", "CAMERA_UNAVAILABLE")
    await ws.send(json.dumps({"type": "camera.set_enabled", "payload": {"on": True}}))
    m = json.loads(await asyncio.wait_for(ws.recv(), 5))
    assert m["type"] == "error" and m["payload"]["code"] == "LINK_FORBIDDEN"
    await ws.close()


async def test_dashboard_home_strip_and_preview_api(core):
    """Home page: /api/runtime carries privacy; the preview opens only with start, polls return frames, closes;
    a paired phone (not this computer) never gets camera images; Core down -> 409 and privacy None (= unknown)."""
    from jarves import runtime as rt_mod
    from jarves.camera.backends import FakeCamera, Probe
    from jarves.dashboard import Ctx
    svc = core.camera
    cam = FakeCamera([room()])
    svc._probe_fn = lambda: Probe(True, "fake", "fake camera")
    svc._factory = lambda p: cam
    svc.probe(fresh=True)
    svc.switches.active = lambda fid: True
    svc.detector, svc._models = None, None
    ctx = Ctx(core.home, core.cfg)
    call = lambda fn, body=None: asyncio.to_thread(fn, ctx, None, body)    # noqa: E731
    st, body = await call(rt_mod.api_runtime)
    assert st == 200 and body["privacy"]["mic"]["state"] in MIC and body["privacy"]["camera"]["state"] in ("off", "unavailable")
    st, r = await call(rt_mod.api_camera_preview_poll)
    assert st == 200 and r["open"] is False                              # a poll never opens it
    st, r = await call(rt_mod.api_camera_preview_set, {"start": True})
    assert st == 200 and r["open"] is True
    frame = None
    for _ in range(40):
        st, r = await call(rt_mod.api_camera_preview_poll)
        if r.get("frame"):
            frame = r["frame"]
            break
        await asyncio.sleep(0.1)
    assert frame and base64.b64decode(frame["data"]) and r["open"] is True
    st, body = await call(rt_mod.api_runtime)
    assert body["privacy"]["camera"]["state"] == "preview"
    st, r = await call(rt_mod.api_camera_preview_set, {"open": False})
    assert st == 200 and r["open"] is False and svc.preview_frame is None
    assert (await call(rt_mod.api_camera_preview_set, {"nope": 1}))[0] == 400
    ctx._tl.who = {"kind": "device", "id": "phone"}                     # a paired phone on the home Wi-Fi
    st, r = rt_mod.api_camera_preview_set(ctx, None, {"start": True})   # refused before the Core is asked
    assert st == 403 and not svc.preview_active()
    ctx._tl.who = None
    ctx.close()


async def test_core_preview_with_fake_camera_reaches_the_face(core):
    """Wiring end to end: the face asks, frames arrive over the link, closing stops them (fake camera, no sensor)."""
    from conftest import open_face
    from jarves.camera.backends import FakeCamera, Probe
    svc = core.camera
    cam = FakeCamera([room()])
    svc._probe_fn = lambda: Probe(True, "fake", "fake camera")
    svc._factory = lambda p: cam
    svc.probe(fresh=True)
    svc.switches.active = lambda fid: True                              # both switches on (unit: gates tested above)
    svc.detector, svc._models = None, None                              # no presence detection: preview only
    face = await open_face(core)
    await face.send("camera.preview", {"open": True, "seconds": 5})
    ps = await face.wait("camera.preview_state")
    assert ps["open"] is True
    fr = await face.wait("camera.preview_frame", timeout=20)
    assert fr["seq"] >= 1 and base64.b64decode(fr["data"])
    await face.wait("privacy.state", lambda p: p["camera"]["state"] == "preview", timeout=20)
    await face.send("camera.preview", {"open": False})
    await face.wait("camera.preview_state", lambda p: p["open"] is False)
    t0 = time.monotonic()
    while cam.is_open and time.monotonic() - t0 < 3:
        await asyncio.sleep(0.05)
    assert not cam.is_open
    await face.ws.close()
