"""Clean-stop plumbing (Issue #17: "JARVES stopped without a clean shutdown 5x" on the owner's PC).

The diagnosis calls a Core process a crash when its log has CORE_STARTING but no CORE_STOPPED. Before this module the
Core stopped cleanly only through `jarvis stop`, the tray Quit and a voice "quit"; everything else the OS can do to a
console program ended the process with no stop at all:

- closing the terminal window (CTRL_CLOSE_EVENT), logging off (CTRL_LOGOFF_EVENT), Windows shutting down
  (CTRL_SHUTDOWN_EVENT): Windows terminates the process a few seconds after those events unless a handler returns;
- `taskkill` without /F or a service manager sending SIGTERM / SIGHUP (macOS, Linux).

`install()` turns each of these into the same orderly shutdown (`Core.request_stop(reason)` -> stop_event -> Core.stop(),
which logs CORE_STOPPED), and for the three "the OS is ending us" events it holds the OS back (up to `wait_s`) until the
Core says it has stopped. It is best-effort and never raises: a platform without these hooks keeps the old behaviour.

`boot_time()` is the second half: CORE_STARTING records the machine's boot time, so a process whose successor started
after a REBOOT is "the computer was shut down / lost power", which JARVES cannot prove was its own crash.
"""
from __future__ import annotations

import signal
import sys
import threading
import time
from typing import Callable

from .. import logs

# Windows console control events (wincon.h)
CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT = 0, 1, 2, 5, 6
EVENT_NAMES = {CTRL_C_EVENT: "ctrl_c", CTRL_BREAK_EVENT: "ctrl_break", CTRL_CLOSE_EVENT: "console_closed",
               CTRL_LOGOFF_EVENT: "windows_logoff", CTRL_SHUTDOWN_EVENT: "windows_shutdown"}
OS_ENDING = (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT)   # Windows kills us when the handler returns
OS_WAIT_S = 4.5                      # Windows allows about 5 s for a console close; stay inside it


def make_console_handler(request_stop: Callable[[str], None], stopped: threading.Event, *, wait_s: float = OS_WAIT_S,
                         call_soon: Callable[[Callable[[], None]], None] | None = None) -> Callable[[int], bool]:
    """The Windows console-control callback (runs on an OS thread). True = handled. `call_soon` hands the request to
    the event loop's thread (loop.call_soon_threadsafe)."""
    seen: set[int] = set()

    def handler(event: int) -> bool:
        name = EVENT_NAMES.get(int(event))
        if name is None:
            return False                                         # not ours: the next handler decides
        if event in (CTRL_C_EVENT, CTRL_BREAK_EVENT):
            if event in seen:
                return False                                     # a second Ctrl+C is the "I mean it": default (abort)
            seen.add(event)
        try:
            (call_soon or (lambda fn: fn()))(lambda: request_stop(name))
        except Exception:                                        # the loop is gone: nothing to stop any more
            return event in OS_ENDING
        if event in OS_ENDING:
            stopped.wait(wait_s)                                 # holds Windows back until Core.stop() finished
        return True
    return handler


def install(loop, request_stop: Callable[[str], None], stopped: threading.Event, *, platform: str | None = None,
            wait_s: float = OS_WAIT_S, kernel32=None) -> Callable[[], None]:
    """Route OS stop requests to `request_stop(reason)`. Returns an `uninstall()`. Best-effort, never raises."""
    plat = platform or sys.platform
    undo: list[Callable[[], None]] = []
    try:
        if plat == "win32":
            import ctypes
            k32 = kernel32 or ctypes.windll.kernel32
            handler = make_console_handler(request_stop, stopped, wait_s=wait_s, call_soon=loop.call_soon_threadsafe)
            proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint) if kernel32 is None else None
            cb = proto(lambda ev: 1 if handler(ev) else 0) if proto is not None else handler
            if k32.SetConsoleCtrlHandler(cb, True):
                undo.append(lambda: k32.SetConsoleCtrlHandler(cb, False))     # `cb` stays referenced until then
            else:
                logs.warn("runtime", "STOP_HANDLER_UNAVAILABLE", "could not register the console stop handler")
        else:
            def first_ctrl_c() -> None:
                request_stop("ctrl_c")
                loop.remove_signal_handler(signal.SIGINT)        # a second Ctrl+C aborts the way it always did

            for sig, name in ((signal.SIGTERM, "sigterm"), (signal.SIGHUP, "sighup")):
                loop.add_signal_handler(sig, request_stop, name)
                undo.append(lambda s=sig: loop.remove_signal_handler(s))
            loop.add_signal_handler(signal.SIGINT, first_ctrl_c)
            undo.append(lambda: loop.remove_signal_handler(signal.SIGINT))
    except (NotImplementedError, RuntimeError, OSError, AttributeError, ValueError) as e:
        logs.warn("runtime", "STOP_HANDLER_UNAVAILABLE", "OS stop requests are not routed to a clean shutdown",
                  error_type=type(e).__name__)

    def uninstall() -> None:
        for fn in undo:
            try:
                fn()
            except Exception:
                pass
        undo.clear()
    return uninstall


def boot_time(clock: Callable[[], float] = time.time) -> int | None:
    """The machine's boot time as a whole epoch second (None when it cannot be read). Two readings of the same boot
    differ by clock jitter of a second or two, so compare them with `same_boot()`, not ==."""
    try:
        if sys.platform == "win32":
            import ctypes
            up_s = ctypes.windll.kernel32.GetTickCount64() / 1000.0
        elif sys.platform.startswith("linux"):
            with open("/proc/uptime", encoding="ascii") as f:
                up_s = float(f.read().split()[0])
        else:
            return None
        return int(clock() - up_s)
    except (OSError, ValueError, AttributeError, IndexError):
        return None


def same_boot(a: int | float | None, b: int | float | None, tolerance_s: float = 120.0) -> bool | None:
    """True/False when both boot times are known, None when either is not (then nothing can be concluded)."""
    if a is None or b is None:
        return None
    return abs(float(a) - float(b)) <= tolerance_s


PLANNED_RESTART_FILE = "planned_restart.json"
PLANNED_RESTART_TTL_S = 600.0


def mark_planned_restart(home, reason: str, clock: Callable[[], float] = time.time) -> None:
    """A planned stop-and-restart (hotfix, V4.5.1 #620) writes this just before it stops the Core. The next start reads
    it so a stop it ended on purpose is not mistaken for a crash. Best-effort, never raises."""
    import json
    try:
        from pathlib import Path
        (Path(home) / PLANNED_RESTART_FILE).write_text(
            json.dumps({"ts": clock(), "reason": str(reason)[:40]}), encoding="utf-8")
    except OSError:
        pass


def consume_planned_restart(home, clock: Callable[[], float] = time.time) -> str | None:
    """The reason when a fresh planned-restart marker exists (it is deleted: one marker explains one restart), else None.
    An old or unreadable marker is deleted and ignored."""
    import json
    from pathlib import Path
    f = Path(home) / PLANNED_RESTART_FILE
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        fresh = 0 <= clock() - float(d["ts"]) <= PLANNED_RESTART_TTL_S
        reason = str(d.get("reason") or "planned")
    except (OSError, ValueError, KeyError, TypeError):
        fresh, reason = False, ""
    try:
        f.unlink()
    except OSError:
        pass
    return reason if fresh else None
