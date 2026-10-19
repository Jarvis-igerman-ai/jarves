"""CameraService: the one owner of the sensor. Privacy rules live HERE, in code (not in prompts):

1. Everything is OFF by default: nothing opens the sensor unless the `camera` switch is on AND a camera exists.
2. The sensor streams only for local presence detection (low resolution, a few fps) and only when a local
   detector exists; otherwise it opens just for a requested still and closes again.
3. Frames stay in RAM; detection keeps a box, never an image. Nothing is recorded or saved; a still is dropped
   right after use (also after a cloud request). There is no snapshot-to-disk path at all.
4. Indicator: whenever the sensor is open the face gets `camera.indicator {on: true}` (Camera Module 3 has no LED);
   a still sent to the cloud also flashes it (`flash: true`) and the Skill tells the user what was sent.
5. Away: nobody seen and no conversation for camera.away_after_s -> camera OFF, unless `camera_when_away` is on.
   It comes back on with the next sign of the user (wake word, conversation, a presence sensor event).
6. Face recognition only with the `face_recognition` switch AND per-person enrolment; deletion always works.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import logs
from .backends import Camera, CameraUnavailable, Probe, make_camera, probe as default_probe
from .detect import Box, Detector, Embedder
from .faces import FaceStore
from .frames import Frame, data_url, encode_still, is_dark
from .presence import PresenceTracker
from .switches import Switches

SendFn = Callable[[str, dict], Awaitable[None] | None]
PROBE_TTL_S = 600.0         # a camera plugged in later is found within 10 minutes (or at the next failure)
RETRY_OPEN_S = 30.0
ENROLL_SAMPLES = 3
INDICATOR_REPEAT_S = 10.0


@dataclass
class Refusal(Exception):
    code: str
    message: str            # Hebrew, spoken by the Skill

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


async def _call(cb, *a) -> Any:
    if cb is None:
        return None
    r = cb(*a)
    return await r if inspect.isawaitable(r) else r


class CameraService:
    def __init__(self, cfg, home: Path, *, switches: Switches, memory=None, health=None, send: SendFn | None = None,
                 activity: Callable[[], bool] | None = None, vision=None, detector: Detector | None = None,
                 embedder: Embedder | None = None, faces: FaceStore | None = None,
                 models: Callable[[], tuple[Detector | None, Embedder | None]] | None = None,
                 probe_fn: Callable[[], Probe] | None = None, camera_factory: Callable[[Probe], Camera] | None = None,
                 on_arrive: Callable[[str | None], Any] | None = None, clock=time.monotonic) -> None:
        self.cfg, self.home, self.switches, self.memory, self.health = cfg, Path(home), switches, memory, health
        self.send, self.activity, self.vision = send, activity, vision
        self.detector, self.embedder = detector, embedder
        self._models = models           # lazy loader (OpenCV + model files), run once when the camera is first on
        self._faces = faces
        self._probe_fn = probe_fn or (lambda: default_probe(cfg.backend))
        self._factory = camera_factory or (lambda p: make_camera(p, cfg))
        self.on_arrive = on_arrive
        self._clock = clock
        self.tracker = PresenceTracker(mirror=cfg.mirror_gaze)
        self.cam: Camera | None = None
        self.streaming = False
        self.indicator = False
        self.away = False
        self.current_person: str | None = None     # recognised (opted-in) person in front, None = guest/unknown
        self._last_human = clock()
        self._probe: Probe | None = None
        self._probe_t = -1e9
        self._open_failed_t = -1e9
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._state: tuple = ()
        self._ind_sent = -1e9
        switches.register_all({"camera": self.available, "face_recognition": self.face_available,
                               "camera_when_away": self.available})

    # ------------------------------------------------------------------ availability
    def probe(self, fresh: bool = False) -> Probe:
        """The cached probe; `fresh` runs it now (blocking: the Windows PnP query takes ~1 s, so the loop calls
        it in a worker thread). Before the first probe the camera counts as unavailable (privacy default)."""
        if fresh:
            try:
                p = self._probe_fn()
            except Exception as e:          # a broken probe = no camera, never a crash
                p = Probe(False, "none", f"camera probe failed: {type(e).__name__}")
            if self._probe is None or (p.available, p.detail) != (self._probe.available, self._probe.detail):
                logs.info("camera", "CAMERA_PROBE", p.detail, available=p.available, backend=p.backend)
            self._probe, self._probe_t = p, self._clock()
        return self._probe or Probe(False, "none", "checking for a camera")

    async def refresh_probe(self) -> None:
        if self._clock() - self._probe_t > PROBE_TTL_S:
            await asyncio.to_thread(self.probe, True)

    def available(self) -> tuple[bool, str]:
        p = self.probe()
        return p.available, p.detail

    def _load_models(self) -> None:
        if self._models is not None and self.switches.active("camera"):
            loader, self._models = self._models, None
            try:
                det, emb = loader()
            except Exception:
                det, emb = None, None
                logs.warn("camera", "CAMERA_MODELS_FAILED", "local face models could not be loaded")
            self.detector, self.embedder = self.detector or det, self.embedder or emb
            logs.info("camera", "CAMERA_MODELS", "local detection models",
                      detector=getattr(self.detector, "name", None), recognition=getattr(self.embedder, "name", None))

    def face_available(self) -> tuple[bool, str]:
        ok, detail = self.available()
        if not ok:
            return ok, detail
        if self.embedder is None and self._models is None:
            return False, "face models not installed (docs/CAMERA_SETUP.md)"
        return True, "local face models ready"

    @property
    def faces(self) -> FaceStore:
        if self._faces is None:
            self._faces = FaceStore(self.home / "faces.db")
        return self._faces

    def wants_stream(self) -> bool:
        self._load_models()
        if self.detector is None or not self.switches.active("camera"):
            return False
        return not self.away or self.switches.active("camera_when_away")

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._supervise())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        async with self._lock:
            await self._close()
        if self._faces is not None:
            self._faces.close()
            self._faces = None

    async def _supervise(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logs.error("camera", "CAMERA_LOOP_ERROR", "camera loop failed", exc=True)
                async with self._lock:
                    await self._close()
                await asyncio.sleep(5)
            await asyncio.sleep(1.0 / self.cfg.presence_fps if self.streaming else 1.0)

    async def tick(self) -> None:
        """One supervisor step (tests call it directly with a fake clock)."""
        await self.refresh_probe()
        now = self._clock()
        if self.activity is not None and self.activity():
            self.user_activity()
        if not self.away and now - max(self._last_human, self.tracker.last_seen) > self.cfg.away_after_s:
            self.away = True
            logs.info("camera", "CAMERA_AWAY", "nobody seen and no conversation: away mode",
                      camera_when_away=self.switches.active("camera_when_away"))
        want = self.wants_stream()
        async with self._lock:
            if want and not self.streaming:
                if now - self._open_failed_t < RETRY_OPEN_S:
                    return
                await self._open()
                self.streaming = self.cam is not None
                if self.streaming:
                    logs.info("camera", "CAMERA_STREAM_ON", "presence detection on (local, low resolution)",
                              backend=self.cam.name, fps=self.cfg.presence_fps)
            elif not want and self.streaming:
                await self._close()
                logs.info("camera", "CAMERA_STREAM_OFF", "camera off", away=self.away)
            if not self.streaming:
                self._report()
                return
            frame = await asyncio.to_thread(self.cam.read)
        if now - self._ind_sent > INDICATOR_REPEAT_S:      # a face that reconnected learns the camera is on
            await self.resend_indicator()
        await self._process(frame, now)

    async def _process(self, frame: Frame, now: float) -> None:
        try:
            boxes = await asyncio.to_thread(self.detector.detect, frame) if not is_dark(frame) else []
            up = self.tracker.feed(boxes, now)
            if up.arrived:
                self._last_human = now
                self.away = False
                self.current_person = await self._recognise(frame, boxes)
                logs.info("camera", "PRESENCE_ARRIVED", "someone is in front of the camera", faces=up.faces,
                          known=self.current_person is not None)
                await _call(self.on_arrive, self.current_person)
            if up.gaze is not None:
                await _call(self.send, "face.set_gaze", up.gaze.payload())
            if up.left:
                self.current_person = None
                logs.info("camera", "PRESENCE_LEFT", "nobody in front of the camera")
                await _call(self.send, "face.set_gaze", {"x": 0.0, "y": 0.0, "confidence": 0.0})
        finally:
            frame.drop()

    async def _recognise(self, frame: Frame, boxes: list[Box]) -> str | None:
        if self.embedder is None or not boxes or not self.switches.active("face_recognition"):
            return None
        try:
            if self.faces.count() == 0:
                return None
            vec = await asyncio.to_thread(self.embedder.embed, frame, max(boxes, key=lambda b: b.area))
            m = self.faces.match(vec)
            return m[0] if m else None
        except Exception:
            logs.warn("camera", "FACE_MATCH_FAILED", "face recognition failed; treating as a guest")
            return None

    def user_activity(self) -> None:
        """A sign of the user that is not the camera (wake word, conversation, button): leave away mode."""
        self._last_human = self._clock()
        if self.away:
            self.away = False
            logs.info("camera", "CAMERA_BACK", "user activity: away mode ended")

    def external_presence(self, present: bool) -> None:
        """DevicePort presence.changed (mmWave/PIR sensor, simulator toggle)."""
        if present:
            self.user_activity()

    # ------------------------------------------------------------------ sensor + indicator
    async def _open(self) -> None:
        p = self.probe()
        try:
            if not p.available:
                raise CameraUnavailable("CAMERA_UNAVAILABLE", p.detail, p.action)
            cam = self._factory(p)
            await asyncio.to_thread(cam.open)
        except CameraUnavailable as e:
            self._open_failed_t = self._clock()
            self._probe_t = -1e9                    # re-probe (in a worker thread) on the next tick
            logs.warn("camera", e.code, e.message, action=e.action)
            self._set_health("unavailable", e.message, e.action)
            self.cam = None
            return
        self.cam = cam
        await self._set_indicator(True)

    async def _close(self) -> None:
        if self.cam is not None:
            try:
                await asyncio.to_thread(self.cam.close)
            except Exception:
                logs.warn("camera", "CAMERA_CLOSE_FAILED", "camera did not close cleanly")
        self.cam, self.streaming = None, False
        self.tracker.reset()
        await self._set_indicator(False)

    async def _set_indicator(self, on: bool, flash: bool = False) -> None:
        if on != self.indicator or flash:
            self.indicator = on
            self._ind_sent = self._clock()
            await _call(self.send, "camera.indicator", {"on": on, "flash": flash})
        self._report()

    async def resend_indicator(self) -> None:
        """After the face (re)connects."""
        self._ind_sent = self._clock()
        await _call(self.send, "camera.indicator", {"on": self.indicator, "flash": False})

    def _set_health(self, status: str, detail: str, action: str = "") -> None:
        key = (status, detail)
        if self.health is not None and key != self._state:
            self._state = key
            self.health.set("camera", status, detail, action)

    def _report(self) -> None:
        ok, detail = self.available()
        if not ok:
            self._set_health("unavailable", detail, self.probe().action)
        elif not self.switches.active("camera"):
            self._set_health("disabled", "camera switched off (default); turn it on in the dashboard")
        elif self.indicator:
            self._set_health("ok", "camera ON (software indicator on the face; frames stay in RAM)")
        elif self.away:
            self._set_health("ok", "away mode: camera OFF until someone is back")
        else:
            self._set_health("ok", f"camera ready ({detail}); opens only when needed")

    # ------------------------------------------------------------------ stills (Skill side)
    def _require(self, switch: str = "camera") -> None:
        ok, detail = self.available()
        if not ok:
            raise Refusal("CAMERA_UNAVAILABLE", "אין לי מצלמה מחוברת כרגע.")
        if not self.switches.active("camera"):
            raise Refusal("CAMERA_DISABLED", "המצלמה כבויה. אפשר להדליק אותה בלוח הבקרה.")
        if switch == "face_recognition" and not self.switches.active("face_recognition"):
            raise Refusal("FACE_RECOGNITION_DISABLED", "זיהוי פנים כבוי. אפשר להדליק אותו בלוח הבקרה.")

    async def capture_still(self, purpose: str, flash: bool = False) -> Frame:
        """One full-resolution still in RAM. The caller must frame.drop() it after use."""
        self._require()
        self.user_activity()                        # the user just asked: they are here
        async with self._lock:
            opened_here = self.cam is None
            if opened_here:
                await self._open()
                if self.cam is None:
                    raise Refusal("CAMERA_UNAVAILABLE", "לא הצלחתי להפעיל את המצלמה.")
            try:
                await self._set_indicator(True, flash=flash)
                frame = await asyncio.to_thread(self.cam.still)
            except CameraUnavailable as e:
                logs.warn("camera", e.code, e.message)
                await self._close()
                raise Refusal(e.code, "המצלמה לא החזירה תמונה.") from e
            finally:
                if opened_here and self.cam is not None:
                    await self._close()
        logs.info("camera", "CAMERA_STILL", "still captured (RAM only)", purpose=purpose, width=frame.width,
                  height=frame.height)
        if is_dark(frame):
            frame.drop()
            raise Refusal("CAMERA_SHUTTER_CLOSED", "התמונה שחורה לגמרי. אולי התריס של המצלמה סגור?")
        return frame

    async def look(self, mode: str, question: str) -> tuple[str, dict]:
        """"מה זה?" / read text: one still -> backend vision. Returns (answer, facts for the Skill)."""
        self._require()
        if self.vision is None:
            raise Refusal("VISION_UNAVAILABLE", "זיהוי תמונה דורש חיבור למפתח OpenAI.")
        limit = self.cfg.daily_cloud_photos
        used = self.memory.usage_today("camera_photos") if self.memory is not None else 0
        if used >= limit:
            raise Refusal("CAMERA_CLOUD_LIMIT", "הגעתי למכסת התמונות היומית לבדיקה בענן.")
        frame = await self.capture_still(mode, flash=True)
        try:
            mime, data = encode_still(frame, self.cfg.still_max_side)
            w, h = frame.width, frame.height
        finally:
            frame.drop()
        url = data_url(mime, data)
        n = len(data)
        del data
        if self.memory is not None:
            self.memory.bump_usage("camera_photos")
        logs.info("camera", "CAMERA_CLOUD_SEND", "one photo sent to the backend vision model", purpose=mode,
                  bytes=n, mime=mime, width=w, height=h, model=getattr(self.vision, "model", ""),
                  photos_today=used + 1, limit=limit)
        try:
            text = await self.vision.ask(mode, question, url)
        finally:
            del url
        return text, {"sent_bytes": n, "photos_today": used + 1}

    async def identify(self) -> str | None:
        self._require("face_recognition")
        self._load_models()
        if self.embedder is None or self.detector is None:
            raise Refusal("FACE_MODELS_MISSING", "מודל זיהוי הפנים לא מותקן עדיין.")
        frame = await self.capture_still("identify")
        try:
            boxes = await asyncio.to_thread(self.detector.detect, frame)
            if not boxes:
                raise Refusal("NO_FACE", "אני לא רואה פנים מול המצלמה.")
            vec = await asyncio.to_thread(self.embedder.embed, frame, max(boxes, key=lambda b: b.area))
        finally:
            frame.drop()
        m = self.faces.match(vec)
        self.current_person = m[0] if m else None
        logs.info("camera", "FACE_IDENTIFY", "who-is-this checked locally", known=m is not None)
        return self.current_person

    def check_enroll(self) -> None:
        """Raise the Refusal enrolment would hit (switches, camera, models) BEFORE consent is asked."""
        self._require("face_recognition")
        self._load_models()
        if self.embedder is None or self.detector is None:
            raise Refusal("FACE_MODELS_MISSING", "מודל זיהוי הפנים לא מותקן עדיין.")

    async def enroll(self, name: str) -> int:
        """Called only after the person confirmed on screen/by voice (the Skill's consent prompt)."""
        self.check_enroll()
        vecs = []
        for i in range(ENROLL_SAMPLES):
            frame = await self.capture_still("enroll")
            try:
                boxes = await asyncio.to_thread(self.detector.detect, frame)
                if len(boxes) != 1:     # never enrol a bystander: exactly one face in every sample
                    raise Refusal("ENROLL_ONE_FACE", "צריך שרק אדם אחד יהיה מול המצלמה. ננסה שוב?")
                vecs.append(await asyncio.to_thread(self.embedder.embed, frame, boxes[0]))
            finally:
                frame.drop()
            if i < ENROLL_SAMPLES - 1:
                await asyncio.sleep(0.3)
        n = self.faces.enroll(name, vecs, consent="voice request + on-screen confirmation")
        logs.info("camera", "FACE_ENROLLED", "a person opted in to face recognition (local embeddings only)",
                  samples=n)
        return n

    def _has_faces(self) -> bool:
        return self._faces is not None or (self.home / "faces.db").exists()

    def forget(self, name: str) -> int:
        """Always works, also with the switches off (deletion on request is never blocked)."""
        if not self._has_faces():
            return 0
        n = self.faces.forget(name)
        if n and self.current_person and self.current_person.casefold() == name.strip().casefold():
            self.current_person = None
        logs.info("camera", "FACE_FORGOTTEN", "face data deleted on request", deleted=n)
        return n

    def forget_all(self) -> int:
        if not self._has_faces():
            return 0
        n = self.faces.forget_all()
        self.current_person = None
        logs.info("camera", "FACE_FORGOTTEN_ALL", "all face data deleted on request", deleted=n)
        return n

    def status(self) -> dict:
        ok, detail = self.available()
        return {"available": ok, "detail": detail, "enabled": self.switches.active("camera"),
                "face_recognition": self.switches.active("face_recognition"),
                "when_away": self.switches.active("camera_when_away"), "indicator": self.indicator,
                "streaming": self.streaming, "away": self.away, "present": self.tracker.present,
                "presence_detector": getattr(self.detector, "name", None),
                "enrolled": self.faces.count() if self._has_faces() else 0,
                "switches": self.switches.backend}
