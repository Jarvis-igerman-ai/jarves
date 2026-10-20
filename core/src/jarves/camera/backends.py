"""Camera adapters (the DevicePort side of the camera: docs/DEVICE_CONTRACT.md "Core MUST NOT import camera
drivers directly; only the adapter does"). This is the ONLY module that imports picamera2 / OpenCV capture.

- `FakeCamera`      - replays fixture arrays (tests, CI, the simulator). No hardware.
- `OpenCVCamera`    - USB webcam on a development PC (opencv-python-headless).
- `Picamera2Camera` - Raspberry Pi Camera Module 3 on the Pi 5 (picamera2 / libcamera, apt package).

`probe()` answers "is there a camera?" WITHOUT opening the sensor (no streaming, no activity light): picamera2's
global camera list, /dev/video* on Linux, the Windows config-manager camera interface list. No camera -> unavailable, never a crash.
All calls are blocking: the service runs them in a worker thread.
"""
from __future__ import annotations

import glob
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .frames import Frame, as_rgb


class CameraUnavailable(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


@dataclass
class Probe:
    available: bool
    backend: str            # "picamera2" | "opencv" | "fake" | "none"
    detail: str
    action: str = ""


class Camera:
    name = "camera"

    def __init__(self) -> None:
        self.is_open = False

    def open(self) -> None:
        raise NotImplementedError

    def read(self) -> Frame:
        """One low-resolution preview frame (local presence detection)."""
        raise NotImplementedError

    def still(self) -> Frame:
        """One full-resolution still (autofocus where the sensor has it)."""
        return self.read()

    def close(self) -> None:
        self.is_open = False


class FakeCamera(Camera):
    """Replays frames (arrays, or .npy fixture files) in order, looping. `fail_open` simulates a missing/busy
    device; `stills` (optional) are returned by still() instead of the preview frames."""
    name = "fake"

    def __init__(self, frames: Iterable[np.ndarray | str | Path] = (), *, stills: Iterable | None = None,
                 fail_open: bool = False) -> None:
        super().__init__()
        self.frames = [self._load(f) for f in frames] or [np.full((240, 320, 3), 90, np.uint8)]
        self.stills = [self._load(f) for f in stills] if stills is not None else None
        self.fail_open = fail_open
        self.opened = self.reads = self.still_count = 0
        self._i = self._j = 0

    @staticmethod
    def _load(f) -> np.ndarray:
        return as_rgb(np.load(f) if isinstance(f, (str, Path)) else f)

    def open(self) -> None:
        if self.fail_open:
            raise CameraUnavailable("CAMERA_OPEN_FAILED", "fake camera set to fail", "")
        self.is_open = True
        self.opened += 1

    def _need_open(self) -> None:
        if not self.is_open:
            raise CameraUnavailable("CAMERA_CLOSED", "camera is not open")

    def read(self) -> Frame:
        self._need_open()
        px = self.frames[self._i % len(self.frames)]
        self._i += 1
        self.reads += 1
        return Frame(px.copy())

    def still(self) -> Frame:
        self._need_open()
        self.still_count += 1
        if self.stills:
            px = self.stills[self._j % len(self.stills)]
            self._j += 1
            return Frame(px.copy())
        return self.read()


class OpenCVCamera(Camera):
    """USB webcam (development PC). The preview is requested at the configured low resolution."""
    name = "opencv"

    def __init__(self, device: int | str = 0, width: int = 320, height: int = 240) -> None:
        super().__init__()
        self.device, self.width, self.height = device, width, height
        self._cap = None

    def open(self) -> None:
        try:
            import cv2
        except ImportError as e:
            raise CameraUnavailable("CAMERA_NO_LIBRARY", "OpenCV is not installed",
                                    "uv sync --extra camera") from e
        # an int is a webcam index; a str is a video file/stream (tests replay a recorded clip through the real path)
        api = cv2.CAP_DSHOW if sys.platform == "win32" and isinstance(self.device, int) else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.device, api)
        if not cap.isOpened():
            cap.release()
            raise CameraUnavailable("CAMERA_OPEN_FAILED", f"webcam {self.device} could not be opened",
                                    "close other apps using the camera; check the device index")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap, self.is_open = cap, True

    def _grab(self) -> np.ndarray:
        import cv2
        if self._cap is None:
            raise CameraUnavailable("CAMERA_CLOSED", "camera is not open")
        ok, bgr = self._cap.read()
        if not ok or bgr is None:
            raise CameraUnavailable("CAMERA_READ_FAILED", "the webcam returned no frame", "reconnect the camera")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def read(self) -> Frame:
        return Frame(self._grab())

    def still(self) -> Frame:
        for _ in range(3):              # webcams adjust exposure over the first frames after a resolution change
            self._cap.grab()
        return Frame(self._grab())

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap, self.is_open = None, False


class Picamera2Camera(Camera):
    """Camera Module 3 (Wide) on the Pi 5: a small RGB preview stream for presence; still() switches to the full
    still configuration and runs one autofocus cycle (documents / "מה זה?")."""
    name = "picamera2"

    def __init__(self, width: int = 320, height: int = 240) -> None:
        super().__init__()
        self.width, self.height = width, height
        self._cam = self._preview = self._still = None

    def open(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError as e:
            raise CameraUnavailable("CAMERA_NO_LIBRARY", "picamera2 is not installed",
                                    "sudo apt install python3-picamera2") from e
        try:
            cam = Picamera2()
            self._preview = cam.create_preview_configuration(main={"size": (self.width, self.height),
                                                                   "format": "RGB888"})
            self._still = cam.create_still_configuration(main={"format": "RGB888"})
            cam.configure(self._preview)
            cam.start()
        except Exception as e:          # libcamera raises RuntimeError/IndexError when no sensor is attached
            raise CameraUnavailable("CAMERA_OPEN_FAILED", f"Pi camera could not start: {type(e).__name__}",
                                    "check the ribbon cable and `rpicam-hello --list-cameras`") from e
        self._cam, self.is_open = cam, True

    def read(self) -> Frame:
        if self._cam is None:
            raise CameraUnavailable("CAMERA_CLOSED", "camera is not open")
        # picamera2 "RGB888" is BGR byte order in memory
        return Frame(np.ascontiguousarray(self._cam.capture_array("main")[:, :, ::-1]))

    def still(self) -> Frame:
        if self._cam is None:
            raise CameraUnavailable("CAMERA_CLOSED", "camera is not open")
        try:
            self._cam.autofocus_cycle()
        except Exception:
            pass                        # fixed-focus module: no autofocus
        px = self._cam.switch_mode_and_capture_array(self._still, "main")
        return Frame(np.ascontiguousarray(px[:, :, ::-1]))

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            except Exception:
                pass
        self._cam, self.is_open = None, False


# ---------------------------------------------------------------------------------------- probe (never opens)
def _picamera2_cameras() -> list | None:
    try:
        from picamera2 import Picamera2
    except ImportError:
        return None
    try:
        return list(Picamera2.global_camera_info())
    except Exception:
        return []


KSCATEGORY_VIDEO_CAMERA = "e5323777-f976-4f5b-9b55-b94699c46e44"


def _windows_cameras() -> list[str]:
    """PRESENT video-camera device interfaces from the Windows config manager (cfgmgr32, ~2 ms, opens nothing)."""
    import ctypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]
    try:
        cm = ctypes.WinDLL("cfgmgr32")
        g = GUID.from_buffer_copy(uuid.UUID(KSCATEGORY_VIDEO_CAMERA).bytes_le)
        size = ctypes.c_ulong()
        if cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(g), None, 0) != 0 or size.value < 2:
            return []
        buf = ctypes.create_unicode_buffer(size.value)
        if cm.CM_Get_Device_Interface_ListW(ctypes.byref(g), None, buf, size, 0) != 0:
            return []
        items = [x for x in ctypes.wstring_at(buf, size.value).split("\0") if x]
    except (OSError, AttributeError, ValueError):
        return []
    # "\\?\USB#VID_30C9&PID_0030&MI_00#..." -> "USB VID_30C9&PID_0030" (no serial numbers in logs)
    return [" ".join(x.lstrip("\\?").split("#")[:2]) for x in items]


def _video_devices() -> list[str]:
    if sys.platform == "win32":
        return _windows_cameras()
    if sys.platform.startswith("linux"):
        out = []
        for d in sorted(glob.glob("/sys/class/video4linux/video*")):
            try:
                name = Path(d, "name").read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                name = ""
            if not any(s in name.lower() for s in _LINUX_NOT_CAMERAS):
                out.append(f"/dev/{Path(d).name} {name}".strip())
        return out
    return []


# Pi 5 / Pi 4 media nodes that are not cameras (ISP back end, CSI front end, codecs)
_LINUX_NOT_CAMERAS = ("pispbe", "rp1-cfe", "bcm2835", "hevc", "codec", "isp", "unicam")


def _cv2_present() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def probe(backend: str = "auto", *, picam: Callable[[], list | None] = _picamera2_cameras,
          devices: Callable[[], list[str]] = _video_devices, cv2_present: Callable[[], bool] = _cv2_present) -> Probe:
    """Is a usable camera attached? Cheap, never opens the sensor. The callables are injectable for tests."""
    if backend == "none":
        return Probe(False, "none", "camera.backend = none", "")
    if backend in ("auto", "picamera2"):
        cams = picam()
        if cams:
            return Probe(True, "picamera2", f"Pi camera: {cams[0].get('Model', 'camera') if isinstance(cams[0], dict) else 'camera'}")
        if backend == "picamera2":
            return Probe(False, "picamera2", "no Pi camera found" if cams is not None else "picamera2 not installed",
                         "connect the camera ribbon; sudo apt install python3-picamera2")
    devs = devices()
    if not devs:
        return Probe(False, "none", "no camera connected", "connect a camera (Pi: Camera Module 3 Wide)")
    if not cv2_present():
        return Probe(False, "opencv", f"camera found ({devs[0]}) but OpenCV is not installed",
                     "uv sync --extra camera")
    return Probe(True, "opencv", f"webcam: {devs[0]}")


def make_camera(p: Probe, cfg) -> Camera:
    """The adapter for a successful probe (`cfg` = config.CameraConfig)."""
    if p.backend == "picamera2":
        return Picamera2Camera(cfg.preview_width, cfg.preview_height)
    if p.backend == "opencv":
        return OpenCVCamera(cfg.device, cfg.preview_width, cfg.preview_height)
    raise CameraUnavailable("CAMERA_UNAVAILABLE", p.detail, p.action)
