"""Shared Windows no-console-window flag for workflow subprocess launches (issue #889)."""
from __future__ import annotations

import subprocess
import sys


def hidden_popen_kwargs(detached: bool = False) -> dict:
    """Keyword args that stop a child process opening a visible console window on Windows; empty elsewhere."""
    if sys.platform != "win32":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if detached:
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    return {"creationflags": flags}
