"""Camera adapters (the DevicePort side of the camera: docs/DEVICE_CONTRACT.md "Core MUST NOT import camera
drivers directly; only the adapter does"). This is the ONLY module that imports picamera2 / OpenCV capture.

- `FakeCamera`      - replays fixture arrays (tests, CI, the simulator). No hardware.
- `OpenCVCamera`    - USB webcam on a development PC (opencv-python-headless).
- `Picamera2Camera` - Raspberry Pi Camera Module 3 on the Pi 5 (picamera2 / libcamera, apt package).

`probe()` (camera/probe.py, re-exported here) answers "is there a camera?" WITHOUT opening the sensor (no streaming, no activity light): picamera2's
global camera list, /dev/video* on Linux, the Windows config-manager camera interface list. No camera -> unavailable, never a crash.
All calls are blocking: the service runs them in a worker thread.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import numpy as np

from .frames import Frame, as_rgb
from .probe import (KSCATEGORY_VIDEO_CAMERA, Probe, _cv2_present, _picamera2_cameras,  # noqa: F401 (re-export)
                    _video_devices, _windows_cameras, probe)


class CameraUnavailable(Exception):
    def __init__(self, code: str, message: str, action: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.action = code, message, action


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
        if sys.platform == "win32" and isinstance(self.device, int):
            # MJPG first: DirectShow's default raw format negotiates slowly and delays the first frame (#355)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
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


def make_camera(p: Probe, cfg) -> Camera:
    """The adapter for a successful probe (`cfg` = config.CameraConfig)."""
    if p.backend == "picamera2":
        return Picamera2Camera(cfg.preview_width, cfg.preview_height)
    if p.backend == "opencv":
        return OpenCVCamera(cfg.device, cfg.preview_width, cfg.preview_height)
    raise CameraUnavailable("CAMERA_UNAVAILABLE", p.detail, p.action)
