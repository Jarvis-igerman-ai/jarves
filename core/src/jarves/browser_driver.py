"""Browser Studio: the REAL page driver for the pilot (V7 B1-B3 and the proxy, issues #574, #566, #396-#398).

`PlaywrightDriver` implements the PilotSession driver contract on a real headless Chromium/Edge:
* ONE worker thread owns Playwright (its sync API is thread-bound); every call from any other thread is a job on its queue with a
  deadline (a job still queued after its deadline is SKIPPED; one already running is not interrupted), so a dashboard thread never touches Playwright; errors are
  fixed words. A hung page cannot block Stop: close() waits a bounded time and then KILLS the browser's process tree;
* the browser reaches the network ONLY through `PilotProxy` (launch flags from `proxy.launch_args()` plus: no direct DNS, no QUIC,
  no network prediction; the renderer sandbox stays ON); the context is fresh and private (no profile), service workers blocked,
  downloads refused, no permissions, popups closed, every request also passes `session.request_allowed` (second line); WebSockets are
  NOT intercepted by Playwright (its connect_to_server dials from the Node side, around our proxy): they go through the proxy like
  everything else, so only the one allowed local port can carry one; `current_url` is read from the page itself, not from a cached event;
* `human_input` maps to mouse.click / keyboard.type / keyboard.press / mouse.wheel ONLY: no navigation, no script, no cookies.
`PilotService` wires session + proxy + driver + live view for a LOCAL TEST PAGE: the session and the proxy are LOCAL-ONLY (every public
host is refused, also the browser's own background calls), and the proxy may reach that ONE port only (never the dashboard or a known
service port). A real or public site needs a separate, owner-run, Tier R decision."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import features as feat
from . import logs
from . import browser_liveview as bl
from . import browser_recording as br
from .browser import candidates
from .browser_pilot import PilotError, PilotSession, page_scope
from .pilot_proxy import PilotProxy

CALL_TIMEOUT_S = 20.0
CLOSE_WAIT_S = 5.0
NAV_TIMEOUT_MS = 15000
# local services a test page must never be pointed at (the dashboard port is added by the route): Ollama, adb, DevTools, databases ...
SERVICE_PORTS = frozenset({22, 23, 25, 53, 80, 443, 445, 1433, 1455, 2375, 2376, 3306, 3389, 5037, 5432, 5900, 6379, 8765, 8766, 8767, 8768, 8769, 8770,
                           8787, 9200, 9222, 9229, 11211, 11434, 27017})        # 1455 ChatGPT login callback, 8765 the default dashboard port (config.py), 8766+ JARVES LAN and other dashboards, 8787 Instagram
EXTRA_ARGS = ["--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1", "--disable-quic",
              "--disable-features=NetworkPrediction,msSmartScreenProtection"]


class DriverError(Exception):
    pass


def kill_tree(pid: int) -> None:
    """Kill a process and everything below it (the Playwright node driver owns the browser)."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
            if r.returncode != 0:
                logs.warn("browser", "BROWSER_DRIVER_KILL_FAILED", "taskkill did not end the abandoned pilot browser")
            return
        out = subprocess.run(["ps", "-e", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
        kids: dict[int, list[int]] = {}
        for ln in out.splitlines():
            a = ln.split()
            if len(a) == 2 and a[0].isdigit() and a[1].isdigit():
                kids.setdefault(int(a[1]), []).append(int(a[0]))
        todo, order = [pid], []
        while todo:
            p = todo.pop()
            order.append(p)
            todo += kids.get(p, [])
        for p in reversed(order):
            try:
                os.kill(p, 9)
            except OSError:
                pass
    except Exception:                                               # noqa: BLE001
        logs.warn("browser", "BROWSER_DRIVER_KILL_FAILED", "could not kill the abandoned pilot browser")


class PlaywrightDriver:
    def __init__(self, proxy: PilotProxy, session_ref: Callable[[], PilotSession | None], *, launch: dict | None = None,
                 call_timeout: float = CALL_TIMEOUT_S, recorder=None) -> None:
        if os.environ.get("PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK"):
            raise DriverError("loopback would bypass the proxy (PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK is set)")
        self.proxy, self.session_ref, self.call_timeout = proxy, session_ref, call_timeout
        if launch is None:
            c = candidates()
            launch = c[0][1] if c else {}
        self._launch = launch
        self._q: queue.Queue = queue.Queue()
        self._qlock = threading.Lock()
        self._url = "about:blank"
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._closed = False
        self._node_pid = 0
        self.pins: dict = {}
        self.recorder = recorder                                    # B5: only when the owner opted in for THIS session (and the switch is on)
        self._pw = self._browser = self._ctx = self._page = None
        self._t = threading.Thread(target=self._main, name="pilot-driver", daemon=True)
        self._t.start()
        if not self._ready.wait(30) or self._error is not None:
            self.close()
            raise DriverError("the browser could not be started")

    # ------------------------------------------------------------------ the worker thread (the only one that touches Playwright)
    def _launch_browser(self, sandbox: bool):
        info = self.proxy.credentials
        return self._pw.chromium.launch(
            headless=True, chromium_sandbox=sandbox,
            args=[a for a in self.proxy.launch_args() if not a.startswith("--proxy-server")] + EXTRA_ARGS,
            proxy={"server": f"http://{info['server']}", "username": info["username"], "password": info["password"],
                   "bypass": "<-loopback>"}, **self._launch)

    def _main(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            try:
                self._node_pid = int(self._pw._impl_obj._connection._transport._proc.pid)
            except Exception:                                       # noqa: BLE001
                self._node_pid = 0
            try:
                self._browser = self._launch_browser(True)          # the renderer sandbox stays ON
            except Exception:                                       # noqa: BLE001
                if os.environ.get("JARVES_PILOT_NO_SANDBOX") != "1" or os.environ.get("CI") != "true":   # CI containers only, never an owner PC
                    raise
                logs.warn("browser", "BROWSER_DRIVER_NO_SANDBOX", "the pilot browser runs WITHOUT its sandbox (JARVES_PILOT_NO_SANDBOX=1)")
                self._browser = self._launch_browser(False)
            self._ctx = self._browser.new_context(service_workers="block", accept_downloads=False, permissions=[],
                                                  viewport={"width": 1024, "height": 768}, locale="en-US")
            self._ctx.route("**/*", self._route)
            if self.recorder is not None:
                self._ctx.add_init_script(br.MASK_INIT_SCRIPT)      # password/card/code fields are blanked from the first frame
            self._page = self._ctx.new_page()
            self._page.on("download", lambda d: d.cancel())
            self._page.on("popup", lambda p: p.close())            # no second window: one page, one URL to judge
            self._page.on("dialog", lambda d: d.dismiss())
        except Exception as e:                                      # noqa: BLE001
            self._error = e
            logs.warn("browser", "BROWSER_DRIVER_START_FAILED", "the pilot browser could not start", error=type(e).__name__)
            self._ready.set()
            return
        self._ready.set()
        while True:
            try:
                job = self._q.get(timeout=0.3) if self.recorder is not None else self._q.get()
            except queue.Empty:
                self._rec_tick()
                continue
            if job is None:
                break
            fn, done, box, deadline = job
            if time.monotonic() > deadline or box.get("cancel"):    # the caller gave up: never run a stale job (a late key press)
                done.set()
                continue
            self._rec_tick()                                        # look for a sensitive field right BEFORE the action, not only between actions
            try:
                box["r"] = fn()
            except Exception as e:                                  # noqa: BLE001 - the caller decides what to say
                box["e"] = e
            done.set()
            self._rec_tick()
        if self.recorder is not None:
            try:
                self.recorder.finish(self._page)
            except Exception:                                       # noqa: BLE001
                logs.warn("browser", "BROWSER_RECORDING_FINISH_FAILED", "the recording could not be closed cleanly")
        for closer in (lambda: self._ctx.close(), lambda: self._browser.close(), lambda: self._pw.stop()):
            try:
                closer()
            except Exception:                                       # noqa: BLE001
                pass

    def _rec_tick(self) -> None:
        """Worker thread only. Records while the session is AGENT or HUMAN; pauses (closes the file) while it is paused/stopped or the page shows a
        password / card / code field. A fault here never reaches the pilot."""
        if self.recorder is None or self._page is None:
            return
        try:
            s = self.session_ref()
            self.recorder.tick(self._page, s is not None and s.state == "AGENT")      # never while the owner is in control (HUMAN) or paused
        except Exception:                                           # noqa: BLE001
            logs.warn("browser", "BROWSER_RECORDING_TICK_FAILED", "a recording check failed; it keeps the file closed")

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

    def _job(self, fn, timeout: float | None = None):
        wait = timeout or self.call_timeout
        done, box = threading.Event(), {}
        with self._qlock:
            if self._closed:
                raise DriverError("the browser is closed")
            self._q.put((fn, done, box, time.monotonic() + wait))
        if not done.wait(wait):
            box["cancel"] = True                                    # still queued: the worker will skip it
            raise DriverError("the page did not answer in time")
        if "e" in box:
            raise DriverError(type(box["e"]).__name__)             # fixed words: an error text may hold a URL or typed text
        return box.get("r")

    # ------------------------------------------------------------------ the PilotSession driver contract
    @property
    def current_url(self) -> str:
        """Read from the page itself through the worker (so pending navigation events are handled first); fails closed to ''."""
        if threading.current_thread() is self._t:
            return self._url
        try:
            self._url = str(self._job(lambda: self._page.url, timeout=3.0) or "")
        except DriverError:
            return ""
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
        with self._qlock:
            if self._closed:
                return
            self._closed = True
            self._q.put(None)
        if threading.current_thread() is not self._t:
            self._t.join(CLOSE_WAIT_S)
            if self._t.is_alive():                                  # a hung page: the browser must not outlive the session
                logs.warn("browser", "BROWSER_DRIVER_ABANDONED", "the pilot browser did not stop in time; killing it")
                if self._node_pid:
                    kill_tree(self._node_pid)
                else:
                    logs.warn("browser", "BROWSER_DRIVER_KILL_FAILED", "the pilot browser process id is unknown: it may keep running")
        try:
            self.proxy.stop()
        except Exception:                                           # noqa: BLE001
            pass


# ---------------------------------------------------------------------- the service: one local test pilot at a time
class PilotService:
    def __init__(self, home: Path, features: Callable[[], object], *, dashboard_port: "int | Callable[[], int]" = 0) -> None:
        self.home, self.features, self._dash = Path(home), features, dashboard_port
        self.session: PilotSession | None = None
        self.driver: PlaywrightDriver | None = None
        self._lock = threading.Lock()
        self._gen = 0

    def _on(self, fid: str) -> bool:
        try:
            return bool(self.features().active(fid))
        except Exception:                                           # noqa: BLE001
            return False

    def start(self, url: str, driver_factory: Callable[..., PlaywrightDriver] | None = None, *, record: bool = False) -> dict:
        """A pilot on ONE local test page (http://localhost:PORT/...). Local-only: the proxy may reach that port and nothing else."""
        u = urlsplit(url or "")
        if u.scheme != "http" or page_scope(url) != "local" or not u.port or u.username or u.password \
                or (u.hostname or "") not in ("localhost", "127.0.0.1"):
            raise PilotError("PILOT_URL", "a pilot starts on a local test page only: http://localhost:PORT/")
        dash = self._dash() if callable(self._dash) else self._dash          # read NOW: the dashboard may have restarted on another port
        if u.port in SERVICE_PORTS or u.port == dash:
            raise PilotError("PILOT_URL_PORT", "that port belongs to a service on this computer (the dashboard, a database, Ollama ...); use your own test page")
        if not self._on("browser_pilot"):
            raise PilotError("BROWSER_PILOT_OFF", "the browser pilot is off (jarvis features on browser_pilot)")
        if record and not self._on("browser_recording"):
            raise PilotError("BROWSER_RECORDING_OFF", "recording is off (jarvis features on browser_recording): start without recording or turn it on")
        with self._lock:
            self._stop_locked()
            self._gen += 1
            gen = self._gen
        # the slow part (browser launch, first page) runs WITHOUT the service lock, so Stop is never stuck behind it
        holder: dict = {}
        session = PilotSession(self.home, _Late(holder), enabled=lambda: self._on("browser_pilot"))
        session.local_only = True
        proxy = PilotProxy(session, local_ports=[u.port], public_ok=False)
        proxy.start()
        recorder = None
        try:
            if record:
                br.purge(self.home)                                 # retention first: old recordings go before a new one starts
                recorder = br.Recorder(self.home)
            driver = (driver_factory or PlaywrightDriver)(proxy, lambda: session, **({"recorder": recorder} if recorder else {}))
        except Exception:
            proxy.stop()
            if recorder is not None:
                br.delete(self.home, recorder.id)
            raise
        holder["d"] = driver
        session.driver = driver
        with self._lock:
            if gen != self._gen:                                    # a Stop (or a newer start) came while we were launching
                session.stop()
                raise PilotError("PILOT_SUPERSEDED", "the pilot was stopped while it started")
            self.session, self.driver = session, driver
        try:
            session.open(url)
        except Exception:
            with self._lock:
                if gen == self._gen:                                # only OUR start is torn down: a newer start (or a Stop) owns the service now
                    self._gen += 1
                    self._stop_locked()
                else:
                    session.stop()
            raise
        with self._lock:
            if gen != self._gen:
                raise PilotError("PILOT_SUPERSEDED", "the pilot was stopped while it started")
            bl.set_active(bl.LiveView(session, enabled=lambda: self._on("browser_liveview"), takeover_enabled=lambda: self._on("browser_takeover")))
        return {"started": True, "state": session.state, **({"recording": recorder.id} if recorder else {})}

    def _stop_locked(self) -> None:
        bl.set_active(None)
        s, self.session, self.driver = self.session, None, None
        if s is not None:
            s.stop()

    def stop(self) -> dict:
        with self._lock:
            self._gen += 1
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
            if name in ("open", "text", "screenshot", "close", "pin_hosts", "human_input"):
                return lambda *a, **k: None
            if name == "current_url":
                return ""
            raise AttributeError(name)
        return getattr(d, name)


# ---------------------------------------------------------------------- dashboard routes: start / stop a local test pilot (owner on this computer only)
SERVICE: PilotService | None = None


def dashboard_routes(register) -> None:
    def service(ctx) -> PilotService:
        global SERVICE
        if SERVICE is None:
            SERVICE = PilotService(ctx.home, lambda: feat._ctx_features(ctx), dashboard_port=lambda: int(getattr(ctx, "port", 0) or 0))
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
            return 200, service(ctx).start(str((body or {}).get("url") or ""), record=(body or {}).get("record") is True)
        except PilotError as e:
            return 409, {"error": e.message, "code": e.code}
        except DriverError:
            return 502, {"error": "the browser could not be started", "code": "PILOT_BROWSER"}

    def stop(ctx, _m, _b):
        bad = local_only(ctx)
        return bad or (200, service(ctx).stop())
    def running_id(ctx) -> str:
        d = service(ctx).driver
        r = getattr(d, "recorder", None)
        return r.id if r is not None and r.state != "finished" else ""

    def recordings(ctx, _m, _b):
        bad = local_only(ctx)
        if bad:
            return bad
        br.purge(ctx.home, keep=running_id(ctx))                    # retention also runs whenever the owner looks at the list
        d = service(ctx).driver
        r = getattr(d, "recorder", None)
        return 200, {"recordings": br.list_recordings(ctx.home), "format": br.FORMAT_LABEL, "retention_days": br.RETENTION_DAYS,
                     "max_total_mb": br.MAX_TOTAL_MB, "enabled": service(ctx)._on("browser_recording"),
                     "current": (r.view() if r is not None else None), "ffmpeg": br.ffmpeg_ready()}

    def delete_recording(ctx, _m, body):
        bad = local_only(ctx)
        if bad:
            return bad
        rid = str((body or {}).get("id") or "")
        if rid and rid == running_id(ctx):
            return 409, {"error": "this recording is still running: close the pilot first", "code": "RECORDING_RUNNING"}
        ok = br.delete(ctx.home, rid)
        return (200, {"deleted": True}) if ok else (404, {"error": "no such recording", "code": "RECORDING_NOT_FOUND"})

    register("GET", r"/api/browser/recordings$", recordings)
    register("POST", r"/api/browser/recordings/delete$", delete_recording)
    register("POST", r"/api/browser/pilot/start$", start)
    register("POST", r"/api/browser/pilot/stop$", stop)
