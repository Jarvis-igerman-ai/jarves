"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). The code is never stored. The running dashboard server holds an X25519 key in memory only and publishes
its public half (unlock_pub.json); the CLI stores an ephemeral public key and an HMAC tag of the code under the shared
secret. An agent that READS every file in the workflow home (access.token included) cannot check a guess offline:
the tag key needs the server's in-memory private key. 5-minute expiry, single use, 5 wrong tries cancel it. Honest
limits: a same-user process that can WRITE the workflow home could plant its own key or record, and one that can read
the server's process memory (on Windows usually allowed for the same user, ReadProcessMemory) can take the private
key; the CLI refuses agent environments and non-terminals (owner_only_guard)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

CODE_TTL_S = 300      # how long a shown code can be typed
UNLOCK_TTL_S = 7 * 86400  # default session unlock: 1 week (owner decision 2 Nov; was 8 h); policy watchdog.owner_unlock_s overrides
FILE = "unlock.json"
PUB_FILE = "unlock_pub.json"
PUB_POLL_S = 0.1      # open: the server binds its port a moment before it writes unlock_pub.json
OPEN_WAIT_S = 2.0     # open: at most this long for that key
RETRY_PAUSE_S = 0.2   # Windows: give a reader holding unlock.json time to close it before the one retry
_sleep = time.sleep   # injectable for hermetic tests


class UnlockUnavailable(RuntimeError):
    """No running dashboard server has published its unlock key."""


class ServerKey:
    """The dashboard server's unlock key: the private half never leaves this process (a restart makes a new one)."""

    def __init__(self) -> None:
        self._priv = X25519PrivateKey.generate()

    def _pub(self) -> str:
        return self._priv.public_key().public_bytes_raw().hex()

    def publish(self, home: Path) -> None:
        _write(Path(home) / PUB_FILE, {"pub": self._pub(), "pid": os.getpid()})

    def withdraw(self, home: Path) -> None:
        """On server stop: remove the published key if it is still ours (a newer server's key stays)."""
        path = Path(home) / PUB_FILE
        try:
            # Accepted race (fails closed): a newer server republishing between this read and the unlink loses its
            # file, so issue() says "start the dashboard" until it is restarted; it can never make a wrong code valid.
            if json.loads(path.read_text(encoding="ascii")).get("pub") == self._pub():
                path.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            pass  # nothing published, unreadable or not ours: leave it; issue() checks the publisher is alive

    def shared(self, eph: bytes) -> bytes:
        return self._priv.exchange(X25519PublicKey.from_public_bytes(eph))


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _tag(shared: bytes, eph: bytes, code: str) -> bytes:
    key = hashlib.sha256(b"jarves-unlock-v2" + shared + eph).digest()
    return hmac.new(key, normalize(code).encode("ascii", "replace"), hashlib.sha256).digest()


def _write(path: Path, record: dict) -> None:
    """Atomic owner-only write: unique temp file in the same folder, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            json.dump(record, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _alive(pid: int) -> bool:
    from ..orchestrator.procs import alive  # Windows-safe: os.kill(pid, 0) would terminate the process there
    return alive(pid)


def _read_pub(home: Path, wait_s: float) -> dict:
    """Read the published key once; with wait_s, poll a missing file for at most wait_s (bounded, injectable sleep)."""
    for _ in range(round(wait_s / PUB_POLL_S)):
        try:
            return json.loads((Path(home) / PUB_FILE).read_text(encoding="ascii"))
        except FileNotFoundError:
            _sleep(PUB_POLL_S)
    return json.loads((Path(home) / PUB_FILE).read_text(encoding="ascii"))


def issue(home: Path, *, now: float | None = None, wait_s: float = 0.0) -> str:
    """Make a new code (replaces any older one) and return it for display in the owner's terminal only.
    wait_s: how long to wait for a just-started server to publish its key (open uses OPEN_WAIT_S)."""
    try:
        published = _read_pub(home, wait_s)  # read once: key and pid of ONE server
        peer = X25519PublicKey.from_public_bytes(bytes.fromhex(published["pub"]))
        pid = int(published.get("pid") or 0)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise UnlockUnavailable("start the dashboard first (jarvis workflow open), then run unlock again") from exc
    # pid <= 0 is no publisher (POSIX kill(-1, 0) would even report alive).
    # A crashed server never withdrew its key: a code for it could never be checked.
    if pid <= 0 or not _alive(pid):
        raise UnlockUnavailable("the dashboard that published the unlock key is not running; start it (jarvis workflow open), then run unlock again")
    raw = f"{secrets.randbelow(10000):04d}"  # owner decision: 4 digits, like the autopilot grant prompt
    eph_key = X25519PrivateKey.generate()
    eph = eph_key.public_key().public_bytes_raw()
    tag = _tag(eph_key.exchange(peer), eph, raw)
    record = {"eph": eph.hex(), "tag": tag.hex(), "expires": (now or time.time()) + CODE_TTL_S}
    for attempt in (1, 2):  # Windows: os.replace fails while the server has unlock.json open for reading; retry once
        try:
            _write(Path(home) / FILE, record)
            break
        except OSError as exc:
            if attempt == 2:
                raise UnlockUnavailable(f"could not save the unlock code ({exc.__class__.__name__}); run unlock again") from exc
            _sleep(RETRY_PAUSE_S)
    return raw


def consume(home: Path, code: str, key: ServerKey, *, now: float | None = None) -> bool:
    """True once for a correct, unexpired code checked with the server's in-memory key (the record is then removed);
    an expired record is removed too."""
    path = Path(home) / FILE
    try:
        record = json.loads(path.read_text(encoding="ascii"))
        eph, want, expires = bytes.fromhex(record["eph"]), bytes.fromhex(record["tag"]), float(record["expires"])
        shared = key.shared(eph)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if (now or time.time()) > expires:
        path.unlink(missing_ok=True)
        return False
    if len(normalize(code)) != 4 or not hmac.compare_digest(_tag(shared, eph, code), want):
        return False
    path.unlink(missing_ok=True)
    return True
