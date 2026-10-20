"""Always-on runtime of one Core: four separate states, the display window, tray, sleep/resume and audio recovery.

The four states (spec 4.1) never imply each other:
- display hidden   no Godot window; the Core, wake word and voice keep running.
- mic muted        software mute: the wake stream is closed, any conversation ends (physical mute, when built,
                   wins and is reported as such).
- alerts paused    proactive alerts are held (the alerts engine asks `alerts_paused()`); answers still speak.
- Core stopped     only Quit / `jarvis stop` / logout. Closing the window never stops it (feature background_core,
                   default on; off = the old "close the window = quit").
User intents (hidden / muted / paused) persist in <state_dir>/runtime.json together with the last live view, so the
dashboard and `jarvis status` can tell the truth even while the Core is down ("stopped", with its age).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from .. import __version__, logs
from . import autostart as autostart_mod
from .power import ResumeWatcher, reset_audio_backend
from .tray import Controller, desktop_session, library_available, status_line
from .viewer import Viewer, find_godot

STATE_FILE = "runtime.json"
PREFS = ("display_hidden", "alerts_paused", "mic_muted")
_current: "Runtime | None" = None


def read_state(home: Path) -> dict:
    try:
        d = json.loads((home / STATE_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def alerts_paused(home: Path | None = None) -> bool:
    """For the proactive-alerts engine (WP-M3): True while the user paused proactive alerts (tray / dashboard)."""
    if _current is not None and (home is None or home == _current.home):
        return _current.prefs["alerts_paused"]
    if home is None:
        return False
    return bool((read_state(home).get("prefs") or {}).get("alerts_paused"))


def current() -> "Runtime | None":
    return _current


class Runtime:
    def __init__(self, core, *, mode: str = "foreground", launcher=None, tray_factory=None,
                 autostart=None) -> None:
        self.core, self.home, self.mode = core, core.home, mode
        rc = core.cfg.runtime
        self.rc = rc
        saved = read_state(self.home).get("prefs") or {}
        self.prefs = {k: bool(saved.get(k, False)) for k in PREFS}
        self.viewer = Viewer(core.cfg.link.port, launcher, exe=rc.viewer_exe, on_exit=self._viewer_exited)
        self._tray_factory = tray_factory
        self.tray = None
        self.tray_state, self.tray_reason = "off", ""
        self.autostart = autostart if autostart is not None else autostart_mod.adapter()
        self.started = time.time()
        self.state = "running"
        self.resumes = 0
        self.recovering = False           # True while audio is being re-opened: the wake loop stays closed
        self._display_crashed = False
        self._custom_launcher = launcher is not None
        self._godot: bool | None = True if launcher is not None else None   # a test launcher needs no Godot
        self.loop: asyncio.AbstractEventLoop | None = None
        self.watcher = ResumeWatcher(self._on_resume, gap_s=rc.resume_gap_s)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        global _current
        _current = self
        self.loop = asyncio.get_running_loop()
        core = self.core
        if self.prefs["mic_muted"]:
            core.dev.set_mic_muted(True)              # the user's software mute survives a restart (privacy)
            logs.info("runtime", "MIC_MUTE_RESTORED", "microphone stays muted (muted before the restart)")
        core._bg.append(asyncio.create_task(self.watcher.run()))
        core._bg.append(asyncio.create_task(self._refresh_loop()))
        if self.mode in ("background", "desktop") and core.features.enabled("tray_icon"):
            self._start_tray()
        self.save()
        logs.info("runtime", "RUNTIME_STARTED", f"runtime mode {self.mode}", mode=self.mode,
                  keep_running=self.keep_running, tray=self.tray_state, alerts_paused=self.prefs["alerts_paused"])

    async def stop(self) -> None:
        global _current
        self.state = "stopping"
        if self.core.link.face is not None:
            await self.core.link.send_face("display.close", {"reason": "core_stopping"})
        await self.viewer.close(grace_s=2.0)
        if self.tray is not None:
            await asyncio.to_thread(self.tray.stop)
            self.tray = None
        self.state = "stopped"
        self.save()
        if _current is self:
            _current = None

    def _start_tray(self) -> None:
        ok, why = (True, "") if self._tray_factory is not None else desktop_session()   # an injected backend needs no desktop
        if ok and self._tray_factory is None:
            ok, why = library_available()
        if not ok:
            self.tray_state, self.tray_reason = "unavailable", why
            logs.info("runtime", "TRAY_UNAVAILABLE", why)
            return
        try:
            factory = self._tray_factory
            if factory is None:
                from .tray import PystrayTray as factory
            self.tray = factory(Controller(self.view, self.act_threadsafe))
            self.tray.start()
            self.tray_state, self.tray_reason = "shown", ""
        except Exception as e:
            self.tray, self.tray_state, self.tray_reason = None, "unavailable", f"{type(e).__name__}: {str(e)[:100]}"
            logs.warn("runtime", "TRAY_UNAVAILABLE", f"tray icon could not start: {self.tray_reason}")

    async def _refresh_loop(self) -> None:
        last = None
        while True:
            await asyncio.sleep(1.0)
            try:
                v = self.view()
                key = json.dumps(v, sort_keys=True, default=str)
                if key != last:
                    last = key
                    self.save(v)
                    if self.tray is not None:
                        await asyncio.to_thread(self.tray.refresh)
            except Exception:
                logs.debug("runtime", "RUNTIME_REFRESH_FAILED", "runtime refresh failed")

    @property
    def keep_running(self) -> bool:
        return self.core.features.enabled("background_core")

    # ------------------------------------------------------------------ the four states
    def display_state(self) -> str:
        if self.core.link.face is not None:
            return "shown"
        if self.viewer.running:
            return "starting"
        if self._display_crashed:
            return "crashed"
        return "hidden"

    def view(self) -> dict:
        core = self.core
        dev = getattr(core, "dev", None)
        if self._godot is None:
            self._godot = find_godot(self.rc.viewer_exe) is not None    # checked once (and again on show)
        godot = self._godot
        snap_core = getattr(getattr(core, "health", None), "core", "unknown")
        live = core.live is not None
        return {
            "core": {"state": self.state, "pid": os.getpid(), "mode": self.mode, "since": self.started,
                     "version": __version__, "platform": sys.platform},
            "display": {"state": self.display_state(), "wanted": not self.prefs["display_hidden"],
                        "available": godot, "reason": "" if godot else "Godot viewer not found (GODOT_EXE)",
                        "owned": self.viewer.running, "crashes": self.viewer.crashes,
                        "close_policy": "hide" if self.keep_running else "quit"},
            "mic": {"muted": bool(getattr(dev, "mic_muted", False)), "source": "software", "physical_mute": None},
            "alerts": {"paused": self.prefs["alerts_paused"]},
            "voice": {"mode": core.voice_mode, "wake_listening": bool(getattr(core, "_wake_mic_active", False)),
                      "live_session": live, "connecting": bool(core._voice_connecting),
                      # idle = no cloud voice connection: the wake word runs on this device only
                      "cloud_connection_open": live or bool(core._voice_connecting),
                      "detail": "wake word off" if core.wake is None else ""},
            "tray": {"state": self.tray_state, "reason": self.tray_reason},
            "dashboard": {"enabled": core.features.enabled("dashboard"),
                          "running": getattr(core, "_dashboard", None) is not None},
            "health": snap_core, "resumes": self.resumes, "ts": time.time(),
        }

    def save(self, view: dict | None = None) -> None:
        data = {"prefs": dict(self.prefs), "live": view or self.view()}
        path = self.home / STATE_FILE
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            logs.debug("runtime", "RUNTIME_SAVE_FAILED", "could not write runtime.json")

    def _set_pref(self, key: str, on: bool) -> None:
        if self.prefs[key] != on:
            self.prefs[key] = on
            self.save()

    def note_muted(self, muted: bool) -> None:
        """Core.set_muted calls this (any route: face, tray, dashboard, CLI) so the mute persists."""
        self._set_pref("mic_muted", bool(muted))

    async def show(self, by: str = "user") -> tuple[bool, str]:
        self._set_pref("display_hidden", False)
        self._display_crashed = False
        if self.core.link.face is not None:
            return True, "already visible"
        if self.viewer.running:
            return True, "already opening"
        if not self._custom_launcher:
            self._godot = None                # re-check: Godot may have been installed meanwhile
        try:
            await self.viewer.open()
        except Exception as e:
            logs.warn("runtime", "VIEWER_START_FAILED", f"display window could not start: {e}", by=by)
            return False, str(e)[:160]
        logs.info("runtime", "DISPLAY_SHOWN", "display window opened", by=by)
        return True, "opening"

    async def hide(self, by: str = "user") -> tuple[bool, str]:
        self._set_pref("display_hidden", True)
        self._display_crashed = False
        had = self.core.link.face is not None or self.viewer.running
        if self.core.link.face is not None:
            await self.core.link.send_face("display.close", {"reason": "hide"})
        await self.viewer.close()
        logs.info("runtime", "DISPLAY_HIDDEN", "display window closed; the Core keeps running", by=by)
        return True, "hidden" if had else "already hidden"

    def window_closed(self) -> bool:
        """The user closed the window (desktop.close). True = keep the Core running (policy hide)."""
        if not self.keep_running:
            return False
        self._set_pref("display_hidden", True)
        logs.info("runtime", "DISPLAY_CLOSED_BY_USER", "window closed; JARVES keeps running in the background "
                  "(tray / \"Hey Jarvis\" / jarvis show)")
        return True

    async def _viewer_exited(self, rc, requested: bool) -> None:
        if rc not in (0, None) and not requested:
            self._display_crashed = True
            self.core.health.set("display", "degraded", f"display window crashed (exit {rc}); the Core keeps running",
                                 "say \"show yourself\" or use the tray to open it again")
        else:
            self.core.health.set("display", "disabled" if self.prefs["display_hidden"] else "ok",
                                 "display hidden" if self.prefs["display_hidden"] else "display closed")

    async def set_alerts_paused(self, paused: bool, by: str = "user") -> None:
        self._set_pref("alerts_paused", bool(paused))
        logs.info("runtime", "ALERTS_PAUSED" if paused else "ALERTS_RESUMED",
                  f"proactive alerts {'paused' if paused else 'resumed'}", by=by)

    async def apply(self, change: dict, by: str) -> dict:
        """One entry point for tray / dashboard / CLI changes: {display: show|hide, mic_muted, alerts_paused}."""
        out = {}
        if change.get("display") in ("show", "hide"):
            ok, detail = await (self.show(by) if change["display"] == "show" else self.hide(by))
            out["display"] = {"ok": ok, "detail": detail}
        if isinstance(change.get("mic_muted"), bool):
            await self.core.set_muted(change["mic_muted"])
            out["mic_muted"] = change["mic_muted"]
        if isinstance(change.get("alerts_paused"), bool):
            await self.set_alerts_paused(change["alerts_paused"], by)
            out["alerts_paused"] = change["alerts_paused"]
        return {"applied": out, "runtime": self.view()}

    # ------------------------------------------------------------------ tray actions (called from the tray thread)
    def act_threadsafe(self, action: str) -> None:
        if self.loop is None or self.loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.act(action), self.loop)

    async def act(self, action: str) -> None:
        try:
            v = self.view()
            if action == "toggle_display":
                await (self.hide("tray") if v["display"]["state"] in ("shown", "starting") else self.show("tray"))
            elif action == "toggle_mute":
                await self.core.set_muted(not v["mic"]["muted"])
            elif action == "toggle_alerts":
                await self.set_alerts_paused(not v["alerts"]["paused"], "tray")
            elif action == "dashboard":
                await self.open_dashboard()
            elif action == "status":
                self._notify(status_line(self.view()))
            elif action == "diagnostics":
                path = await asyncio.to_thread(self._write_diag)
                self._notify(f"Support bundle saved: {path}")
                _reveal(path)
            elif action == "quit":
                logs.info("runtime", "QUIT_REQUESTED", "Quit chosen in the tray")
                self.core.stop_event.set()
            if self.tray is not None:
                await asyncio.to_thread(self.tray.refresh)
        except Exception:
            logs.error("runtime", "TRAY_ACTION_FAILED", f"tray action {action} failed", exc=True)

    def _notify(self, text: str) -> None:
        if self.tray is not None:
            self.tray.notify(text)

    async def open_dashboard(self) -> str | None:
        if not self.core.features.enabled("dashboard"):
            self._notify("The dashboard is switched off (jarvis features on dashboard)")
            return None
        return await self.core.open_dashboard()      # the Core's in-process dashboard (WP-H), opened in the browser

    def _write_diag(self) -> str:
        from types import SimpleNamespace
        from ..cli import cmd_diag
        out = self.home / f"jarves-diag-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        cmd_diag(SimpleNamespace(out=str(out), lines=500), self.core.cfg, self.home)
        return str(out)

    # ------------------------------------------------------------------ sleep / resume and audio recovery
    async def _on_resume(self, slept_s: float) -> None:
        self.resumes += 1
        core = self.core
        if core.live is not None:            # its connection died while the computer slept: end it (no billing)
            live, core.live = core.live, None
            try:
                await asyncio.wait_for(live.stop(), 5)
            except Exception:
                logs.warn("runtime", "RESUME_LIVE_STOP_FAILED", "stale conversation did not stop cleanly")
        await self.recover_audio("resume")
        core.features.invalidate()
        await core.link.broadcast("status", core.snapshot())

    async def recover_audio(self, why: str) -> bool:
        """Close the wake stream, re-scan devices, re-check audio. Never runs twice at once."""
        if self.recovering:
            return False
        self.recovering = True
        try:
            deadline = time.monotonic() + 3
            while getattr(self.core, "_wake_mic_active", False) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if self.core.use_real_audio and self.core.live is None:
                await asyncio.to_thread(reset_audio_backend)
            ok = self.core.dev.check_audio()
            logs.info("runtime", "AUDIO_RECOVERED" if ok else "AUDIO_STILL_UNAVAILABLE",
                      "audio re-opened" if ok else "audio still unavailable; retrying automatically", why=why)
            return ok
        finally:
            self.recovering = False


def _reveal(path: str) -> None:
    try:
        if sys.platform == "win32":
            import subprocess
            subprocess.Popen(["explorer", "/select,", path])
    except OSError:
        pass
