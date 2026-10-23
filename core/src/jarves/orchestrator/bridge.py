"""The ONE local transport between JARVES and the coding agents' plugin processes (and the CLI / dashboard of
another process): a Windows named pipe `\\\\.\\pipe\\jarves-agents-<id>` (remote clients rejected), a Unix socket
(0600, in a 0700 folder) elsewhere. No TCP port.

Auth: a per-instance token (`<home>/agents/bridge.token`, 32 random bytes, rewritten at every Core start, owner-only
file) and a mutual HMAC-SHA256 challenge before anything else: the server proves it holds the token too, so a
squatting pipe cannot collect requests. A wrong token, a slow client (5 s per step) or an oversize frame closes
the connection. Frames are JSON objects (`send_bytes` / `recv_bytes`; pickle is never used).
<id> is derived from the profile home, so two profiles (or two test homes) never share a pipe.

Server: `BridgeServer(home, handler).start()`; `handler(conn, msg) -> dict | None` runs on the connection's own
thread; `conn.push(obj)` sends an unsolicited frame (live channel delivery). Client: `BridgeClient.connect(home)`
then `.request(op, **kw)`; `.listen(callback)` for pushed frames.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from multiprocessing import connection as mpc
from pathlib import Path
from typing import Any, Callable

from .. import logs

STEP_S = 5.0
MAX_FRAME = 1_000_000
MAX_CONNS = 64
MAGIC = b"JARVES-AGENTS-1"
REPLACE_RETRY_S = 1.0                # Windows: how long a briefly held bridge.token may refuse the swap
_IS_WIN = sys.platform == "win32"


def instance_id(home: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(Path(home).resolve())).encode("utf-8")).hexdigest()[:16]


def address(home: Path) -> tuple[str, str]:
    """(address, family) of this profile's bridge."""
    iid = instance_id(home)
    if _IS_WIN:
        return rf"\\.\pipe\jarves-agents-{iid}", "AF_PIPE"
    p = Path(home) / "agents" / "bridge.sock"
    if len(str(p)) > 100:                                   # AF_UNIX path limit
        d = Path(tempfile.gettempdir()) / f"jarves-{os.getuid()}"
        d.mkdir(mode=0o700, exist_ok=True)
        p = d / f"{iid}.sock"
    return str(p), "AF_UNIX"


def token_path(home: Path) -> Path:
    return Path(home) / "agents" / "bridge.token"


def _replace(src: Path, dst: Path) -> None:
    """os.replace that rides out a brief Windows sharing violation. While another process holds `dst` open (a CLI
    or plugin reading the token, antivirus or the indexer scanning the file just written), MoveFileEx refuses the swap
    with WinError 5/32 (PermissionError). Retry with short sleeps for up to REPLACE_RETRY_S, then raise the last error."""
    deadline = time.monotonic() + REPLACE_RETRY_S
    delay = 0.01
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            left = deadline - time.monotonic()
            if left <= 0:
                raise
            time.sleep(min(delay, left))
            delay = min(delay * 2, 0.1)


def write_token(home: Path) -> bytes:
    p = token_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tok = secrets.token_hex(32)
    tmp = p.with_name(f"bridge.token.{secrets.token_hex(3)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(tok)
        _replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)                      # never leave a stray copy of the token behind
        raise
    return tok.encode("ascii")


def read_token(home: Path) -> bytes | None:
    try:
        t = token_path(home).read_text(encoding="ascii").strip()
    except OSError:
        return None
    return t.encode("ascii") if len(t) == 64 else None


def _mac(token: bytes, side: bytes, nonce: bytes) -> bytes:
    return hmac.new(token, side + nonce, hashlib.sha256).digest()


def _recv(conn, timeout: float = STEP_S) -> bytes:
    if not conn.poll(timeout):
        raise TimeoutError("bridge peer too slow")
    return conn.recv_bytes(MAX_FRAME)


if _IS_WIN:
    import _winapi

    PIPE_REJECT_REMOTE_CLIENTS = 0x00000008

    class _LocalPipeListener(mpc.PipeListener):           # type: ignore[name-defined, misc]
        """The stdlib pipe listener, plus PIPE_REJECT_REMOTE_CLIENTS (no SMB client may connect)."""

        def _new_handle(self, first=False):
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            return _winapi.CreateNamedPipe(
                self._address, flags,
                _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT
                | PIPE_REJECT_REMOTE_CLIENTS,
                _winapi.PIPE_UNLIMITED_INSTANCES, mpc.BUFSIZE, mpc.BUFSIZE, _winapi.NMPWAIT_WAIT_FOREVER,
                _winapi.NULL)


class Conn:
    """One accepted, authenticated connection."""

    def __init__(self, raw, server: "BridgeServer") -> None:
        self.raw, self.server = raw, server
        self.role = ""
        self.session_id = ""
        self.meta: dict = {}
        self._wlock = threading.Lock()
        self.closed = False

    def push(self, obj: dict) -> bool:
        try:
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            with self._wlock:
                self.raw.send_bytes(data)
            return True
        except (OSError, ValueError, EOFError):
            self.closed = True
            return False

    def close(self) -> None:
        self.closed = True
        try:
            self.raw.close()
        except OSError:
            pass


class BridgeServer:
    def __init__(self, home: Path, handler: Callable[[Conn, dict], dict | None],
                 on_close: Callable[[Conn], None] | None = None) -> None:
        self.home = Path(home)
        self.handler, self.on_close = handler, on_close
        self.addr, self.family = address(self.home)
        self.token = b""
        self._listener = None
        self._stop = threading.Event()
        self.conns: set[Conn] = set()
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.started_at = 0.0
        self.refused = 0

    def start(self) -> "BridgeServer":
        self.token = write_token(self.home)
        if self.family == "AF_UNIX":
            try:
                os.unlink(self.addr)
            except OSError:
                pass
        if _IS_WIN:
            lst = mpc.Listener.__new__(mpc.Listener)
            lst._listener = _LocalPipeListener(self.addr)
            lst._authkey = None
            self._listener = lst
        else:
            old = os.umask(0o177)
            try:
                self._listener = mpc.Listener(self.addr, self.family)
            finally:
                os.umask(old)
            os.chmod(self.addr, 0o600)
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._accept_loop, name="agents-bridge", daemon=True)
        self.thread.start()
        logs.info("agents", "AGENTS_BRIDGE_STARTED", "agent bridge listening", transport=self.family)
        return self

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        try:                                              # wake the blocking accept()
            c = mpc.Client(self.addr, self.family)
            c.close()
        except OSError:
            pass
        with self._lock:
            conns = list(self.conns)
        for c in conns:
            c.close()
        try:
            self._listener.close()
        except Exception:
            pass
        if self.thread is not None:
            self.thread.join(5)
        if self.family == "AF_UNIX":
            try:
                os.unlink(self.addr)
            except OSError:
                pass
        logs.info("agents", "AGENTS_BRIDGE_STOPPED", "agent bridge stopped")

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._listener.accept()
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.05)
                continue
            if self._stop.is_set():
                try:
                    raw.close()
                except OSError:
                    pass
                return
            with self._lock:
                too_many = len(self.conns) >= MAX_CONNS
            if too_many:
                self.refused += 1
                raw.close()
                continue
            threading.Thread(target=self._serve, args=(raw,), name="agents-bridge-conn", daemon=True).start()

    def _handshake(self, raw) -> bool:
        nonce = secrets.token_bytes(32)
        raw.send_bytes(MAGIC + nonce)
        answer = _recv(raw)
        if not hmac.compare_digest(answer, _mac(self.token, b"c", nonce)):
            return False
        raw.send_bytes(b"OK" + _mac(self.token, b"s", nonce))
        return True

    def _serve(self, raw) -> None:
        conn = Conn(raw, self)
        try:
            if not self._handshake(raw):
                self.refused += 1
                logs.warn("agents", "AGENTS_BRIDGE_AUTH_FAILED", "a bridge client failed authentication")
                raw.close()
                return
        except (OSError, EOFError, TimeoutError):
            self.refused += 1
            raw.close()
            return
        with self._lock:
            self.conns.add(conn)
        try:
            while not self._stop.is_set() and not conn.closed:
                try:
                    if not raw.poll(1.0):
                        continue
                    data = raw.recv_bytes(MAX_FRAME)
                except (OSError, EOFError):
                    break
                try:
                    msg = json.loads(data.decode("utf-8"))
                    if not isinstance(msg, dict):
                        raise ValueError("frame must be an object")
                except (ValueError, UnicodeDecodeError):
                    conn.push({"ok": False, "error": "bad frame"})
                    continue
                try:
                    reply = self.handler(conn, msg)
                except Exception as e:                      # one bad request never kills the bridge
                    logs.error("agents", "AGENTS_BRIDGE_HANDLER_FAILED", "bridge request failed", error_type=type(e).__name__,
                               exc=True, op=str(msg.get("op"))[:30])
                    reply = {"ok": False, "error": type(e).__name__}
                if reply is not None:
                    if "rid" in msg:
                        reply = {**reply, "rid": msg["rid"]}
                    conn.push(reply)
        finally:
            with self._lock:
                self.conns.discard(conn)
            conn.close()
            if self.on_close is not None:
                try:
                    self.on_close(conn)
                except Exception:
                    logs.error("agents", "AGENTS_BRIDGE_CLOSE_FAILED", "bridge close hook failed", exc=True)


class BridgeError(Exception):
    pass


class BridgeClient:
    def __init__(self, raw) -> None:
        self.raw = raw
        self._lock = threading.Lock()
        self._rid = 0
        self._replies: dict[int, dict] = {}
        self._cv = threading.Condition()
        self._listener: threading.Thread | None = None
        self._on_push: Callable[[dict], None] | None = None
        self.closed = False

    @classmethod
    def connect(cls, home: Path, *, token: bytes | None = None, timeout: float = STEP_S) -> "BridgeClient":
        tok = token if token is not None else read_token(home)
        if not tok:
            raise BridgeError("no bridge token (JARVES is not running)")
        addr, fam = address(Path(home))
        try:
            raw = mpc.Client(addr, fam)
        except (OSError, EOFError) as e:
            raise BridgeError(f"bridge not reachable ({type(e).__name__})") from None
        try:
            hello = _recv(raw, timeout)
            if not hello.startswith(MAGIC) or len(hello) != len(MAGIC) + 32:
                raise BridgeError("not a JARVES bridge")
            nonce = hello[len(MAGIC):]
            raw.send_bytes(_mac(tok, b"c", nonce))
            ok = _recv(raw, timeout)
            if not (ok.startswith(b"OK") and hmac.compare_digest(ok[2:], _mac(tok, b"s", nonce))):
                raise BridgeError("bridge authentication failed")
        except (OSError, EOFError, TimeoutError) as e:
            raw.close()
            raise BridgeError(f"bridge authentication failed ({type(e).__name__})") from None
        except BridgeError:
            raw.close()
            raise
        return cls(raw)

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        with self._lock:
            self.raw.send_bytes(data)

    def request(self, op: str, timeout: float = 30.0, **kw) -> dict:
        with self._cv:
            self._rid += 1
            rid = self._rid
        try:
            self._send({"op": op, "rid": rid, **kw})
        except (OSError, ValueError) as e:
            raise BridgeError(f"bridge send failed ({type(e).__name__})") from None
        deadline = time.monotonic() + timeout
        if self._listener is not None:                   # a listening client: the reader thread files replies
            with self._cv:
                while rid not in self._replies:
                    left = deadline - time.monotonic()
                    if left <= 0 or self.closed:
                        raise BridgeError("bridge reply timed out")
                    self._cv.wait(min(left, 1.0))
                return self._replies.pop(rid)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BridgeError("bridge reply timed out")
            try:
                if not self.raw.poll(min(left, 1.0)):
                    continue
                msg = json.loads(self.raw.recv_bytes(MAX_FRAME).decode("utf-8"))
            except (OSError, EOFError, ValueError) as e:
                raise BridgeError(f"bridge closed ({type(e).__name__})") from None
            if isinstance(msg, dict) and msg.get("rid") == rid:
                return msg

    def listen(self, on_push: Callable[[dict], None]) -> None:
        """Start a reader thread: replies go to request(), everything else (pushes) to on_push."""
        self._on_push = on_push

        def run() -> None:
            while not self.closed:
                try:
                    if not self.raw.poll(1.0):
                        continue
                    msg = json.loads(self.raw.recv_bytes(MAX_FRAME).decode("utf-8"))
                except (OSError, EOFError, ValueError):
                    break
                if isinstance(msg, dict) and "rid" in msg:
                    with self._cv:
                        self._replies[msg["rid"]] = msg
                        self._cv.notify_all()
                elif isinstance(msg, dict) and self._on_push is not None:
                    try:
                        self._on_push(msg)
                    except Exception:
                        pass
            self.closed = True
            with self._cv:
                self._cv.notify_all()
            if self._on_push is not None:
                try:
                    self._on_push({"push": "closed"})
                except Exception:
                    pass
        self._listener = threading.Thread(target=run, name="agents-bridge-client", daemon=True)
        self._listener.start()

    def close(self) -> None:
        self.closed = True
        try:
            self.raw.close()
        except OSError:
            pass


def try_request(home: Path, op: str, timeout: float = 30.0, **kw) -> dict | None:
    """One request to the running Core's bridge; None when no Core serves this home."""
    try:
        c = BridgeClient.connect(home)
    except BridgeError:
        return None
    try:
        return c.request(op, timeout=timeout, **kw)
    except BridgeError:
        return None
    finally:
        c.close()


def safe_view(home: Path) -> dict[str, Any]:
    addr, fam = address(home)
    return {"transport": "named pipe" if fam == "AF_PIPE" else "unix socket", "address": addr,
            "token_file": str(token_path(home)), "token_present": read_token(home) is not None}
