"""Browser Studio B2 (V7, #397): a READ-ONLY live view of the pilot's page for the owner, on his own computer only.

Frames are PNGs polled by the dashboard (no push channel, no WebSocket). The server enforces everything, the page enforces
nothing: (1) loopback owner only (a paired phone or any other principal is refused); (2) a per-view token, 256 random bits,
kept only as a hash, shown once to the dashboard that started the view, sent in a POST body (never a URL, so it is in no log),
fixed expiry (5 minutes), one live view at a time (a new start revokes the old token); (3) TEST SITE ONLY (top-level page only: a local test page that embeds public content would show it; use test pages that do not): PilotSession.view_frame
refuses unless the current page is a loopback page, checked on every frame; (4) no input path for the AGENT exists. B3 adds ONE owner path: takeover / input / return (grant required, local test
pages only, the owner's keystrokes go to the page and nowhere else, see PilotSession.human_input); (5) a Stop button that stops the view AND the pilot session
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
TAKEOVER = "browser_takeover"       # B3: a SEPARATE owner switch (beta, OFF): the owner's own clicks and typing into the pilot's page
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
                 ttl_s: float = TTL_S, takeover_enabled: Callable[[], bool] = lambda: False) -> None:
        self.session, self.enabled, self.clock, self.ttl_s = session, enabled, clock, ttl_s
        self.takeover_enabled = takeover_enabled
        self._fl = threading.Lock()                       # one screenshot at a time (never held together with _lock across driver I/O)
        self._hash = ""
        self._holder = "local"
        self._expires = 0.0
        self._last_at = -1e9
        self._last: bytes = b""
        self.seq = 0
        self._gb = 0                                       # bumped on start / give back / revoke: a frame taken before it is dropped
        self._lock = threading.RLock()        # the dashboard is threaded: start / frame / stop never overlap (#649 review).
        # NOTE for the driver PR: the page driver must be called from its own thread (Playwright sync is thread-bound).

    def start(self, holder: str = "local") -> dict:
        """`holder` = who the grant belongs to ("local" = the dashboard on this computer, "phone:<device>"). A live, unexpired grant of
        ANOTHER holder is never replaced (LIVEVIEW_IN_USE): a phone cannot knock the owner's view off, nor one phone another."""
        if not self.enabled():
            raise LiveError("LIVEVIEW_OFF", "the live view is off (jarvis features on browser_liveview)", 403)
        try:                                                # driver I/O OUTSIDE the view lock: fail early (switch, stopped, cap, test page only)
            self.session.view_frame()
        except PilotError as e:
            raise LiveError(e.code, e.message, 429 if e.code == "BROWSER_PILOT_BUSY" else 409) from None
        with self._lock:
            if self._hash and self.clock() <= self._expires and holder != self._holder:
                raise LiveError("LIVEVIEW_IN_USE", "the live view is open somewhere else; close it there first", 409)
            token = secrets.token_urlsafe(32)
            self._holder = holder
            self._hash, self._expires, self._last, self._last_at, self.seq = _h(token), self.clock() + self.ttl_s, b"", -1e9, 0
            self._gb += 1
            self.session._receipt("view:start", "ok")
            logs.info("browser", "BROWSER_LIVEVIEW", "live view started")
            return {"grant": token, "expires_in_s": int(self.ttl_s), "interval_ms": 1000, "state": self.session.state}

    def _check(self, token: object, holder: str = "local") -> None:
        if not self._hash or not isinstance(token, str) or not hmac.compare_digest(_h(token), self._hash) or holder != self._holder:
            raise LiveError("LIVEVIEW_TOKEN", "this live view link is not valid", 403)
        if self.clock() > self._expires:
            self._revoke()
            self.session.human_returns()                 # never leave the agent locked out behind a dead view (HUMAN -> PAUSED)
            raise LiveError("LIVEVIEW_EXPIRED", "the live view expired; start it again", 410)
        if not self.enabled():
            self._revoke()
            raise LiveError("LIVEVIEW_OFF", "the live view is off", 403)

    def _revoke(self) -> None:
        self._hash, self._last, self._holder = "", b"", "local"
        self._gb += 1

    def frame(self, token: object, holder: str = "local") -> dict:
        """The view lock is held only for this object's own fields, NEVER across the page driver: a hung screenshot must not block
        Stop, takeover, give back or the owner's input. One screenshot at a time (`_fl`); the others get the same new frame."""
        with self._lock:
            self._check(token, holder)
            cached = self._cached()
        if cached is not None:
            return self._answer(cached)
        if not self._fl.acquire(timeout=3.0):
            raise LiveError("LIVEVIEW_BUSY", "the page is busy; try again", 429)
        try:
            with self._lock:
                self._check(token, holder)
                cached = self._cached()
                gb = self._gb
            if cached is not None:                           # another request just took the picture
                return self._answer(cached)
            try:
                png = self.session.view_frame()
            except PilotError as e:
                if e.code == "BROWSER_PILOT_BUSY":
                    raise LiveError(e.code, e.message, 429) from None
                with self._lock:
                    if e.code != "BROWSER_PILOT_OFF":
                        self._revoke()                       # stopped, capped or left the test site: the view ends
                if e.code == "BROWSER_PILOT_VIEW_TEST_SITE" and self.session.state == "HUMAN":
                    self.session.stop()                      # the owner was driving a page that left the test site
                raise LiveError(e.code, e.message, 409) from None
            except Exception:                                # noqa: BLE001 - a driver error text may carry a URL: fixed words only
                with self._lock:
                    self._revoke()
                logs.warn("browser", "BROWSER_LIVEVIEW_FRAME", "live view frame failed")
                raise LiveError("LIVEVIEW_FRAME", "the page could not be captured; the live view ended", 502) from None
            if not png or len(png) > MAX_FRAME_BYTES:
                raise LiveError("LIVEVIEW_FRAME", "no usable frame", 502)
            with self._lock:
                if not self._hash or self._gb != gb:         # revoked or handed back while the picture was taken: drop it
                    raise LiveError("LIVEVIEW_TOKEN", "this live view link is not valid", 403)
                self._last, self._last_at, self.seq = png, self.clock(), self.seq + 1
            return self._answer(png)
        finally:
            self._fl.release()

    def _cached(self) -> bytes | None:
        if self._last and self.clock() - self._last_at < MIN_INTERVAL_S:
            return self._last
        return None

    def _answer(self, png: bytes) -> dict:
        return {"png_b64": base64.b64encode(png).decode("ascii"), "seq": self.seq,
                "expires_in_s": max(0, int(self._expires - self.clock())), "state": self.session.state}

    # ---- B3: the owner drives the page from this view, in the SAME session (local test pages only, see PilotSession.human_input)
    def _takeover_check(self, token: object, holder: str = "local") -> None:
        self._check(token, holder)
        if not self.takeover_enabled():
            raise LiveError("LIVEVIEW_TAKEOVER_OFF", f"taking over needs its own owner switch (jarvis features on {TAKEOVER})", 403)

    def takeover(self, token: object, holder: str = "local") -> dict:
        with self._lock:
            self._takeover_check(token, holder)
            if not self.session.human_takes_over():
                raise LiveError("LIVEVIEW_TAKEOVER", "the session cannot be taken over now (is it running, and not already yours?)", 409)
            logs.info("browser", "BROWSER_LIVEVIEW", "owner took over")
            return {"state": self.session.state}

    def give_back(self, token: object, resume: object = False, holder: str = "local") -> dict:
        """Two explicit steps: give back (the session is PAUSED, the assistant still cannot act) and, only when `resume` is true,
        let the assistant continue (it may then read the page as it is now, a logged-in account for instance)."""
        with self._lock:
            if resume is True:
                self._takeover_check(token, holder)
            else:
                self._check(token, holder)
            self._last = b""                                   # the picture may show what the owner typed
            self._gb += 1                                      # ... and a picture still being taken must not become the cache
            was_paused = self.session.state == "PAUSED"        # decided on the SERVER state, not on what the page believes
            out = self.session.human_returns()
            if resume is True and was_paused and self.session.state == "PAUSED":
                self.session.resume()
                out = {**out, "returned": True, "state": self.session.state}
            return out

    def human(self, token: object, kind: object, data: object, holder: str = "local") -> dict:
        """One owner input event. The answer is {"ok": true} only: the typed text is never echoed, logged or stored here."""
        with self._lock:
            self._takeover_check(token, holder)
        try:                                                   # outside the view lock: a hung page must not block the owner's other buttons
            return self.session.human_input(str(kind), data if isinstance(data, dict) else {})
        except PilotError as e:
            if e.code in ("BROWSER_PILOT_STOPPED", "BROWSER_PILOT_CAP", "BROWSER_PILOT_VIEW_TEST_SITE"):
                with self._lock:
                    self._revoke()
            raise LiveError(e.code, e.message, 409 if e.code != "BROWSER_PILOT_BUSY" else 429) from None

    def stop(self) -> dict:
        """The Stop button: ends the view AND the pilot session (the driver is closed). It needs no grant: it works after the
        view expired or ended (stopping is fail-safe; the route already requires the local owner)."""
        self._hash, self._last, self._holder = "", b"", "local"   # no view lock: Stop must work while a frame or an input hangs
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
    register("POST", r"/api/browser/live/takeover$", lambda ctx, m, b: call(ctx, lambda v: v.takeover((b or {}).get("grant"))))
    register("POST", r"/api/browser/live/return$", lambda ctx, m, b: call(ctx, lambda v: v.give_back((b or {}).get("grant"), (b or {}).get("resume"))))
    register("POST", r"/api/browser/live/input$", lambda ctx, m, b: call(ctx, lambda v: v.human(
        (b or {}).get("grant"), (b or {}).get("kind"), (b or {}).get("data"))))


def _probe(c: feat.ProbeCtx) -> tuple[bool, str]:
    return True, ""


feat.register(TAKEOVER, "שליטה ידנית בדפדפן הפיילוט", "Take over the browser pilot yourself", False, probe=_probe,
              needs=["browser_liveview"], risk="high", stage="beta",
              about="from the live view you can take over the pilot's page and click and type yourself on local test pages; the assistant "
                    "is locked out meanwhile and never sees what you type",
              targets=targets("test_browser_takeover.py"),
              permissions=["your clicks and typing go to the page"], settings=f"jarvis features on {TAKEOVER}")


feat.register(FEATURE, "תצוגה חיה של הדפדפן (קריאה בלבד)", "Browser live view (read only)", False, probe=_probe,
              needs=["browser_pilot"], risk="high",
              about="watch the browser pilot's page live on this computer, read only, local test pages only; "
                    "a Stop button ends the view and the session",
              targets=targets("test_browser_liveview.py"),
              permissions=["read"], settings="jarvis features on browser_liveview")
