"""Shared real-browser helper: retry Page.goto when the host (Windows CI runner) runs out of socket buffers.

ERR_NO_BUFFER_SPACE / WinError 10053 are host-level transient failures (#874, #707, #695, #546): the page itself is fine.
Only those errors are retried (a real navigation error is raised at once), with a growing pause so buffers drain."""
from __future__ import annotations

import functools
import re
import time

TRANSIENT = re.compile(r"net::ERR_(NO_BUFFER_SPACE|INSUFFICIENT_RESOURCES|NETWORK_IO_SUSPENDED|CONNECTION_ABORTED)")
ATTEMPTS = 4
PAUSE_S = 2.0


def retrying(fn, attempts: int = ATTEMPTS, pause: float | None = None, sleep=time.sleep):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        for n in range(1, attempts + 1):
            try:
                return fn(*a, **kw)
            except Exception as e:
                if n == attempts or not TRANSIENT.search(str(e)):
                    raise
                print(f"browser_retry: attempt {n}/{attempts} transient goto error, retrying: {str(e)[:120]}", flush=True)
                sleep((PAUSE_S if pause is None else pause) * n)
    wrapper._retrying = True
    return wrapper


def install() -> bool:
    """Wrap Playwright's sync Page.goto once; False when Playwright is not installed."""
    try:
        from playwright.sync_api import Page
    except Exception:
        return False
    if not getattr(Page.goto, "_retrying", False):
        Page.goto = retrying(Page.goto)
    return True
