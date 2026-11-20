"""Tk helpers shared by the approval popup and the hidden-mode listening dot (#520)."""
from __future__ import annotations

import time

# Windows reads Tcl's init scripts now and then with a failure ("couldn't read file ...auto.tcl",
# 'invalid command name "tcl_findLibrary"'); a short retry succeeds.
TRANSIENT = ("couldn't read file", "tk.tcl", "init.tcl", "auto.tcl", "tcl_findLibrary", "tk_getFileLibrary",
             "can't find a usable")


def new_root(tk, *, attempts: int = 5, sleep=None):
    """tk.Tk(), retried (bounded) on a transient Tcl init failure; any other TclError is raised at once."""
    for attempt in range(attempts):
        try:
            return tk.Tk()
        except tk.TclError as e:
            if attempt == attempts - 1 or not any(k in str(e) for k in TRANSIENT):
                raise
            (sleep or time.sleep)(0.25 * (attempt + 1))     # looked up at call time (tests patch time.sleep)
