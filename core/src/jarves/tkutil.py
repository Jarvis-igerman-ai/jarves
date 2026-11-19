"""Neutral Tk helpers shared by the approval popup and the listening dot (#520)."""
from __future__ import annotations

import time

TCL_TRANSIENT = ("couldn't read file", "tk.tcl", "init.tcl", "auto.tcl", "tcl_findLibrary", "tk_getFileLibrary",
                 "can't find a usable")


def new_tk_root(tk, attempts: int = 5, sleep=None):
    """tk.Tk() with a few retries. Windows: Tcl's init scripts are read now and then with a failure ("couldn't read
    file ...auto.tcl", 'invalid command name "tcl_findLibrary"'). attempts below 1 counts as 1 (never returns None)."""
    attempts, sleep = max(1, int(attempts)), sleep or time.sleep    # resolved per call, so tests can patch time.sleep
    for attempt in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as e:
            if attempt == attempts - 1 or not any(k in str(e) for k in TCL_TRANSIENT):
                raise
            sleep(0.25 * (attempt + 1))
    raise AssertionError("unreachable")
