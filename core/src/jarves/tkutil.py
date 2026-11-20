"""Tk helpers shared by the approval popup and the hidden-mode listening dot (#520)."""
from __future__ import annotations

import time

TRANSIENT = ("couldn't read file", "tk.tcl", "init.tcl", "auto.tcl", "tcl_findLibrary", "tk_getFileLibrary",
             "can't find a usable")


def new_root(tk, *, attempts: int = 5, sleep=None):
    """tk.Tk(), retried on Windows' transient Tcl init failures ("couldn't read file ...auto.tcl", 'invalid command
    name "tcl_findLibrary"'); any other TclError is raised at once."""
    for attempt in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as e:
            if attempt == attempts - 1 or not any(k in str(e) for k in TRANSIENT):
                raise
            (sleep or time.sleep)(0.25 * (attempt + 1))     # looked up at call time (tests patch time.sleep)
