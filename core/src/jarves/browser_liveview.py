"""Browser Studio B2 (V7, #397): a READ-ONLY live view of the pilot's page for the owner, on his own computer only.

Frames are PNGs polled by the dashboard (no push channel, no WebSocket). The server enforces everything, the page enforces
nothing: (1) loopback owner only (a paired phone or any other principal is refused); (2) a per-view token, 256 random bits,
kept only as a hash, shown once to the dashboard that started the view, sent in a POST body (never a URL, so it is in no log),
fixed expiry (5 minutes), one live view at a time (a new start revokes the old token); (3) TEST SITE ONLY (top-level page only: a local test page that embeds public content would show it; use test pages that do not): PilotSession.view_frame
refuses unless the current page is a loopback page, checked on every frame; (4) no input path exists: the only routes are
start / frame / stop, and none carries a key, a click or a URL; (5) a Stop button that stops the view AND the pilot session
(driver closed). A frame is never given to a model and never stored. Beta, OFF by default (switch `browser_liveview`, needs
`browser_pilot`)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from typing import Callable

from . import features as feat
from . import logs
from .browser_pilot import PilotError, PilotSession
from .calm import targets

FEATURE = "browser_liveview"
TTL_S = 300.0
MIN_INTERVAL_S = 0.4
MAX_FRAME_BYTES = 3_000_000


class LiveError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _h(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


class LiveView:
    def __init__(self, session: PilotSession, *, enabled: Callable[[], bool], clock: Callable[[], float] = time.monotonic,
                 ttl_s: float = TTL_S) -> None:
        self.session, self.enabled, self.clock, self.ttl_s = session, enabled, clock, ttl_s
        self._hash = ""
        self._expires = 0.0
        self._last_at = -1e9
        self._last: bytes = b""
        self.seq = 0
        self._lock = threading.RLock()        # the dashboard is threaded: start / frame / stop never overlap (#649 review).
        # NOTE for the driver PR: the page driver must be called from its own thread (Playwright sync is thread-bound).

    def start(self) -> dict:
        with self._lock:
            return self._start()

    def _start(self) -> dict:
        if not self.enabled():
            raise LiveError("LIVEVIEW_OFF", "the live view is off (jarvis features on browser_liveview)", 403)
        try:
            self.session.view_frame()                       # fail early: switch, stopped, time cap, local test page only
        except PilotError as e:
            raise LiveError(e.code, e.message, 409) from None
        token = secrets.token_urlsafe(32)
        self._hash, self._expires, self._last, self._last_at, self.seq = _h(token), self.clock() + self.ttl_s, b"", -1e9, 0
        self.session._receipt("view:start", "ok")
        logs.info("browser", "BROWSER_LIVEVIEW", "live view started")
        return {"grant": token, "expires_in_s": int(self.ttl_s), "interval_ms": 1000}

    def _check(self, token: object) -> None:
        if not self._hash or not isinstance(token, str) or not hmac.compare_digest(_h(token), self._hash):
            raise LiveError("LIVEVIEW_TOKEN", "this live view link is not valid", 403)
        if self.clock() > self._expires:
            self._revoke()
            raise LiveError("LIVEVIEW_EXPIRED", "the live view expired; start it again", 410)
        if not self.enabled():
            self._revoke()
            raise LiveError("LIVEVIEW_OFF", "the live view is off", 403)

    def _revoke(self) -> None:
        self._hash, self._last = "", b""

    def frame(self, token: object) -> dict:
        with self._lock:
            return self._frame(token)

    def _frame(self, token: object) -> dict:
        self._check(token)
        now = self.clock()
        if self._last and now - self._last_at < MIN_INTERVAL_S:       # too fast: the same frame again, no new screenshot
            png = self._last
        else:
            try:
                png = self.session.view_frame()
            except PilotError as e:
                if e.code != "BROWSER_PILOT_OFF":
                    self._revoke()                                     # stopped, capped or left the test site: the view ends
                raise LiveError(e.code, e.message, 409) from None
            except Exception:                                          # noqa: BLE001 - a driver error text may carry a URL: fixed words only
                self._revoke()
                logs.warn("browser", "BROWSER_LIVEVIEW_FRAME", "live view frame failed")
                raise LiveError("LIVEVIEW_FRAME", "the page could not be captured; the live view ended", 502) from None
            if not png or len(png) > MAX_FRAME_BYTES:
                raise LiveError("LIVEVIEW_FRAME", "no usable frame", 502)
            self._last, self._last_at, self.seq = png, now, self.seq + 1
        return {"png_b64": base64.b64encode(png).decode("ascii"), "seq": self.seq, "expires_in_s": max(0, int(self._expires - now))}

    def stop(self) -> dict:
        """The Stop button: ends the view AND the pilot session (the driver is closed). It needs no grant: it works after the
        view expired or ended (stopping is fail-safe; the route already requires the local owner)."""
        with self._lock:
            self._revoke()
            self.session._receipt("view:stop", "ok")
            self.session.stop()
        logs.info("browser", "BROWSER_LIVEVIEW", "live view stopped")
        return {"stopped": True}


# ---------------------------------------------------------------------- the one active view (a driver PR sets it)
ACTIVE: LiveView | None = None


def set_active(view: LiveView | None) -> None:
    global ACTIVE
    ACTIVE = view


def dashboard_routes(register) -> None:
    def call(ctx, fn):
        who = getattr(ctx, "principal", None)
        if not (isinstance(who, dict) and who.get("kind") == "local"):
            return 403, {"error": "the live view opens only on the computer itself", "code": "LIVEVIEW_LOCAL_ONLY"}
        if ACTIVE is None:
            return 404, {"error": "no browser pilot session is running", "code": "LIVEVIEW_NO_SESSION"}
        try:
            return 200, fn(ACTIVE)
        except LiveError as e:
            return e.status, {"error": e.message, "code": e.code}

    register("POST", r"/api/browser/live/start$", lambda ctx, m, b: call(ctx, lambda v: v.start()))
    register("POST", r"/api/browser/live/frame$", lambda ctx, m, b: call(ctx, lambda v: v.frame((b or {}).get("grant"))))
    register("POST", r"/api/browser/live/stop$", lambda ctx, m, b: call(ctx, lambda v: v.stop()))


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(FEATURE, "תצוגה חיה של הדפדפן (קריאה בלבד)", "Browser live view (read only)", False, probe=_probe,
              needs=["browser_pilot"], risk="high",
              about="watch the browser pilot's page live on this computer, read only, local test pages only; "
                    "a Stop button ends the view and the session",
              targets=targets("test_browser_liveview.py"),
              permissions=["read"], settings="jarvis features on browser_liveview")
