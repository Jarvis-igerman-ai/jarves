"""Listening indicator for hidden mode (window closed, tray only): ONE state vocabulary for every channel.

States: waiting (wake word only, nothing leaves the device) / listening (a live conversation is open) / thinking /
speaking / muted / error. Channels that work with the window hidden: the tray icon (colour + shape + tooltip in the UI
language, tray.py) and an optional tiny always-on-top click-through "listening dot" near the screen corner that exists
only while a conversation is open (ListeningDot below). Everything here is pure or injectable: no tray library and no
display are needed to test it.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

from .. import logs

STATES = ("waiting", "listening", "thinking", "speaking", "muted", "error")

# state -> (colour RGB, shape, English text, Hebrew text). Colour AND shape differ, so colour-blind users can tell.
STYLE: dict[str, tuple[tuple[int, int, int], str, str, str]] = {
    "waiting":   ((90, 140, 200), "ring",    "waiting for \"Hey Jarvis\"", "ממתין ל\"היי ג'ארוויס\""),
    "listening": ((70, 210, 120), "disc",    "listening (conversation open)", "מקשיב (שיחה פתוחה)"),
    "thinking":  ((245, 175, 40), "dots",    "thinking", "חושב"),
    "speaking":  ((64, 210, 230), "wave",    "speaking", "מדבר"),
    "muted":     ((235, 70, 70),  "slash",   "microphone muted", "המיקרופון מושתק"),
    "error":     ((235, 70, 70),  "cross",   "problem: open the dashboard", "תקלה: פתחו את לוח הבקרה"),
}
OPEN_STATES = ("listening", "thinking", "speaking")      # a conversation is open: the dot is shown


def state_of(view: dict) -> str:
    """The one state for a runtime view (runtime/service.py view())."""
    if view.get("core", {}).get("state", "running") != "running" or view.get("voice", {}).get("error"):
        return "error"
    if view.get("mic", {}).get("muted"):
        return "muted"
    voice = view.get("voice", {})
    if voice.get("live_session"):
        if voice.get("speaking"):
            return "speaking"
        if voice.get("thinking"):
            return "thinking"
        return "listening"
    if voice.get("connecting"):
        return "listening"
    return "waiting"


def text(state: str, locale: str = "en") -> str:
    st = STYLE.get(state, STYLE["error"])
    return st[3] if locale == "he" else st[2]


def locale_of(view: dict) -> str:
    return "he" if str(view.get("locale", "en")).startswith("he") else "en"


class ListeningDot:
    """The optional corner dot. `backend` is injected (tests use a fake); the real one is TkDotBackend.
    Shown only while a conversation is open AND the window is hidden; hidden again at once otherwise."""

    def __init__(self, backend, enabled: Callable[[], bool] = lambda: True) -> None:
        self.backend, self.enabled = backend, enabled
        self.shown, self.state = False, ""

    def update(self, view: dict) -> None:
        state = state_of(view)
        window = view.get("display", {}).get("state") in ("shown", "starting")
        want = state in OPEN_STATES and not window and bool(self.enabled())
        try:
            if want and (not self.shown or state != self.state):
                self.backend.show(state, STYLE[state][0])
            elif not want and self.shown:
                self.backend.hide()
        except Exception:
            logs.debug("runtime", "DOT_FAILED", "listening dot could not be updated")
            want = False
        self.shown, self.state = want, state

    def stop(self) -> None:
        try:
            self.backend.close()
        except Exception:
            pass


class TkDotBackend:
    """A 14 px dot at the bottom-right corner on its own thread: no title bar, always on top, click-through and
    no-activate on Windows (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW), so it never steals focus or
    clicks. Silently does nothing when Tk or a display is missing (headless, Pi without desktop)."""

    SIZE = 14

    def __init__(self) -> None:
        self._cmd: list[tuple[str, object]] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = False

    def _ensure(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="listening-dot", daemon=True)
            self._thread.start()

    def show(self, state: str, rgb: tuple[int, int, int]) -> None:
        with self._lock:
            self._cmd.append(("show", rgb))
        self._ensure()

    def hide(self) -> None:
        with self._lock:
            self._cmd.append(("hide", None))

    def close(self) -> None:
        self._stop = True

    def _run(self) -> None:
        try:
            import tkinter as tk
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.85)
            s = self.SIZE
            root.geometry(f"{s}x{s}+{root.winfo_screenwidth() - s - 12}+{root.winfo_screenheight() - s - 52}")
            cv = tk.Canvas(root, width=s, height=s, highlightthickness=0, bd=0, bg="#010101")
            cv.pack()
            try:
                root.attributes("-transparentcolor", "#010101")
            except Exception:
                pass
            dot = cv.create_oval(1, 1, s - 1, s - 1, fill="#46d278", outline="")
            root.withdraw()
            if sys.platform == "win32":
                self._click_through(root)

            def pump() -> None:
                with self._lock:
                    cmds, self._cmd = self._cmd, []
                for kind, arg in cmds:
                    if kind == "show":
                        r, g, b = arg
                        cv.itemconfig(dot, fill=f"#{r:02x}{g:02x}{b:02x}")
                        root.deiconify()
                        root.attributes("-topmost", True)
                    else:
                        root.withdraw()
                if self._stop:
                    root.destroy()
                else:
                    root.after(150, pump)
            root.after(150, pump)
            root.mainloop()
        except Exception:
            logs.debug("runtime", "DOT_UNAVAILABLE", "listening dot unavailable (no display or Tk)")

    @staticmethod
    def _click_through(root) -> None:
        try:
            import ctypes
            root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
            GWL_EXSTYLE, flags = -20, 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080   # LAYERED|TRANSPARENT|NOACTIVATE|TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | flags)
        except Exception:
            logs.debug("runtime", "DOT_CLICKTHROUGH_FAILED", "could not make the dot click-through")
