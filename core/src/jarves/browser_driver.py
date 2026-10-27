"""Browser Studio: the REAL page driver for the pilot (V7 B1-B3 and the proxy, issues #574, #566, #396-#398).

`PlaywrightDriver` implements the PilotSession driver contract on a real headless Chromium/Edge:
* ONE worker thread owns Playwright (its sync API is thread-bound); every call from any other thread is a job on its queue with a
  timeout, so a dashboard thread never touches Playwright and a hung page never blocks Stop (close() abandons the worker after a
  bounded wait and the browser goes away with the process);
* the browser reaches the network ONLY through `PilotProxy` (launch flags from `proxy.launch_args()`, proxy credentials by Playwright);
  the context is fresh and private (no profile, no cookies on disk), service workers blocked, downloads refused, no permissions,
  every request additionally passes `session.request_allowed` (second line), WebSockets to non-local hosts are closed;
* `human_input` maps to mouse.click / keyboard.type / keyboard.press / mouse.wheel ONLY: no navigation, no script, no cookies.
`PilotService` wires session + proxy + driver + live view for a LOCAL TEST PAGE (loopback only, ONE port: the proxy's local_ports) and
makes the live view active. Nothing public is ever opened by it; a real site needs a separate, owner-run, Tier R decision."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import features as feat
from . import logs
from . import browser_liveview as bl
from .browser import candidates
from .browser_pilot import PilotError, PilotSession, page_scope
from .pilot_proxy import PilotProxy

CALL_TIMEOUT_S = 20.0
CLOSE_WAIT_S = 5.0
NAV_TIMEOUT_MS = 15000


class DriverError(Exception):
    pass


class PlaywrightDriver:
    def __init__(self, proxy: PilotProxy, session_ref: Callable[[], PilotSession | None], *, launch: dict | None = None,
                 call_timeout: float = CALL_TIMEOUT_S) -> None:
        self.proxy, self.session_ref, self.call_timeout = proxy, session_ref, call_timeout
        self._launch = launch if launch is not None else (candidates()[0][1] if candidates() else {})
        self._q: queue.Queue = queue.Queue()
        self._url = "about:blank"
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._closed = False
        self.pins: dict = {}
        self._pw = self._browser = self._ctx = self._page = None
        self._t = threading.Thread(target=self._main, name="pilot-driver", daemon=True)
        self._t.start()
        if not self._ready.wait(30) or self._error is not None:
            self.close()
            raise DriverError("the browser could not be started")

    # ------------------------------------------------------------------ the worker thread (the only one that touches Playwright)
    def _main(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            info = self.proxy.credentials
            self._browser = self._pw.chromium.launch(
                headless=True, args=[a for a in self.proxy.launch_args() if not a.startswith("--proxy-server")],
                proxy={"server": f"http://{info['server']}", "username": info["username"], "password": info["password"],
                       "bypass": "<-loopback>"}, **self._launch)
            self._ctx = self._browser.new_context(service_workers="block", accept_downloads=False, permissions=[],
                                                  viewport={"width": 1024, "height": 768}, locale="en-US")
            self._ctx.route("**/*", self._route)
            self._ctx.route_web_socket("**/*", self._ws)
            self._page = self._ctx.new_page()
            self._page.on("framenavigated", lambda f: setattr(self, "_url", self._page.url) if f == self._page.main_frame else None)
            self._page.on("download", lambda d: d.cancel())
            self._page.on("popup", lambda p: p.close())            # no second window: one page, one URL to judge
        except Exception as e:                                      # noqa: BLE001
            self._error = e
            logs.warn("browser", "BROWSER_DRIVER_START_FAILED", "the pilot browser could not start", error=type(e).__name__)
            self._ready.set()
            return
        self._ready.set()
        while True:
            job = self._q.get()
            if job is None:
                break
            fn, done, box = job
            try:
                box["r"] = fn()
            except Exception as e:                                  # noqa: BLE001 - the caller decides what to say
                box["e"] = e
            done.set()
        for closer in (lambda: self._ctx.close(), lambda: self._browser.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:                                       # noqa: BLE001
                pass

    def _route(self, route) -> None:
        """Second line (the proxy is the first): every request, redirect target and subresource must pass the session's vetting."""
        try:
            s = self.session_ref()
            url = route.request.url
            ok = url.startswith(("http://", "https://")) and s is not None and s.request_allowed(url)
            route.continue_() if ok else route.abort("blockedbyclient")
        except Exception:                                           # noqa: BLE001 - always answer the route: fail closed
            try:
                route.abort("blockedbyclient")
            except Exception:                                       # noqa: BLE001
                pass

    def _ws(self, ws) -> None:
        try:
            if page_scope(ws.url.replace("ws://", "http://", 1).replace("wss://", "https://", 1)) == "local":
                ws.connect_to_server()
            else:
                ws.close(code=1008)
        except Exception:                                           # noqa: BLE001
            pass

    def _job(self, fn, timeout: float | None = None):
        if self._closed:
            raise DriverError("the browser is closed")
        done, box = threading.Event(), {}
        self._q.put((fn, done, box))
        if not done.wait(timeout or self.call_timeout):
            raise DriverError("the page did not answer in time")
        if "e" in box:
            raise DriverError(type(box["e"]).__name__)             # fixed words: an error text may hold a URL or typed text
        return box.get("r")

    # ------------------------------------------------------------------ the PilotSession driver contract
    @property
    def current_url(self) -> str:
        return self._url

    def pin_hosts(self, mapping: dict) -> None:
        self.pins = dict(mapping)                                  # the PROXY dials the vetted addresses; this is bookkeeping

    def open(self, url: str) -> None:
        self._job(lambda: self._page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS), timeout=NAV_TIMEOUT_MS / 1000 + 5)

    def text(self) -> str:
        return str(self._job(lambda: self._page.inner_text("body")) or "")

    def screenshot(self) -> bytes:
        return bytes(self._job(lambda: self._page.screenshot(type="png")) or b"")

    def human_input(self, kind: str, data: dict) -> None:
        if kind == "click":
            self._job(lambda: self._page.mouse.click(data["x"], data["y"]))
        elif kind == "type":
            self._job(lambda: self._page.keyboard.type(data["text"]))
        elif kind == "key":
            self._job(lambda: self._page.keyboard.press(data["key"]))
        elif kind == "scroll":
            self._job(lambda: self._page.mouse.wheel(0, data["dy"]))
        else:
            raise DriverError("unsupported input")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._q.put(None)
        self._t.join(CLOSE_WAIT_S)                                  # a hung worker is abandoned (daemon): the process owns the browser
        try:
            self.proxy.stop()
        except Exception:                                           # noqa: BLE001
            pass


# ---------------------------------------------------------------------- the service: one local test pilot at a time
class PilotService:
    def __init__(self, home: Path, features: Callable[[], object]) -> None:
        self.home, self.features = Path(home), features
        self.session: PilotSession | None = None
        self.driver: PlaywrightDriver | None = None
        self._lock = threading.Lock()

    def _on(self, fid: str) -> bool:
        try:
            return bool(self.features().active(fid))
        except Exception:                                           # noqa: BLE001
            return False

    def start(self, url: str, driver_factory: Callable[..., PlaywrightDriver] | None = None) -> dict:
        """A pilot on ONE local test page (http://localhost:PORT/...). The proxy may reach that port and nothing else local."""
        u = urlsplit(url or "")
        if u.scheme != "http" or page_scope(url) != "local" or not u.port or u.username or u.password or (u.hostname or "") not in ("localhost", "127.0.0.1"):
            raise PilotError("PILOT_URL", "a pilot starts on a local test page only: http://localhost:PORT/")
        with self._lock:
            self._stop_locked()
            if not self._on("browser_pilot"):
                raise PilotError("BROWSER_PILOT_OFF", "the browser pilot is off (jarvis features on browser_pilot)")
            holder: dict = {}
            session = PilotSession(self.home, _Late(holder), enabled=lambda: self._on("browser_pilot"))
            proxy = PilotProxy(session, local_ports=[u.port])
            proxy.start()
            try:
                driver = (driver_factory or PlaywrightDriver)(proxy, lambda: session)
            except Exception:
                proxy.stop()
                raise
            holder["d"] = driver
            session.driver = driver
            self.session, self.driver = session, driver
            try:
                session.open(url)
            except Exception:
                self._stop_locked()
                raise
            view = bl.LiveView(session, enabled=lambda: self._on("browser_liveview"), takeover_enabled=lambda: self._on("browser_takeover"))
            bl.set_active(view)
            return {"started": True, "state": session.state}

    def _stop_locked(self) -> None:
        bl.set_active(None)
        s, self.session, self.driver = self.session, None, None
        if s is not None:
            s.stop()

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
        return {"stopped": True}


class _Late:
    """The PilotSession needs a driver at construction; the proxy needs the session, and the driver needs the proxy. This forwards
    every contract name to the real driver once it exists (before that nothing is called)."""

    def __init__(self, holder: dict) -> None:
        self._h = holder

    def __getattr__(self, name):
        d = self._h.get("d")
        if d is None:
            if name in ("open", "text", "screenshot", "close", "pin_hosts", "current_url", "human_input"):
                return (lambda *a, **k: None) if name != "current_url" else ""
            raise AttributeError(name)
        return getattr(d, name)


# ---------------------------------------------------------------------- dashboard routes: start / stop a local test pilot (owner on this computer only)
SERVICE: PilotService | None = None


def dashboard_routes(register) -> None:
    def service(ctx) -> PilotService:
        global SERVICE
        if SERVICE is None:
            SERVICE = PilotService(ctx.home, lambda: feat._ctx_features(ctx))
        return SERVICE

    def local_only(ctx):
        who = getattr(ctx, "principal", None)
        return None if isinstance(who, dict) and who.get("kind") == "local" else (
            403, {"error": "the test pilot starts only on the computer itself", "code": "PILOT_LOCAL_ONLY"})

    def start(ctx, _m, body):
        bad = local_only(ctx)
        if bad:
            return bad
        try:
            return 200, service(ctx).start(str((body or {}).get("url") or ""))
        except PilotError as e:
            return 409, {"error": e.message, "code": e.code}
        except DriverError:
            return 502, {"error": "the browser could not be started", "code": "PILOT_BROWSER"}

    def stop(ctx, _m, _b):
        bad = local_only(ctx)
        return bad or (200, service(ctx).stop())
    register("POST", r"/api/browser/pilot/start$", start)
    register("POST", r"/api/browser/pilot/stop$", stop)
