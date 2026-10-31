"""The Godot window as a child the Core can open and close at any time ("show yourself" / "hide yourself").

The Core never depends on it: closing or crashing the window only changes the display state. At most ONE viewer
process is owned at a time; a show while one is starting or running is a no-op.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Awaitable, Callable

from .. import logs

GODOT_VERSION_GLOB = "Godot_v4.7.2-stable_win64.exe"
PROJECT = Path(__file__).resolve().parents[4] / "godot"


def find_godot(override: str = "") -> Path | None:
    """GODOT_EXE / runtime.viewer_exe, the WinGet install, then `godot` on PATH (Linux / Pi)."""
    override = (override or os.environ.get("GODOT_EXE", "")).strip('"')
    if override:
        p = Path(override)
        return p if p.is_file() else None
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
        hit = next(iter(sorted(root.glob(f"GodotEngine.GodotEngine_*/{GODOT_VERSION_GLOB}"), reverse=True)), None)
        if hit is not None:
            return hit
    for name in ("godot4", "godot"):
        w = shutil.which(name)
        if w:
            return Path(w)
    return None


def installed_face() -> Path:
    """Where the WP-M6 installer puts the exported face: <app>\\face, beside the bundled <app>\\python."""
    return Path(sys.executable).resolve().parent.parent / "face" / "JARVES Face.exe"


async def launch_godot(port: int, exe: str = "", scene: str = "res://loading.tscn"):
    """Start the room/face window (no console window of its own on Windows); the API key is never passed on."""
    flags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    # WP-M6 installed build: the exported face ({app}\face, next to {app}\python; jarvis.cmd also sets
    # JARVES_FACE_EXE, autostart does not). It starts at the loading screen via its override.cfg, because release
    # templates refuse a scene path argument.
    face = os.environ.get("JARVES_FACE_EXE", "").strip('"') or str(installed_face())
    if not exe and face and Path(face).is_file() and scene == "res://loading.tscn":
        return await asyncio.create_subprocess_exec(
            face, "--", f"--core-port={port}",
            env={k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}, creationflags=flags)
    executable = find_godot(exe)
    if executable is None:
        raise RuntimeError("Godot was not found; set GODOT_EXE or runtime.viewer_exe, or install the Godot build")
    return await asyncio.create_subprocess_exec(
        str(executable), "--path", str(PROJECT), scene, "--", f"--core-port={port}",
        env={k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}, creationflags=flags)


Launcher = Callable[[int], Awaitable[object]]


class Viewer:
    """Owns at most one viewer process. on_exit(returncode, requested) runs when it ends."""

    def __init__(self, port: int, launcher: Launcher | None = None, *, exe: str = "",
                 on_exit: Callable[[int | None, bool], Awaitable[None]] | None = None) -> None:
        self.port, self.exe = port, exe
        self.launcher = launcher or (lambda p: launch_godot(p, self.exe))
        self.on_exit = on_exit
        self.proc = None
        self._watch: asyncio.Task | None = None
        self._starting = False
        self._closing = False
        self.last_exit: int | None = None
        self.crashes = 0

    @property
    def running(self) -> bool:
        return self._starting or (self.proc is not None and getattr(self.proc, "returncode", 0) is None)

    @property
    def closing(self) -> bool:
        """A window is on its way out (hide asked, or the user closed it) but its process is still alive."""
        return self._closing and self.proc is not None and getattr(self.proc, "returncode", 0) is None

    def expect_exit(self) -> None:
        """The window said it is closing (desktop.close): a show now must wait for the process to be gone."""
        if self.proc is not None and getattr(self.proc, "returncode", 0) is None:
            self._closing = True

    async def settle(self, timeout_s: float = 5.0) -> None:
        """Wait for a closing window to end; terminate it if it lingers. Afterwards no old process is alive."""
        proc, watch = self.proc, self._watch
        if proc is None or getattr(proc, "returncode", 0) is not None:
            return
        if watch is not None:
            try:
                await asyncio.wait_for(asyncio.shield(watch), timeout_s)
                return
            except asyncio.TimeoutError:
                pass
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        if watch is not None:
            await asyncio.gather(watch, return_exceptions=True)

    async def open(self) -> bool:
        """True when a new window was started; False when one is already open or starting."""
        if self.running:
            return False
        self._starting = True
        try:
            self.proc = await self.launcher(self.port)
        finally:
            self._starting = False
        self._closing = False
        logs.info("runtime", "VIEWER_STARTED", "display window started", pid=getattr(self.proc, "pid", None))
        self._watch = asyncio.create_task(self._wait(self.proc))
        return True

    def adopt(self, proc) -> None:
        """Take over a window started before the Core (`jarvis` desktop mode opens the loading screen first)."""
        if self.running or getattr(proc, "returncode", 0) is not None:
            return
        self.proc, self._closing = proc, False
        self._watch = asyncio.create_task(self._wait(proc))

    async def _wait(self, proc) -> None:
        rc = await proc.wait()
        requested = self._closing
        self.last_exit = rc
        if self.proc is proc:
            self.proc = None
        if rc not in (0, None) and not requested:
            self.crashes += 1
            logs.warn("runtime", "VIEWER_CRASHED", "display window ended unexpectedly; the Core keeps running",
                      returncode=rc)
        else:
            logs.info("runtime", "VIEWER_EXITED", "display window closed", returncode=rc, requested=requested)
        if self.on_exit is not None:
            try:
                await self.on_exit(rc, requested)
            except Exception:
                logs.error("runtime", "VIEWER_EXIT_HANDLER_FAILED", "viewer exit handler failed", exc=True)

    async def close(self, grace_s: float = 3.0) -> bool:
        """Ask nicely (the face got `display.close` from the Core already), then terminate. True if one was open."""
        proc = self.proc
        if proc is None or getattr(proc, "returncode", 0) is not None:
            return False
        self._closing = True
        watch = self._watch
        try:
            await asyncio.wait_for(asyncio.shield(watch), grace_s) if watch else None
        except asyncio.TimeoutError:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            if watch is not None:
                await asyncio.gather(watch, return_exceptions=True)
        return True

    async def wait_exit(self) -> None:
        if self._watch is not None:
            await asyncio.gather(asyncio.shield(self._watch), return_exceptions=True)
