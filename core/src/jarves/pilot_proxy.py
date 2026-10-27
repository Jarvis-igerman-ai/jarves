"""Browser Studio: the pilot's local forward proxy (design: BROWSER_PILOT_REAL_DRIVER items 1, 2, 6; issues #574, #566).

A real browser must reach the network ONLY through this proxy. Chromium's `--host-resolver-rules` is a launch flag, Playwright
cannot pin a host that first appears in a redirect or a subresource, and `page.route` does not see WebSockets, WebRTC or service
workers. The proxy is the safety net under all of that: it is the only door out, and it decides, for EVERY connection:

* default deny: the target must pass `PilotSession.vet_for_proxy` (a public name is resolved ONCE per session under a lock and
  every answer vetted; private, link-local, CGNAT, metadata, SIIT and odd forms refused); public hosts only on 80 (plain GET/HEAD)
  and 443 (CONNECT tunnel); nothing unless the session is running (not paused, stopped, switched off or past its time cap) and only
  loopback while the owner drives or has driven;
* it connects ONLY to the addresses vetted for that name (`session.pins`), never to a fresh DNS answer: no rebinding;
* LOOPBACK is not "anything local": only the ports in `local_ports` (the test site's port, nothing else: never the JARVES
  dashboard, Ollama, adb, databases) and never after a public page was allowed (a public page must not reach local services);
* a redirect target is a new request vetted again before a byte is sent;
* plain HTTP: GET and HEAD without a body; the request is forwarded once and the connection is NOT a pipe from the client to the
  server (the next byte from the client closes it: no request smuggling over a reused socket); the response says Connection: close;
  HTTPS is an opaque CONNECT tunnel to the vetted address (methods inside it are limited by the driver's page.route, not here);
* open tunnels are re-checked every second: Stop, the owner taking over, the switch and the time cap all close them;
* it listens on 127.0.0.1 only, random port, Basic secret; caps: header size and a TOTAL header deadline, concurrent connections,
  idle time, tunnel lifetime, bytes; no URL is ever logged.
DRIVER CONTRACT (`launch_args()`, tested): the browser is launched with `--proxy-server`, `--proxy-bypass-list=<-loopback>` (Chromium
otherwise never sends loopback through a proxy, so the local-port rule could not be enforced) and
`--force-webrtc-ip-handling-policy=disable_non_proxied_udp` (WebRTC UDP skips HTTP proxies). The start() dict holds the secret:
a driver must never log it.
No browser is started here: tests use fake upstreams and an injected `connect`."""
from __future__ import annotations

import base64
import hmac
import ipaddress
import secrets
import select
import socket
import threading
import time
from typing import Callable, Iterable
from urllib.parse import urlsplit

from . import logs
from .browser_pilot import PilotSession, _canonical_host, page_scope, public_ip

MAX_HEADER_BYTES = 16 * 1024
HEAD_DEADLINE_S = 5.0
MAX_CONNECTIONS = 32
IDLE_S = 60.0
CONNECT_TIMEOUT_S = 10.0
MAX_TUNNEL_S = 600.0
MAX_BYTES = 200 * 1024 * 1024
TOTAL_BYTES = 1024 * 1024 * 1024        # all connections together, per proxy
MAX_BUSY_THREADS = 8
LOG_EVERY_S = 1.0
_HOP = frozenset({"proxy-authorization", "proxy-connection", "connection", "keep-alive", "te", "trailer", "upgrade", "host",
                  "content-length", "transfer-encoding", "expect"})


class Denied(Exception):
    def __init__(self, status: int, why: str) -> None:
        super().__init__(why)
        self.status, self.why = status, why


def _addr_ok(addr: str, scope: str) -> bool:
    """Defence in depth: the address actually dialled is global (public names) or loopback (local names)."""
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if scope == "local":
        return ip.is_loopback
    return public_ip(ip)


def _port(v: str) -> int:
    if not (v.isascii() and v.isdecimal() and 0 < len(v) <= 5):
        raise Denied(400, "bad port")
    return int(v)


class PilotProxy:
    def __init__(self, session: PilotSession, *, local_ports: Iterable[int] = (), public_ok: bool = True, connect: Callable[[str, int], socket.socket] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.session, self.clock = session, clock
        self.local_ports = frozenset(int(p) for p in local_ports)
        self.public_ok = public_ok                       # False = a LOCAL-ONLY proxy: every public target is refused
        self.denials: list[str] = []                     # fixed reason words of the last refusals (never a URL), for tests and status
        self.connections_local = self.connections_public = 0
        self._connect = connect or (lambda a, p: socket.create_connection((a, p), timeout=CONNECT_TIMEOUT_S))
        self.secret = secrets.token_urlsafe(24)
        self._sock: socket.socket | None = None
        self._threads: set[threading.Thread] = set()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._stop = threading.Event()
        self._public_seen = False
        self._lock = threading.Lock()
        self._accept: threading.Thread | None = None
        self._conns: set[socket.socket] = set()          # open client sockets (Stop closes them)
        self._busy_slots = threading.BoundedSemaphore(MAX_BUSY_THREADS)
        self._total = 0
        self._last_log = -1e9
        self.port = 0
        self.credentials: dict = {}
        self.connections = 0                             # accepted tunnels / requests that were let through (tests and status)

    # ------------------------------------------------------------------------------------------ decision
    def targets(self, host: str, port: int, *, plain: bool = False) -> tuple[str, list[str]]:
        """(scope, addresses to dial) for host:port, or raise Denied. The ONLY place that decides."""
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise Denied(400, "bad port")
        if not self._running():
            raise Denied(403, "the session is not running")
        h = _canonical_host(host.strip("[]").lower())
        if not h:
            raise Denied(403, "host refused")
        url = f"http://{'[' + h + ']' if ':' in h else h}:{port}/"
        scope = page_scope(url)
        if not scope:
            raise Denied(403, "address refused")
        if scope == "public" and not self.public_ok:
            self._deny("public refused")
        if scope == "public" and port != (80 if plain else 443):
            raise Denied(403, "port refused")                  # plain HTTP on 80, tunnels on 443: read-only is not enforceable in a tunnel on 80
        if scope == "local":
            if port not in self.local_ports:
                self._deny("local port refused")        # never the dashboard, a database, Ollama, adb ...
            if self._public_seen:
                raise Denied(403, "no local access after a public page")
        if not self.session.vet_for_proxy(url):                # resolves + vets a new name once, under a lock
            raise Denied(403, "request refused")
        if scope == "local":
            try:
                addrs = [str(ipaddress.ip_address(h))]
            except ValueError:
                addrs = ["127.0.0.1"]                          # localhost / *.localhost: never resolved
        else:
            try:
                ipaddress.ip_address(h)
                addrs = [h]                                    # a global IP literal (page_scope judged it)
            except ValueError:
                with self.session._pin_lock:
                    addrs = list(self.session.pins.get(h) or [])
        addrs = [a for a in addrs if _addr_ok(a, scope)]
        if not addrs:
            raise Denied(403, "no vetted address")
        with self._lock:
            self.connections += 1
            if scope == "public":
                self.connections_public += 1
                self._public_seen = True
            else:
                self.connections_local += 1
        return scope, addrs

    def _running(self) -> bool:
        """New AND open connections need a running session: switched on, AGENT or HUMAN (not PAUSED, not STOPPED) and inside the
        session's time cap, counted from the session's start (not from when a tunnel opened)."""
        s = self.session
        try:
            return bool(s.enabled()) and s.state in ("AGENT", "HUMAN") and s.clock() - s.started <= s.max_seconds
        except Exception:                                      # noqa: BLE001 - unreadable = not running
            return False

    def _deny(self, why: str) -> None:
        with self._lock:
            self.denials = (self.denials + [why])[-50:]
        raise Denied(403, why)

    def _dial(self, host: str, port: int, *, plain: bool = False) -> tuple[socket.socket, str]:
        scope, addrs = self.targets(host, port, plain=plain)
        last = None
        for a in addrs:
            try:
                return self._connect(a, port), scope
            except OSError as e:
                last = e
        raise Denied(502, "upstream unreachable") from last

    def launch_args(self) -> list[str]:
        """The browser launch flags the driver MUST use (see the module docstring)."""
        return [f"--proxy-server=http://127.0.0.1:{self.port}", "--proxy-bypass-list=<-loopback>",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]

    # ------------------------------------------------------------------------------------------ server
    def start(self) -> dict:
        if self._sock is not None:
            raise RuntimeError("already started")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(64)
        s.settimeout(0.5)
        self._sock, self.port = s, s.getsockname()[1]
        self._accept = threading.Thread(target=self._serve, name="pilot-proxy", daemon=True)
        self._accept.start()
        self.credentials = {"server": f"127.0.0.1:{self.port}", "username": "pilot", "password": self.secret}
        return dict(self.credentials)

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._lock:
            conns, threads = list(self._conns), list(self._threads)
        for c in conns:                                        # a relay stuck in a send or a read ends now
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        end = self.clock() + 3.0
        if self._accept is not None:
            self._accept.join(1.0)                              # the accept loop wakes every 0.5 s and sees _stop
        for t in threads:
            t.join(max(0.0, end - self.clock()))

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                c, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                if self._busy_slots.acquire(blocking=False):   # a flood of rejected connections cannot spawn unlimited threads
                    threading.Thread(target=self._busy, args=(c,), daemon=True).start()
                else:
                    try:
                        c.close()
                    except OSError:
                        pass
                continue
            t = threading.Thread(target=self._handle, args=(c,), daemon=True)
            with self._lock:
                self._threads.add(t)
                self._conns.add(c)
            t.start()

    def _busy(self, c: socket.socket) -> None:
        """Answer 503 and let the client read it (a reset would swallow the answer): reply, half-close, drain briefly."""
        try:
            self._reply(c, 503, "busy")
            c.shutdown(socket.SHUT_WR)
            c.settimeout(0.5)
            c.recv(4096)
        except OSError:
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass
            self._busy_slots.release()

    @staticmethod
    def _reply(c: socket.socket, status: int, why: str) -> None:
        body = (why + "\n").encode()
        head = f"HTTP/1.1 {status} {why}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n"
        if status == 407:
            head += 'Proxy-Authenticate: Basic realm="pilot"\r\n'
        try:
            c.sendall(head.encode() + b"\r\n" + body)
        except OSError:
            pass

    def _read_head(self, c: socket.socket) -> tuple[bytes, bytes]:
        buf = b""
        deadline = self.clock() + HEAD_DEADLINE_S                 # TOTAL time for the head, not per recv (slowloris)
        while b"\r\n\r\n" not in buf:
            left = deadline - self.clock()
            if left <= 0:
                raise Denied(408, "request timeout")
            if len(buf) > MAX_HEADER_BYTES:
                raise Denied(431, "header too large")
            c.settimeout(left)
            try:
                chunk = c.recv(4096)
            except socket.timeout:
                raise Denied(408, "request timeout") from None
            if not chunk:
                raise Denied(400, "closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if len(head) > MAX_HEADER_BYTES:
            raise Denied(431, "header too large")
        return head, rest

    def _authed(self, headers: dict[str, str]) -> bool:
        v = headers.get("proxy-authorization", "")
        if not v.lower().startswith("basic "):
            return False
        try:
            user, _, pw = base64.b64decode(v[6:].strip(), validate=True).decode("utf-8").partition(":")
            return hmac.compare_digest(user.encode(), b"pilot") & hmac.compare_digest(pw.encode(), self.secret.encode())
        except (ValueError, UnicodeError):
            return False

    def _log_refusal(self, status: int) -> None:
        now = self.clock()
        if now - self._last_log >= LOG_EVERY_S:                    # a local process cannot flood the log
            self._last_log = now
            logs.info("browser", "BROWSER_PILOT_PROXY", "proxy refused a connection", count=status)

    def _handle(self, c: socket.socket) -> None:
        up = None
        try:
            head, rest = self._read_head(c)
            lines = head.decode("latin-1").split("\r\n")
            if any(("\n" in ln or "\r" in ln or "\x00" in ln) for ln in lines):
                raise Denied(400, "bad header")
            parts = lines[0].split(" ")
            if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
                raise Denied(400, "bad request")
            method, target = parts[0].upper(), parts[1]
            headers: dict[str, str] = {}
            raw_headers: list[tuple[str, str]] = []
            for ln in lines[1:]:
                k, sep, v = ln.partition(":")
                if not sep or not k or k != k.strip():
                    raise Denied(400, "bad header")
                headers[k.lower()] = v.strip()
                raw_headers.append((k, v.strip()))
            if not self._authed(headers):
                raise Denied(407, "proxy authentication required")
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                if not host:
                    raise Denied(400, "bad target")
                up, scope = self._dial(host, _port(port))
                c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if rest:
                    up.sendall(rest)
                self._relay(c, up, scope)
                return
            if method not in ("GET", "HEAD"):
                raise Denied(405, "the pilot only observes")
            if rest or any(h in headers for h in ("content-length", "transfer-encoding", "expect")):
                raise Denied(400, "a GET with a body is refused")
            u = urlsplit(target)
            if u.scheme != "http" or not u.hostname or u.username or u.password:
                raise Denied(400, "absolute http URL needed")
            port = u.port or 80
            up, scope = self._dial(u.hostname, port, plain=True)
            path = (u.path or "/") + ("?" + u.query if u.query else "")
            hostline = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
            out = f"{method} {path} HTTP/1.1\r\nHost: {hostline}{'' if port == 80 else ':' + str(port)}\r\nConnection: close\r\n"
            for k, v in raw_headers:
                if k.lower() not in _HOP:
                    out += f"{k}: {v}\r\n"
            up.sendall(out.encode("latin-1", "replace") + b"\r\n")
            self._relay_response(c, up, scope)
        except Denied as d:
            self._log_refusal(d.status)
            self._reply(c, d.status, d.why)
        except (OSError, ValueError, TypeError, UnicodeError):
            self._log_refusal(400)
            self._reply(c, 400, "bad request")
        finally:
            for s in (up, c):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass
            with self._lock:
                self._threads.discard(threading.current_thread())
                self._conns.discard(c)
            self._slots.release()

    # ------------------------------------------------------------------------------------------ relays
    def _alive(self, scope: str, started: float) -> bool:
        """Re-checked every second on every open connection."""
        s = self.session
        if self._stop.is_set() or not self._running():
            return False
        if scope != "local" and (s.state == "HUMAN" or s.after_human):
            return False
        if scope == "local" and self._public_seen:             # a public page must not keep a local connection
            return False
        return self.clock() - started <= MAX_TUNNEL_S

    def _count(self, n: int) -> bool:
        """Add n bytes to the proxy-wide total; False when the total cap is reached."""
        with self._lock:
            self._total += n
            return self._total <= TOTAL_BYTES

    def _relay(self, a: socket.socket, b: socket.socket, scope: str) -> None:
        """A CONNECT tunnel: bytes both ways."""
        a.settimeout(IDLE_S)
        b.settimeout(IDLE_S)
        started = last = self.clock()
        total = 0
        socks = [a, b]
        while self._alive(scope, started):
            r, _, x = select.select(socks, [], socks, 1.0)
            if x:
                return
            if not r:
                if self.clock() - last > IDLE_S:
                    return
                continue
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                total += len(data)
                if total > MAX_BYTES or not self._count(len(data)):
                    return
                (b if s is a else a).sendall(data)
            last = self.clock()

    def _relay_response(self, c: socket.socket, up: socket.socket, scope: str) -> None:
        """Plain HTTP: server -> client only, with Connection: close. ANY byte from the client ends it (a reused socket cannot
        carry a second request to this server)."""
        c.settimeout(IDLE_S)
        up.settimeout(IDLE_S)
        started = last = self.clock()
        buf, sent_head, total = b"", False, 0
        while self._alive(scope, started):
            r, _, x = select.select([c, up], [], [c, up], 1.0)
            if x:
                return
            if not r:
                if self.clock() - last > IDLE_S:
                    return
                continue
            if c in r:
                return                                              # the client sent more (or hung up): close
            data = up.recv(65536)
            if not data:
                if buf and not sent_head:
                    c.sendall(self._close_head(buf))
                return
            total += len(data)
            if total > MAX_BYTES or not self._count(len(data)):
                return
            if not sent_head:
                buf += data
                if b"\r\n\r\n" in buf:
                    if buf.index(b"\r\n\r\n") > MAX_HEADER_BYTES:
                        return                                  # an oversized response head is refused, never truncated
                    c.sendall(self._close_head(buf))
                    sent_head, buf = True, b""
                elif len(buf) > MAX_HEADER_BYTES:
                    return
            else:
                c.sendall(data)
            last = self.clock()

    @staticmethod
    def _close_head(buf: bytes) -> bytes:
        """The response head with Connection/Keep-Alive replaced by Connection: close."""
        head, sep, body = buf.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        keep = [lines[0]] + [ln for ln in lines[1:] if ln.split(b":", 1)[0].strip().lower() not in (b"connection", b"keep-alive", b"proxy-connection")]
        return b"\r\n".join(keep + [b"Connection: close"]) + sep + body
