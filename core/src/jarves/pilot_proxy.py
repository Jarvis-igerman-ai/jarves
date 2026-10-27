"""Browser Studio: the pilot's local forward proxy (design: BROWSER_PILOT_REAL_DRIVER items 1, 2, 6; issues #574, #566).

A real browser must reach the network ONLY through this proxy. Chromium's `--host-resolver-rules` is a launch flag, Playwright
cannot pin a host that first appears in a redirect or a subresource, and `page.route` does not see WebSockets, WebRTC or service
workers. The proxy is the safety net under all of that: it is the only door out, and it decides, for EVERY connection:

* default deny: the target (host, port) must pass `PilotSession.request_allowed` (a public name is resolved ONCE per session and
  every answer vetted; private, loopback-for-public, link-local, CGNAT, metadata, SIIT and odd forms are refused; public hosts only
  on ports 80/443; nothing while the owner drives except loopback; nothing after Stop);
* it connects ONLY to the addresses vetted for that name (`session.pins`), never to a fresh DNS answer, so a rebinding server cannot
  hand the browser a second, private address; `localhost` and loopback literals go to loopback and are never resolved;
* a redirect target is a new request and is vetted again before a byte is sent (the post-hoc final-URL check is only a second line);
* plain HTTP only GET and HEAD (the pilot observes); HTTPS is an opaque CONNECT tunnel to the vetted address;
* it listens on 127.0.0.1 only, on a random port, and wants `Proxy-Authorization` with a random secret (another local process
  cannot use it); caps: header size, concurrent connections, idle time, bytes per connection; no URL is ever logged.
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
from typing import Callable
from urllib.parse import urlsplit

from . import logs
from .browser_pilot import PilotSession, _canonical_host, page_scope

MAX_HEADER_BYTES = 16 * 1024
MAX_CONNECTIONS = 32
IDLE_S = 60.0
CONNECT_TIMEOUT_S = 10.0
MAX_BYTES = 200 * 1024 * 1024
_HOP = frozenset({"proxy-authorization", "proxy-connection", "connection", "keep-alive", "te", "trailer", "upgrade"})


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
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


class PilotProxy:
    def __init__(self, session: PilotSession, *, connect: Callable[[str, int], socket.socket] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.session, self.clock = session, clock
        self._connect = connect or (lambda a, p: socket.create_connection((a, p), timeout=CONNECT_TIMEOUT_S))
        self.secret = secrets.token_urlsafe(24)
        self._sock: socket.socket | None = None
        self._threads: set[threading.Thread] = set()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._stop = threading.Event()
        self.port = 0

    # ------------------------------------------------------------------------------------------ decision (pure)
    def targets(self, host: str, port: int) -> tuple[str, list[str]]:
        """(scope, addresses to dial) for host:port, or raise Denied. The ONLY place that decides."""
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise Denied(400, "bad port")
        h = _canonical_host(host.strip("[]").lower())
        if not h:
            raise Denied(403, "host refused")
        url = f"http://{'[' + h + ']' if ':' in h else h}:{port}/"
        scope = page_scope(url)
        if not scope:
            raise Denied(403, "address refused")
        state = self.session.state
        if state == "STOPPED" or (state == "HUMAN" and scope != "local"):   # the proxy enforces this itself, whatever the session does
            raise Denied(403, "not allowed in this state")
        if not self.session.request_allowed(url):          # resolves + vets a new name once, and pins it
            raise Denied(403, "request refused")
        if scope == "local":
            try:
                lit = ipaddress.ip_address(h)
                addrs = [str(lit)]
            except ValueError:
                addrs = ["127.0.0.1"]                      # localhost / *.localhost: never resolved
        else:
            try:
                ipaddress.ip_address(h)
                addrs = [h]                                # a global IP literal (page_scope judged it)
            except ValueError:
                addrs = list(self.session.pins.get(h) or [])
        addrs = [a for a in addrs if _addr_ok(a, scope)]
        if not addrs:
            raise Denied(403, "no vetted address")
        return scope, addrs

    def _dial(self, host: str, port: int) -> socket.socket:
        scope, addrs = self.targets(host, port)
        last = None
        for a in addrs:
            try:
                return self._connect(a, port)
            except OSError as e:
                last = e
        raise Denied(502, "upstream unreachable") from last

    # ------------------------------------------------------------------------------------------ server
    def start(self) -> dict:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(64)
        s.settimeout(0.5)
        self._sock, self.port = s, s.getsockname()[1]
        t = threading.Thread(target=self._serve, name="pilot-proxy", daemon=True)
        t.start()
        return {"server": f"127.0.0.1:{self.port}", "username": "pilot", "password": self.secret}

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        for t in list(self._threads):
            t.join(1.0)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                c, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                self._reply(c, 503, "busy")
                c.close()
                continue
            t = threading.Thread(target=self._handle, args=(c,), daemon=True)
            self._threads.add(t)
            t.start()

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
        c.settimeout(10.0)
        while b"\r\n\r\n" not in buf:
            if len(buf) > MAX_HEADER_BYTES:
                raise Denied(431, "header too large")
            chunk = c.recv(4096)
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
        except Exception:                                  # noqa: BLE001
            return False
        return hmac.compare_digest(user, "pilot") and hmac.compare_digest(pw, self.secret)

    def _handle(self, c: socket.socket) -> None:
        up = None
        try:
            head, rest = self._read_head(c)
            lines = head.decode("latin-1").split("\r\n")
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
                if not host or not port.isdigit():
                    raise Denied(400, "bad target")
                up = self._dial(host, int(port))
                c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                if rest:
                    up.sendall(rest)
                self._relay(c, up)
                return
            if method not in ("GET", "HEAD"):
                raise Denied(405, "the pilot only observes")
            u = urlsplit(target)
            if u.scheme != "http" or not u.hostname or u.username or u.password:
                raise Denied(400, "absolute http URL needed")
            port = u.port or 80
            up = self._dial(u.hostname, port)
            path = (u.path or "/") + ("?" + u.query if u.query else "")
            out = f"{method} {path} HTTP/1.1\r\nHost: {u.hostname}{'' if port == 80 else ':' + str(port)}\r\nConnection: close\r\n"
            for k, v in raw_headers:
                if k.lower() not in _HOP and k.lower() != "host":
                    out += f"{k}: {v}\r\n"
            up.sendall(out.encode("latin-1", "replace") + b"\r\n")
            self._relay(c, up)
        except Denied as d:
            logs.info("browser", "BROWSER_PILOT_PROXY", "proxy refused a connection", count=d.status)
            self._reply(c, d.status, d.why)
        except OSError:
            pass
        finally:
            for s in (up, c):
                try:
                    if s is not None:
                        s.close()
                except OSError:
                    pass
            self._threads.discard(threading.current_thread())
            self._slots.release()

    def _relay(self, a: socket.socket, b: socket.socket) -> None:
        a.settimeout(None)
        b.settimeout(None)
        total, last = 0, self.clock()
        socks = [a, b]
        while not self._stop.is_set():
            if self.session.state == "STOPPED":            # Stop closes every tunnel
                return
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
                if total > MAX_BYTES:
                    return
                (b if s is a else a).sendall(data)
            last = self.clock()
