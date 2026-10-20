"""Bounded CLI prerequisites. Never opens sensors, starts services or signs into accounts."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from .runtime.lock import AlreadyRunning, InstanceLock

CAMERA_FEATURES = frozenset({"camera", "face_recognition", "camera_when_away"})
SFACE_FILE = "face_recognition_sface_2021dec.onnx"
# Official asset and digest already approved in docs/CAMERA_SETUP.md.
SFACE_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/" + SFACE_FILE)
SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
SFACE_BYTES = 38_696_353


def _present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _missing_extra(fid: str) -> str | None:
    if fid in CAMERA_FEATURES and not _present("cv2"):
        return "camera"
    if fid == "wake_word" and (not _present("sounddevice") or
                               not any(_present(m) for m in ("pyopen_wakeword", "openwakeword"))):
        return "voice"
    if fid == "browser" and not _present("playwright"):
        return "browser"
    return None


def _source_core() -> Path | None:
    if os.environ.get("JARVES_APP"):
        return None
    core = Path(__file__).resolve().parents[2]
    if (core / "pyproject.toml").is_file() and (core / "uv.lock").is_file():
        return core
    return None


def _install_extra(extra: str) -> None:
    core = _source_core()
    if core is None:
        raise RuntimeError("This app is missing a bundled dependency; repair/update the JARVES release. "
                           "No developer tools are required for installed apps.")
    if Path(sys.prefix).resolve() != (core / ".venv").resolve():
        raise RuntimeError("Run the source checkout's jarvis command in its core/.venv before installing prerequisites.")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("The source checkout needs uv; restore the documented developer environment first.")
    # Frozen lock; inexact keeps other installed extras; never reinstall the running jarves entry point.
    subprocess.run([uv, "sync", "--directory", str(core), "--frozen", "--inexact", "--no-install-project",
                    "--extra", extra], check=True, timeout=300)
    importlib.invalidate_caches()


def _model_valid(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != SFACE_BYTES:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == SFACE_SHA256


def _download_model(path: Path) -> None:
    """Only this named official model; validate before atomic replacement, keeping any previous file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sface-", suffix=".tmp", delete=False) as out:
            scratch = Path(out.name)
            started, count = time.monotonic(), 0
            with urlopen(SFACE_URL, timeout=20) as response:
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > SFACE_BYTES or time.monotonic() - started > 120:
                        raise RuntimeError("SFace download exceeded its size/time limit; try again later.")
                    out.write(chunk)
        if not _model_valid(scratch):
            raise RuntimeError("SFace download failed the pinned size/SHA256 check; no model was installed.")
        os.replace(scratch, path)
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)


def prepare(fid: str, features, home: Path) -> tuple[bool, str]:
    """Install allowlisted software, recheck actual availability, then let the caller persist the switch."""
    try:
        extra = _missing_extra(fid)
        model = Path(home) / "models" / SFACE_FILE
        need_model = fid == "face_recognition" and not _model_valid(model)
        if extra or need_model:
            lock = InstanceLock(Path(home))
            try:
                lock.acquire("feature prerequisites")
            except AlreadyRunning:
                return False, "Prerequisites pending: Core is running. Run jarvis stop, then repeat this command."
            try:
                if extra:
                    print(f"Installing locked {extra} prerequisites (existing extras are preserved)...")
                    _install_extra(extra)
                    if _missing_extra(fid):
                        return False, "Dependency install completed but the required modules are still missing; repair the environment."
                if need_model:
                    print("Installing the pinned official SFace model (38.7 MB); no camera or face data is opened...")
                    _download_model(model)
                    if not _model_valid(model):
                        return False, "SFace is still unavailable after installation."
            finally:
                lock.release()
        features.invalidate()
        ok, reason = features.available(fid)
        if not ok:
            return False, "Prerequisites pending: " + reason + ". Hardware, account consent and live services require the user."
        return True, ""
    except KeyboardInterrupt:
        return False, "Prerequisite setup cancelled; the feature switch was not changed."
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        return False, f"Prerequisite setup failed: {e}. The feature switch was not changed."
