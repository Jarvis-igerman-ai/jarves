"""A marker the standalone dashboard leaves while it runs, so the installer can start exactly what was running before
an update (#166). The file lives in the data folder; it names the process and the safe flags only (no URL, no key, no
page). A killed process leaves it behind: the installer only trusts it when that pid is a process of the installed
program, so a stale marker never starts anything."""
from __future__ import annotations

import json
import os
from pathlib import Path

DASHBOARD_FILE = "dashboard.run.json"
SAFE_FLAGS = ("--lan", "--mobile", "--tls-selfsigned", "--tailnet-only")      # the installer allows exactly these


def dashboard_flags(args) -> list[str]:
    """The restartable flags of this dashboard run, from the parsed CLI arguments."""
    names = {"--lan": "lan", "--mobile": "mobile", "--tls-selfsigned": "tls_selfsigned", "--tailnet-only": "tailnet_only"}
    return [flag for flag in SAFE_FLAGS if getattr(args, names[flag], False)]


def write_dashboard(home: Path, flags: list[str], pid: int | None = None) -> None:
    try:
        path = Path(home) / DASHBOARD_FILE
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"pid": os.getpid() if pid is None else pid,
                                   "flags": [f for f in flags if f in SAFE_FLAGS]}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass                                           # a marker that cannot be written never stops the dashboard


def clear_dashboard(home: Path, pid: int | None = None) -> None:
    """Remove the marker on a normal stop, only when it is ours (a second dashboard keeps its own)."""
    path = Path(home) / DASHBOARD_FILE
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == (os.getpid() if pid is None else pid):
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def was_running_arg(was: dict | None) -> str | None:
    """The /WASRUNNING= value for setup: 'core,face,dashboard:lan:mobile' or 'none'; None = unknown (setup detects)."""
    if was is None:
        return None
    parts = [n for n in ("core", "face") if was.get(n)]
    if was.get("dashboard") is not None:
        flags = [f[2:] for f in was["dashboard"] if f in SAFE_FLAGS]
        parts.append(":".join(["dashboard"] + flags))
    return ",".join(parts) or "none"


def detect_running(home: Path, *, core_up: bool, face_up=None) -> dict:
    """What runs now, read BEFORE anything is stopped (same semantics as the installer's detect-only script): the Core
    (the caller's live status answer), the face window (a process check), the dashboard (its marker, only when that pid
    is alive; only the safe flags). Nothing is started or stopped here."""
    from .budget import pid_alive
    dash = None
    try:
        m = json.loads((Path(home) / DASHBOARD_FILE).read_text(encoding="utf-8"))
        if pid_alive(int(m["pid"])):
            dash = [f for f in m.get("flags") or [] if f in SAFE_FLAGS]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        face = bool((face_up or _face_running)())
    except Exception:
        face = False
    return {"core": bool(core_up), "face": face, "dashboard": dash}


def _face_running() -> bool:
    import subprocess
    import sys
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq JARVES Face.exe", "/NH"], capture_output=True, text=True,
                         timeout=10).stdout
    return "JARVES Face.exe" in out
