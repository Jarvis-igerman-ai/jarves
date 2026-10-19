"""Camera (SW-V2 WP-J): offline, no hardware, no network. Synthetic fixture frames only (never photos of people):
a "face" is a bright square; the fake detector finds it, the fake embedder turns its colour into an identity.
Covers: probe without opening, switches default OFF (sensor never opened), indicator, presence -> gaze, away mode,
"מה זה?" through the backend path with a fake Responses client (budget + no tools + image part), shutter closed,
daily photo cap, untrusted-image guard, opt-in enrolment with consent, per-person delete, Core wiring."""
import asyncio
import base64
import os
import struct
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

from jarves.budget import Budget
from jarves.camera.backends import FakeCamera, Probe, probe
from jarves.camera.detect import Box, cosine
from jarves.camera.faces import FaceStore
from jarves.camera.frames import Frame, encode_still, is_dark
from jarves.camera.presence import PresenceTracker
from jarves.camera.service import CameraService
from jarves.camera.switches import LocalSwitches, Switches
from jarves.camera.vision import Vision
from jarves.camera.wire import greeting, install_taint_guard
from jarves.config import AIConfig, CameraConfig
from jarves.memory import MemoryStore
from jarves.skills import SkillRegistry, Tool, ToolResult, Skill
from jarves.skills.camera import CameraSkill


# ------------------------------------------------------------------ synthetic fixtures
def room(face_at=None, colour=(250, 250, 250), size=(240, 320), level=90):
    """A grey room; optionally one 'face' (bright square, 1/6 of the height) centred at face_at=(cx, cy) 0..1."""
    h, w = size
    px = np.full((h, w, 3), level, np.uint8)
    px[::7, :, :] = level + 20                       # some texture so the frame is not "dark"
    if face_at is not None:
        s = h // 6
        cx, cy = int(face_at[0] * w), int(face_at[1] * h)
        px[cy - s // 2: cy + s // 2, cx - s // 2: cx + s // 2] = colour
    return px


BLACK = np.zeros((240, 320, 3), np.uint8)


class BlobDetector:
    """Finds squares whose pixels are >= 200 in every channel (the synthetic faces)."""
    name = "blob"

    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        px = frame.pixels
        mask = (px >= 200).all(axis=2)
        if not mask.any():
            return []
        h, w = mask.shape
        out = []
        cols = np.where(mask.any(axis=0))[0]
        # split into separate squares by gaps in the columns
        groups = np.split(cols, np.where(np.diff(cols) > 1)[0] + 1)
        for g in groups:
            rows = np.where(mask[:, g[0]:g[-1] + 1].any(axis=1))[0]
            out.append(Box(g[0] / w, rows[0] / h, (g[-1] - g[0] + 1) / w, (rows[-1] - rows[0] + 1) / h, 0.95))
        return out


class ColourEmbedder:
    name = "colour"

    def embed(self, frame, box):
        h, w = frame.pixels.shape[:2]
        x, y = int((box.x + box.w / 2) * w), int((box.y + box.h / 2) * h)
        c = frame.pixels[y, x].astype(np.float32) - 225.0
        return np.concatenate([c, [1.0]]).astype(np.float32)


class FakeStream:
    def __init__(self, events):
        self.events = events

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self.events:
            yield e

    async def close(self):
        pass


class FakeResponses:
    def __init__(self, text="זה ספל קפה לבן."):
        self.text, self.bodies = text, []

    async def create(self, **body):
        self.bodies.append(body)
        return FakeStream([{"type": "response.created", "response": {"id": "r1", "model": body["model"]}},
                           {"type": "response.output_text.delta", "delta": self.text},
                           {"type": "response.completed", "response": {"usage": {"input_tokens": 900,
                                                                                 "output_tokens": 20}}}])


class Sent(list):
    async def __call__(self, t, p):
        self.append((t, p))

    def of(self, t):
        return [p for k, p in self if k == t]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, *, frames=(), stills=None, on=(), detector=True, embedder=False, vision=None, cfg=None,
         available=True, activity=None):
    mem = MemoryStore(tmp_path / "memory.db")
    sw = Switches(mem, features=None)
    sw._f = None
    sw.backend = "local"
    for s in on:
        mem.set_setting(f"feature:{s}", "on")
    cam = FakeCamera(frames or [room()], stills=stills)
    sent, clock = Sent(), Clock()
    svc = CameraService(cfg or CameraConfig(away_after_s=60), tmp_path, switches=sw, memory=mem, send=sent,
                        vision=vision, detector=BlobDetector() if detector else None,
                        embedder=ColourEmbedder() if embedder else None, activity=activity,
                        probe_fn=lambda: Probe(available, "fake" if available else "none",
                                               "fake camera" if available else "no camera connected"),
                        camera_factory=lambda p: cam, clock=clock)
    svc.probe(fresh=True)
    return svc, cam, sent, clock, mem


# ------------------------------------------------------------------ probe / frames / tracker
def test_probe_never_opens_and_reports_missing_camera():
    assert not probe("none").available
    p = probe("auto", picam=lambda: None, devices=lambda: [], cv2_present=lambda: True)
    assert not p.available and p.detail == "no camera connected"
    p = probe("auto", picam=lambda: None, devices=lambda: ["USB VID_1"], cv2_present=lambda: False)
    assert not p.available and "OpenCV" in p.detail and "extra camera" in p.action
    p = probe("auto", picam=lambda: [{"Model": "imx708_wide"}], devices=lambda: [], cv2_present=lambda: False)
    assert p.available and p.backend == "picamera2" and "imx708" in p.detail
    p = probe("opencv", picam=lambda: [{"Model": "x"}], devices=lambda: ["USB VID_1"], cv2_present=lambda: True)
    assert p.available and p.backend == "opencv"


def test_frames_png_fallback_dark_detection_and_drop(tmp_path):
    f = Frame(room((0.5, 0.5)))
    assert not is_dark(f) and is_dark(Frame(BLACK))
    mime, data = encode_still(Frame(room(size=(600, 900))), max_side=300)
    if mime == "image/png":                         # no OpenCV in the test venv: dependency-free PNG
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", data[16:24])
        assert max(w, h) <= 300
        idat = data[data.index(b"IDAT") + 4: data.index(b"IEND") - 8]
        assert len(zlib.decompress(idat)) == h * (w * 3 + 1)
    else:
        assert mime == "image/jpeg" and data[:2] == b"\xff\xd8"
    f.drop()
    assert f.pixels.size == 3
    np.save(tmp_path / "frame.npy", room((0.3, 0.5)))
    cam = FakeCamera([tmp_path / "frame.npy"])
    cam.open()
    assert BlobDetector().detect(cam.read())


def test_presence_tracker_debounce_mirror_rate_limit_and_leave():
    t = PresenceTracker(mirror=True)
    face_left_of_image = [Box(0.1, 0.4, 0.2, 0.2, 0.9)]          # centre x = 0.2
    assert not t.feed(face_left_of_image, 0.0).arrived           # one detection never wakes the face
    up = t.feed(face_left_of_image, 0.1)
    assert up.arrived and up.gaze is not None and up.gaze.x > 0.5     # mirrored: image left -> face looks right
    assert t.feed([Box(0.12, 0.4, 0.2, 0.2, 0.9)], 0.15).gaze is None  # tiny move, too soon
    assert t.feed([Box(0.6, 0.4, 0.2, 0.2, 0.9)], 0.2).gaze is None    # big move but within 1/5 s
    g = t.feed([Box(0.6, 0.4, 0.2, 0.2, 0.9)], 0.35).gaze
    assert g is not None and g.x < 0 and -1 <= g.y <= 1
    assert not t.feed([], 1.0).left and t.present                  # brief miss: still present
    assert t.feed([], 2.5).left and not t.present
    assert not t.feed([Box(0.4, 0.4, 0.2, 0.2, 0.3)], 3.0).faces   # low score ignored


# ------------------------------------------------------------------ privacy defaults
async def test_everything_default_off_sensor_never_opened(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, frames=[room((0.5, 0.5))])
    for _ in range(5):
        await svc.tick()
        clock.t += 1
    assert cam.opened == 0 and not svc.indicator and sent.of("camera.indicator") == []
    assert svc.status()["enabled"] is False and svc.status()["face_recognition"] is False
    from jarves.camera.service import Refusal
    with pytest.raises(Refusal) as e:
        await svc.capture_still("test")
    assert e.value.code == "CAMERA_DISABLED" and cam.opened == 0
    assert not (tmp_path / "faces.db").exists()                     # nothing biometric is created by default
    mem.close()


async def test_no_camera_is_unavailable_not_a_crash(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, on=("camera",), available=False)
    await svc.tick()
    assert cam.opened == 0 and svc.status()["available"] is False
    assert svc.switches.active("camera") is False                  # switched on but unavailable -> not active
    assert svc.face_available()[0] is False
    mem.close()


def test_local_switches_interface_matches_features_contract(tmp_path):
    mem = MemoryStore(tmp_path / "m.db")
    sw = LocalSwitches(mem)
    sw.register("camera", "מצלמה", "Camera", False, lambda: (True, "ok"), "medium")
    assert sw.active("camera") is False
    sw.set("camera", True)
    assert sw.active("camera") is True
    sw.register("camera", "מצלמה", "Camera", False, lambda: (False, "no camera connected"), "medium")
    v = sw.view()[0]
    assert v["enabled"] and not v["available"] and v["detail"] == "no camera connected" and not sw.active("camera")

    calls = []

    class Features:                                 # WP-I's module shape: register(...) + active(id)
        def register(self, *a):
            calls.append(a)

        def active(self, id):
            raise RuntimeError("broken backend")
    s2 = Switches(mem, features=Features())
    s2.register_all({})
    assert calls == []                                             # jarves.features registers the three itself
    assert s2.active("camera") is False                            # a failing switch backend counts as OFF
    mem.close()


# ------------------------------------------------------------------ presence, gaze, indicator, away
async def test_presence_drives_gaze_and_indicator_and_frames_are_dropped(tmp_path):
    frames = [room((0.25, 0.5))] * 3 + [room()] * 12
    svc, cam, sent, clock, mem = make(tmp_path, frames=frames, on=("camera",))
    arrived = []
    svc.on_arrive = arrived.append
    for _ in range(15):
        await svc.tick()
        clock.t += 0.25
    assert cam.opened == 1 and svc.streaming and svc.indicator
    assert sent.of("camera.indicator")[0] == {"on": True, "flash": False}
    gazes = sent.of("face.set_gaze")
    assert gazes[0]["x"] > 0.3 and gazes[0]["confidence"] > 0.9        # person on image left -> look right
    assert gazes[-1] == {"x": 0.0, "y": 0.0, "confidence": 0.0}        # left -> stop tracking
    assert arrived == [None]                                           # recognition off -> unknown person
    mem.set_setting("feature:camera", "off")
    await svc.tick()
    assert not svc.streaming and not cam.is_open and sent.of("camera.indicator")[-1]["on"] is False
    mem.close()


async def test_away_mode_turns_camera_off_and_activity_brings_it_back(tmp_path):
    busy = {"v": False}
    svc, cam, sent, clock, mem = make(tmp_path, frames=[room()], on=("camera",), activity=lambda: busy["v"],
                                      cfg=CameraConfig(away_after_s=60))
    await svc.tick()
    assert svc.streaming
    clock.t += 61
    await svc.tick()
    assert svc.away and not svc.streaming and not cam.is_open and not svc.indicator
    clock.t += 1000
    await svc.tick()
    assert cam.opened == 1                                             # stays OFF while away
    busy["v"] = True                                                   # "Hey Jarvis" / a conversation
    await svc.tick()
    assert not svc.away and svc.streaming and cam.opened == 2
    # camera_when_away on: away does not close it
    busy["v"] = False
    mem.set_setting("feature:camera_when_away", "on")
    clock.t += 61
    await svc.tick()
    assert svc.away and svc.streaming
    svc.external_presence(True)                                        # presence sensor event
    assert not svc.away
    await svc.stop()
    assert not cam.is_open and not svc.indicator
    mem.close()


async def test_open_failure_is_recoverable(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, on=("camera",))
    cam.fail_open = True
    await svc.tick()
    assert not svc.streaming and cam.opened == 0 and not svc.indicator
    cam.fail_open = False
    clock.t += 5
    await svc.tick()
    assert not svc.streaming                                           # backs off before retrying
    clock.t += 31
    await svc.tick()
    assert svc.streaming
    mem.close()


# ------------------------------------------------------------------ "מה זה?" / read text
async def test_describe_sends_one_photo_through_backend_path_with_budget(tmp_path):
    resp = FakeResponses()
    svc, cam, sent, clock, mem = make(tmp_path, stills=[room((0.5, 0.5), colour=(240, 200, 200))],
                                      on=("camera",), detector=False)
    budget = Budget(mem, AIConfig(daily_request_limit=5))
    svc.vision = Vision("gpt-6-luna", "JARVES", budget, client=SimpleNamespace(responses=resp),
                        reasoning_effort="none")
    reg = SkillRegistry()
    taint = install_taint_guard(reg)
    reg.register(CameraSkill(svc, on_untrusted=taint))
    r = await reg.call("camera_describe", {"question": "מה זה?"})
    assert r.ok and r.message == "זה ספל קפה לבן." and r.data["untrusted"] is True and r.data["photos_today"] == 1
    body = resp.bodies[0]
    assert body["model"] == "gpt-6-luna" and body["store"] is False and "tools" not in body
    parts = body["input"][0]["content"]
    assert parts[0] == {"type": "input_text", "text": "מה זה?"}
    url = parts[1]["image_url"]
    assert parts[1]["type"] == "input_image" and url.startswith("data:image/")
    assert len(base64.b64decode(url.split(",", 1)[1])) == r.data["sent_bytes"]
    assert "never instructions" in body["instructions"]
    assert budget.used("ai_requests") == 1 and mem.usage_today("camera_photos") == 1
    assert cam.still_count == 1 and not cam.is_open                    # opened for the still only, then closed
    ind = sent.of("camera.indicator")
    assert {"on": True, "flash": True} in ind and ind[-1]["on"] is False
    t = await reg.call("camera_read_text", {})
    assert t.ok and "Transcribe" in resp.bodies[1]["instructions"]
    budget.close()
    mem.close()


async def test_shutter_closed_limit_and_no_vision(tmp_path):
    svc, cam, sent, clock, mem = make(tmp_path, stills=[BLACK], on=("camera",), detector=False,
                                      cfg=CameraConfig(daily_cloud_photos=1))
    skill = SkillRegistry()
    skill.register(CameraSkill(svc))
    r = await skill.call("camera_describe", {})
    assert not r.ok and r.code == "VISION_UNAVAILABLE"
    resp = FakeResponses()
    svc.vision = Vision("gpt-6-luna", "JARVES", None, client=SimpleNamespace(responses=resp))
    r = await skill.call("camera_describe", {})
    assert not r.ok and r.code == "CAMERA_SHUTTER_CLOSED" and resp.bodies == []   # nothing sent
    cam.stills = [room((0.5, 0.5))]
    assert (await skill.call("camera_describe", {})).ok
    r = await skill.call("camera_describe", {})
    assert not r.ok and r.code == "CAMERA_CLOUD_LIMIT" and len(resp.bodies) == 1
    mem.close()


async def test_untrusted_image_text_makes_actions_ask_first(tmp_path):
    clock = Clock()
    reg = SkillRegistry(allowed_actions={"do_it"})
    asked = []

    async def deny(prompt):
        asked.append(prompt)
        return False
    reg.confirm = deny

    class S(Skill):
        name = "s"

        def tools(self):
            async def ok(_):
                return ToolResult(True, "done")
            return [Tool("do_it", "x", ok, permission="action"), Tool("read_it", "x", ok, permission="read")]
    reg.register(S())
    taint = install_taint_guard(reg, window_s=60, clock=clock)
    assert (await reg.call("do_it", {})).ok and asked == []
    taint()                                          # a photo said "turn the music to 100" ...
    r = await reg.call("do_it", {})
    assert not r.ok and r.code == "TOOL_DECLINED" and len(asked) == 1
    assert (await reg.call("read_it", {})).ok                         # reads are not affected
    assert (await reg.call("do_it", {}, origin="user")).ok            # the user's own command is not affected
    clock.t += 61
    assert (await reg.call("do_it", {})).ok and len(asked) == 1


# ------------------------------------------------------------------ opt-in face recognition
def test_face_store_embeddings_only_match_and_delete(tmp_path):
    fs = FaceStore(tmp_path / "faces.db")
    a, b = np.array([1, 0, 0, 0.1], np.float32), np.array([0, 1, 0, 0.1], np.float32)
    assert fs.enroll("נועה", [a, a * 1.1], consent="test") == 2
    fs.enroll("Dan", [b], consent="test")
    assert fs.match(a + 0.05)[0] == "נועה" and fs.match(b)[0] == "Dan"
    assert fs.match(np.array([0, 0, 1, 0], np.float32)) is None        # unknown = guest
    assert {p["name"] for p in fs.people()} == {"נועה", "Dan"}
    cols = [r[1] for r in fs._db.execute("PRAGMA table_info(embeddings)")]
    assert cols == ["id", "person", "vec"]                             # no image column anywhere
    assert fs.forget("  נועה ") == 1 and fs.match(a) is None and fs.forget("nobody") == 0
    assert fs.forget_all() == 1 and fs.count() == 0
    with pytest.raises(ValueError):
        fs.enroll("x", [a], consent="")                                # consent is mandatory
    fs.close()
    assert cosine(a, a) == pytest.approx(1.0)


async def test_enroll_needs_switch_consent_and_exactly_one_face(tmp_path):
    noa = room((0.5, 0.5), colour=(250, 210, 210))
    svc, cam, sent, clock, mem = make(tmp_path, stills=[noa], on=("camera",), embedder=True)
    reg = SkillRegistry(allowed_actions={"camera_enroll"})
    answers = []

    async def confirm(prompt):
        answers.append(prompt)
        return len(answers) > 1                     # first time the person says no
    reg.confirm = confirm
    reg.register(CameraSkill(svc, reg))
    r = await reg.call("camera_enroll", {"name": "נועה"})
    assert not r.ok and r.code == "FACE_RECOGNITION_DISABLED" and answers == []   # no consent asked, no capture
    mem.set_setting("feature:face_recognition", "on")
    r = await reg.call("camera_enroll", {"name": "נועה"})
    assert not r.ok and r.code == "TOOL_DECLINED" and "במכשיר" in answers[0]
    assert not (tmp_path / "faces.db").exists() and cam.still_count == 0
    cam.stills = [room((0.3, 0.5)) | room((0.75, 0.5))]               # two people in view
    r = await reg.call("camera_enroll", {"name": "נועה"})
    assert not r.ok and r.code == "ENROLL_ONE_FACE" and svc.faces.count() == 0
    cam.stills = [noa]
    r = await reg.call("camera_enroll", {"name": "נועה"})
    assert r.ok and r.data["samples"] == 3
    w = await reg.call("camera_who", {})
    assert w.ok and w.data == {"known": True, "name": "נועה"}
    cam.stills = [room((0.5, 0.5), colour=(205, 250, 205))]
    w = await reg.call("camera_who", {})
    assert w.ok and w.data["known"] is False                           # stranger -> guest
    # deleting always works, also with the switches off; from the model it asks first
    mem.set_setting("feature:camera", "off")
    r = await reg.call("camera_forget", {"name": "נועה"})
    assert r.ok and svc.faces.count() == 0
    mem.close()


async def test_presence_recognises_enrolled_person_for_greeting(tmp_path):
    noa = room((0.5, 0.5), colour=(250, 210, 210))
    svc, cam, sent, clock, mem = make(tmp_path, frames=[noa], stills=[noa], on=("camera", "face_recognition"),
                                      embedder=True)
    await svc.enroll("נועה")
    arrived = []
    svc.on_arrive = arrived.append
    for _ in range(3):
        await svc.tick()
        clock.t += 0.3
    assert arrived == ["נועה"] and svc.current_person == "נועה"
    assert greeting("נועה", hour=20) == "ערב טוב, נועה!" and greeting(None, hour=8) == "בוקר טוב!"
    await svc.stop()
    mem.close()


# ------------------------------------------------------------------ Core wiring
async def test_core_attaches_camera_switched_off(core):
    from conftest import open_face
    assert core.camera is not None
    assert core.camera.switches.active("camera") is False
    assert core.registry.get("camera_describe") is None            # switched off: the model is not offered the tool
    assert "camera" in core.workspace.read("TOOLS.md")             # ...and TOOLS.md says it is off
    face = await open_face(core)
    await asyncio.sleep(0.2)
    r = await core.registry.call("camera_status", {})
    assert not r.ok and r.code == "FEATURE_OFF"                    # even the status tool is gated while it is off
    assert core.camera.status()["enabled"] is False and core.camera.status()["indicator"] is False
    r = await core.registry.call("camera_describe", {})
    assert not r.ok and r.code in ("CAMERA_DISABLED", "CAMERA_UNAVAILABLE", "FEATURE_OFF")
    await face.send("presence.changed", {"present": True})
    await asyncio.sleep(0.1)
    assert all(m["type"] != "camera.indicator" or m["payload"]["on"] is False for m in face.msgs)
    await face.ws.close()


# ------------------------------------------------------------------ real OpenCV (only where the camera extra is installed)
def test_bundled_yunet_runs_on_a_synthetic_frame(tmp_path):
    cv2 = pytest.importorskip("cv2")
    from jarves.camera.detect import BUNDLED, make_detector, yunet_model
    assert yunet_model(tmp_path) == BUNDLED / "face_detection_yunet_2023mar.onnx"
    det = make_detector(tmp_path)
    assert det is not None and det.name == "yunet"
    assert det.detect(Frame(room())) == []                             # an empty room has no face
    det.detect(Frame(room(size=(480, 640))))                            # input size change is handled
    mime, data = encode_still(Frame(room(size=(900, 1200))), 640)
    assert mime == "image/jpeg" and cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR).shape[1] == 640


# ------------------------------------------------------------------ MANUAL: the PC webcam (never in CI, never records)
@pytest.mark.skipif(os.environ.get("JARVES_MANUAL_CAMERA") != "1",
                    reason="manual only: set JARVES_MANUAL_CAMERA=1 to open the real webcam once")
def test_manual_real_webcam_one_frame(tmp_path):
    """Opens the PC webcam, reads ONE preview frame and one still, runs local detection, drops both. Nothing is
    saved or sent anywhere. By hand: JARVES_MANUAL_CAMERA=1 uv run pytest tests/test_camera.py -k manual -s"""
    pytest.importorskip("cv2")
    from jarves.camera.backends import make_camera
    from jarves.camera.detect import make_detector
    p = probe("opencv")
    if not p.available:
        pytest.skip(p.detail)
    cam = make_camera(p, CameraConfig())
    cam.open()
    try:
        f, s = cam.read(), cam.still()
        faces = make_detector(tmp_path).detect(f)
        print(f"webcam ok: preview {f.width}x{f.height}, still {s.width}x{s.height}, dark={is_dark(f)}, "
              f"faces={len(faces)}")
        f.drop()
        s.drop()
    finally:
        cam.close()
    assert not cam.is_open
