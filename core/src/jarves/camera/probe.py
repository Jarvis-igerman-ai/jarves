"""Is there a camera? Answered WITHOUT opening the sensor and without importing numpy / OpenCV (the feature probe
runs in every features list): picamera2's global camera list, /dev/video* on Linux, the Windows config-manager
camera interface list. No camera -> unavailable, never a crash. `backends` re-exports everything here.
"""
from __future__ import annotations

import glob
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Probe:
    available: bool
    backend: str            # "picamera2" | "opencv" | "fake" | "none"
    detail: str
    action: str = ""


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
    # the friendly name from the registry; a raw hardware id (VID_xxxx&PID_xxxx) is never shown as the name (#355)
    return [friendly_camera_name(x) for x in items]


def _registry_name(hw: str, instance: str) -> str:
    """FriendlyName / DeviceDesc of one USB device instance (read-only registry lookup, opens no camera)."""
    import winreg
    try:
        bs = "\x5c"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, bs.join(("SYSTEM", "CurrentControlSet", "Enum", "USB", hw, instance))) as k:
            for value in ("FriendlyName", "DeviceDesc"):
                try:
                    return str(winreg.QueryValueEx(k, value)[0])
                except OSError:
                    continue
    except OSError:
        pass
    return ""


def friendly_camera_name(interface: str, lookup: Callable[[str, str], str] = _registry_name) -> str:
    """A device-interface path (USB#VID_30C9&PID_0030&MI_00#instance#guid) -> the camera's own name, else "USB camera"."""
    parts = interface.lstrip("\x5c?").split("#")
    name = ""
    if len(parts) >= 3:
        try:
            name = lookup(parts[1], parts[2])
        except Exception:
            name = ""
    name = name.rsplit(";", 1)[-1].strip()      # "@oem12.inf,%cam%;Integrated Camera" -> "Integrated Camera"
    if not name or name.startswith("@") or "VID_" in name.upper():
        return "USB camera"
    return name


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
    """OpenCV is installed. find_spec, not an import: loading cv2 costs ~0.2 s and tens of MB, and the feature probe
    runs on every features list (V4 perf fix). Opening the camera imports it for real and reports a broken install."""
    if "cv2" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("cv2") is not None
    except (ImportError, ValueError):
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
