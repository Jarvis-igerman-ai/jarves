"""Local face detection and (opt-in) face embeddings. Nothing here touches the network.

Library choice (researched 2026-10-19, docs/CAMERA_SETUP.md "Why OpenCV YuNet + SFace"): OpenCV's own DNN face
models from the OpenCV Zoo, one dependency (opencv-python-headless, aarch64 wheels for the Pi 5):
- detection: YuNet (`FaceDetectorYN`, `face_detection_yunet_2023mar.onnx`, MIT, ~230 kB, bundled in models/;
  ~6 ms at 160x120 on a Pi 4 CPU per the zoo benchmark, measured 7 ms at 320x240 with OpenCV 5.0 on the dev PC).
  Fallback without the model file on OpenCV 4.x: the bundled Haar cascade (presence only).
- recognition: SFace (`FaceRecognizerSF`, `face_recognition_sface_2021dec.onnx`, Apache-2.0, 128-d embedding,
  ~100 ms per face on a Pi 4 CPU); runs only for "who is this" / enrolment, never continuously.
Both are fallback-grade local models (AGENTS.md): they drive presence, gaze and greetings, never security.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from .frames import Frame

# YuNet 2026may = the 2023mar weights re-exported with dynamic input size (needed by OpenCV 5's ONNX Runtime
# engine); either file works with the default OpenCV DNN engine. First one present wins.
YUNET_FILES = ("face_detection_yunet_2026may.onnx", "face_detection_yunet_2023mar.onnx")
YUNET_FILE = YUNET_FILES[-1]
SFACE_FILE = "face_recognition_sface_2021dec.onnx"


# YuNet 2023mar (MIT, 232 589 bytes, sha256 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4) ships
# with the code, so presence works with just the `camera` extra. SFace (37 MB) is a manual, opt-in download.
BUNDLED = Path(__file__).with_name("models")


def yunet_model(models_dir: Path) -> Path | None:
    return next((d / f for d in (models_dir, BUNDLED) for f in YUNET_FILES if (d / f).exists()), None)
# OpenCV FaceRecognizerSF documentation: same person when cosine >= 0.363 (L2 <= 1.128)
SFACE_COSINE_MATCH = 0.363


@dataclass
class Box:
    """A face, normalised to the frame (0..1), with the detector score and the raw detector row (for alignment)."""
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0
    raw: np.ndarray | None = field(default=None, repr=False)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return self.w * self.h


class Detector(Protocol):
    name: str

    def detect(self, frame: Frame) -> list[Box]: ...


class Embedder(Protocol):
    name: str

    def embed(self, frame: Frame, box: Box) -> np.ndarray: ...


def unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(unit(a), unit(b)))


class YuNetDetector:
    name = "yunet"

    def __init__(self, model: Path, score: float = 0.8) -> None:
        import cv2
        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(str(model), "", (320, 240), score, 0.3, 5000)
        self._size = (320, 240)

    def detect(self, frame: Frame) -> list[Box]:
        cv2 = self._cv2
        h, w = frame.pixels.shape[:2]
        if (w, h) != self._size:
            self._det.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self._det.detect(cv2.cvtColor(frame.pixels, cv2.COLOR_RGB2BGR))
        out = []
        for row in faces if faces is not None else []:
            x, y, bw, bh = (float(v) for v in row[:4])
            out.append(Box(x / w, y / h, bw / w, bh / h, float(row[-1]), np.array(row, np.float32)))
        return out


class HaarDetector:
    """Presence-only fallback when the YuNet file is missing: the frontal-face cascade bundled with OpenCV 4.x
    (OpenCV 5 dropped the cascade API: then there is no fallback and presence is unavailable)."""
    name = "haar"

    def __init__(self) -> None:
        import cv2
        if not hasattr(cv2, "CascadeClassifier"):
            raise RuntimeError("this OpenCV has no Haar cascades")
        self._cv2 = cv2
        self._cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self._cc.empty():
            raise RuntimeError("haar cascade missing")

    def detect(self, frame: Frame) -> list[Box]:
        cv2 = self._cv2
        h, w = frame.pixels.shape[:2]
        gray = cv2.cvtColor(frame.pixels, cv2.COLOR_RGB2GRAY)
        found = self._cc.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24))
        return [Box(x / w, y / h, bw / w, bh / h, 0.9) for (x, y, bw, bh) in found]


class SFaceEmbedder:
    name = "sface"

    def __init__(self, model: Path) -> None:
        import cv2
        self._cv2 = cv2
        self._rec = cv2.FaceRecognizerSF.create(str(model), "")

    def embed(self, frame: Frame, box: Box) -> np.ndarray:
        if box.raw is None:
            raise ValueError("SFace needs YuNet landmarks for alignment")
        cv2 = self._cv2
        bgr = cv2.cvtColor(frame.pixels, cv2.COLOR_RGB2BGR)
        crop = self._rec.alignCrop(bgr, box.raw)
        return unit(self._rec.feature(crop))


def make_detector(models_dir: Path) -> Detector | None:
    """YuNet when its model file is installed, else the Haar fallback, else None (no OpenCV)."""
    try:
        model = yunet_model(models_dir)
        return YuNetDetector(model) if model is not None else HaarDetector()
    except Exception:
        return None


def make_embedder(models_dir: Path) -> Embedder | None:
    """SFace needs both model files (alignment uses YuNet landmarks)."""
    if not ((models_dir / SFACE_FILE).exists() and yunet_model(models_dir) is not None):
        return None
    try:
        return SFaceEmbedder(models_dir / SFACE_FILE)
    except Exception:
        return None
