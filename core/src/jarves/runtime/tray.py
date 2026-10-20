"""Notification-area (system tray) icon for the window-less Core.

Library: pystray 0.19.5 (checked 2026-10-19 on pypi.org/project/pystray; LGPLv3, used unmodified; last release
2023-09 but stable and the de-facto standard), pure Python on ctypes Win32 / AppIndicator / GTK / Xorg, only Pillow besides). Alternatives considered:
PySide6 6.12 QSystemTrayIcon (maintained, but ~250 MB of Qt for one icon and a second GUI event loop; too heavy for
the installer and the Pi), infi.systray 0.1.12 (Windows only), tray-manager / trayapp (thin wrappers around pystray).
pystray sits behind `TrayBackend`, so replacing it touches this file only.

Threading: pystray runs its own message loop in a daemon thread. Menu callbacks never touch Core state directly:
`Controller.act()` hands the action to the Core's asyncio loop (run_coroutine_threadsafe).
Linux/Pi: needs a desktop session (DISPLAY / WAYLAND_DISPLAY); headless the dashboard is the equivalent control.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Callable

from .. import logs

# Menu (spec 4.1): Dashboard; Show/Hide; Software Mic Mute; Pause Proactive Alerts; Status; Diagnostics; Quit.
ACTIONS = ("dashboard", "toggle_display", "toggle_mute", "toggle_alerts", "status", "diagnostics", "quit")


@dataclass
class MenuEntry:
    id: str
    text: str
    checked: bool | None = None       # None = not a check item
    enabled: bool = True
    default: bool = False             # double-click / left-click action


def menu_entries(view: dict) -> list[MenuEntry]:
    """The tray menu for a runtime view (pure: tested without any tray library)."""
    shown = view.get("display", {}).get("state") in ("shown", "starting")
    can_show = view.get("display", {}).get("available", True)
    return [
        MenuEntry("dashboard", "Dashboard", enabled=view.get("dashboard", {}).get("enabled", True), default=True),
        MenuEntry("toggle_display", "Hide JARVES" if shown else "Show JARVES", enabled=shown or can_show),
        MenuEntry("toggle_mute", "Mute microphone (software)", checked=bool(view.get("mic", {}).get("muted"))),
        MenuEntry("toggle_alerts", "Pause proactive alerts", checked=bool(view.get("alerts", {}).get("paused"))),
        MenuEntry("status", "Status: " + status_line(view)),
        MenuEntry("diagnostics", "Diagnostics (save support bundle)"),
        MenuEntry("quit", "Quit JARVES"),
    ]


def status_line(view: dict) -> str:
    core = view.get("core", {}).get("state", "unknown")
    if core != "running":
        return f"Core {core}"
    parts = []
    voice = view.get("voice", {})
    if view.get("mic", {}).get("muted"):
        parts.append("mic muted")
    elif voice.get("live_session"):
        parts.append("in conversation")
    elif voice.get("wake_listening"):
        parts.append("listening for \"Hey Jarvis\"")
    else:
        parts.append(voice.get("detail") or "voice idle")
    parts.append("window " + view.get("display", {}).get("state", "hidden"))
    if view.get("alerts", {}).get("paused"):
        parts.append("alerts paused")
    health = view.get("health")
    if health and health != "ready":
        parts.append(f"health {health}")
    return ", ".join(parts)


def tooltip(view: dict) -> str:
    from ..profiles import DEFAULT, ProfileError, current_name
    try:
        p = current_name()
    except ProfileError:
        p = DEFAULT
    name = "JARVES" if p == DEFAULT else f"JARVES [{p}]"      # two profiles = two tray icons: say which is which
    return (name + " - " + status_line(view))[:120]       # Windows limits the tip to 127 characters


def desktop_session() -> tuple[bool, str]:
    if sys.platform == "win32":
        return True, ""
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False, "no desktop session (headless): use the dashboard instead"
    return True, ""


def library_available() -> tuple[bool, str]:
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401
        return True, ""
    except Exception as e:     # ImportError, or a backend that fails to load (no X server...)
        return False, f"tray library not installed ({type(e).__name__}): uv sync --extra tray"


def icon_image(view: dict, size: int = 64):
    """A calm ring: cyan = listening, dim = window hidden, red bar = mic muted, amber dot = alerts paused."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    running = view.get("core", {}).get("state") == "running"
    ring = (64, 210, 230, 255) if running else (120, 120, 120, 255)
    d.ellipse((4 * s, 4 * s, 60 * s, 60 * s), fill=(18, 24, 32, 255), outline=ring, width=max(2, int(6 * s)))
    d.ellipse((24 * s, 24 * s, 40 * s, 40 * s), fill=ring)
    if view.get("mic", {}).get("muted"):
        d.line((12 * s, 52 * s, 52 * s, 12 * s), fill=(235, 70, 70, 255), width=max(2, int(7 * s)))
    if view.get("alerts", {}).get("paused"):
        d.ellipse((44 * s, 2 * s, 62 * s, 20 * s), fill=(245, 175, 40, 255))
    return img


class Controller:
    """What the tray needs from the Runtime: a thread-safe view and fire-and-forget actions."""

    def __init__(self, view: Callable[[], dict], act: Callable[[str], None]) -> None:
        self.view, self.act = view, act


class TrayBackend:
    """Interface: start(), refresh(), notify(text), stop()."""

    def start(self) -> None: ...
    def refresh(self) -> None: ...
    def notify(self, text: str) -> None: ...
    def stop(self) -> None: ...


class PystrayTray(TrayBackend):
    def __init__(self, ctl: Controller) -> None:
        import pystray
        self._pystray = pystray
        self.ctl = ctl
        self._last = None
        self.icon = pystray.Icon("jarves", icon_image(ctl.view()), tooltip(ctl.view()), menu=self._menu())
        self._thread: threading.Thread | None = None

    def _menu(self):
        ps = self._pystray

        def item(i: int):
            def text(_):
                return menu_entries(self.ctl.view())[i].text

            def checked(_):
                return bool(menu_entries(self.ctl.view())[i].checked)

            def enabled(_):
                return menu_entries(self.ctl.view())[i].enabled
            e = menu_entries(self.ctl.view())[i]
            return ps.MenuItem(text, lambda *_: self.ctl.act(e.id), checked=checked if e.checked is not None else None,
                               enabled=enabled, default=e.default)
        n = len(ACTIONS)
        items = [item(i) for i in range(n)]
        return ps.Menu(*items[:4], ps.Menu.SEPARATOR, *items[4:6], ps.Menu.SEPARATOR, items[6])

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="jarves-tray", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.icon.run()
        except Exception:
            logs.error("runtime", "TRAY_FAILED", "tray icon stopped", exc=True)

    def refresh(self) -> None:
        v = self.ctl.view()
        key = (v.get("core", {}).get("state"), v.get("mic", {}).get("muted"), v.get("alerts", {}).get("paused"),
               v.get("display", {}).get("state"), status_line(v))
        if key == self._last:
            return
        self._last = key
        try:
            self.icon.icon = icon_image(v)
            self.icon.title = tooltip(v)
            self.icon.update_menu()
        except Exception:
            logs.debug("runtime", "TRAY_REFRESH_FAILED", "tray refresh failed")

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text[:250], "JARVES")
        except Exception:
            logs.debug("runtime", "TRAY_NOTIFY_FAILED", "tray notification failed")

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=3)
