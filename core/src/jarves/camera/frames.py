"""Frames in RAM only. A Frame is an RGB uint8 array plus a capture time; it is never written to disk or logs.
`encode_still` makes the one image a cloud request needs (JPEG with OpenCV, else a dependency-free PNG) and the
caller drops it right after the request. `is_dark` detects a closed shutter / covered lens (near-black frame)."""
from __future__ import annotations

import base64
import itertools
import struct
import time
import zlib
from dataclasses import dataclass, field

import numpy as np

_ids = itertools.count(1)

DARK_MEAN = 14.0        # 0..255 mean luminance below this = shutter closed / lens covered
DARK_SPREAD = 10.0      # ...and almost no detail (std), so a dim room with a lamp still counts as "open"


@dataclass
class Frame:
    pixels: np.ndarray                  # H x W x 3, uint8, RGB
    ts: float = field(default_factory=time.monotonic)
    id: str = field(default_factory=lambda: f"f{next(_ids)}")

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    def drop(self) -> None:
        """Release the pixels now (snapshots are deleted after use, not left to the garbage collector)."""
        self.pixels = np.zeros((1, 1, 3), np.uint8)


def as_rgb(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a)
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    if a.ndim != 3 or a.shape[2] not in (3, 4):
        raise ValueError(f"not an image array: shape {a.shape}")
    a = a[:, :, :3]
    if a.dtype != np.uint8:
        a = np.clip(a, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(a)


def luminance(frame: Frame) -> tuple[float, float]:
    """(mean, std) of the luma on a coarse grid (cheap on a Pi)."""
    p = frame.pixels[::4, ::4].astype(np.float32)
    y = 0.299 * p[:, :, 0] + 0.587 * p[:, :, 1] + 0.114 * p[:, :, 2]
    return float(y.mean()), float(y.std())


def is_dark(frame: Frame) -> bool:
    mean, std = luminance(frame)
    return mean < DARK_MEAN and std < DARK_SPREAD


def downscale(pixels: np.ndarray, max_side: int) -> np.ndarray:
    h, w = pixels.shape[:2]
    side = max(h, w)
    if side <= max_side:
        return pixels
    try:
        import cv2
        s = max_side / side
        return cv2.resize(pixels, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    except ImportError:                             # no OpenCV: integer striding (good enough for a photo)
        step = -(-side // max_side)
        return np.ascontiguousarray(pixels[::step, ::step])


def _png(pixels: np.ndarray) -> bytes:
    h, w = pixels.shape[:2]
    raw = b"".join(b"\x00" + pixels[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def encode_still(frame: Frame, max_side: int = 1280) -> tuple[str, bytes]:
    """(mime, bytes) of the downscaled image, in RAM."""
    px = downscale(frame.pixels, max_side)
    try:
        import cv2
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(px, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            return "image/jpeg", buf.tobytes()
    except ImportError:
        pass
    return "image/png", _png(px)


def encode_preview(frame: Frame, max_side: int = 320, quality: int = 70) -> tuple[str, bytes, int, int]:
    """(mime, bytes, width, height) of one small preview image for the local "What does he see?" window, in RAM.
    Without OpenCV the PNG fallback is halved again so a frame stays small on the local link."""
    try:
        import cv2
        px = downscale(frame.pixels, max_side)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(px, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            return "image/jpeg", buf.tobytes(), int(px.shape[1]), int(px.shape[0])
    except ImportError:
        pass
    px = downscale(frame.pixels, max(80, max_side // 2))
    return "image/png", _png(np.ascontiguousarray(px)), int(px.shape[1]), int(px.shape[0])


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
