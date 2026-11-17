"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). The code is never stored. The running server keeps an X25519 private key in memory only and publishes
its public key (unlock_pub.json). `issue` writes an ephemeral public key plus HMAC(shared secret, code): a file reader
sees no value that can confirm a guess offline, so token-plus-files READERS can only guess online (5 tries, then the
code is cancelled). Windows: the 0o600 mode does not restrict other local accounts (no owner-only ACL yet); the files
hold only public keys and a tag, so this leaks no oracle. Honest limit (issue #750): an agent that can WRITE files in the workflow home (it can forge
unlock.json from the public key) or run code as the owner's OS user can still create a code. The CLI refuses agent
environments and non-terminals (owner_only_guard). One key per home: the publishing server holds an OS lock
(unlock_pub.lock, released by the OS even on a crash). A second server of the same home publishes nothing (so its clean
stop withdraws nothing) and retries the lock every few seconds, taking over when the first stops. A key whose lock is free
is stale: no code is issued for it. The CLI issues a code only for the dashboard on the published port."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..runtime.lock import held

CODE_TTL_S = 300      # how long a shown code can be typed
UNLOCK_TTL_S = 7 * 86400  # default session unlock: 1 week (owner decision 2 Nov; was 8 h); policy watchdog.owner_unlock_s overrides
FILE = "unlock.json"
PUB_FILE = "unlock_pub.json"  # the running server's public key; its private key never leaves server memory
KEY_LOCK = "unlock_pub.lock"  # held by the server whose key is published, for its whole life


class UnlockUnavailable(RuntimeError):
    """No usable server key: the dashboard is not running (or its key file is damaged)."""


class ServerKey:
    """X25519 key of one running dashboard server; the private half exists only in this object."""

    def __init__(self) -> None:
        self._key = X25519PrivateKey.generate()

    @property
    def pub(self) -> str:
        return self._key.public_key().public_bytes_raw().hex()

    def publish(self, home: Path, port: int) -> None:
        _write_private(Path(home) / PUB_FILE, json.dumps({"pub": self.pub, "port": port}))

    def withdraw(self, home: Path) -> None:
        """Remove the published key on clean shutdown, only while it is still this server's key.

        Read-then-unlink is not atomic: a racing newer server's key may be lost, which fails closed (no code until it
        restarts); a wrong code can never become valid."""
        path = Path(home) / PUB_FILE
        try:
            if json.loads(path.read_text(encoding="ascii")).get("pub") == self.pub:
                path.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            pass  # missing, damaged or foreign file: leave it; issue() refuses a damaged one

    def shared(self, peer_hex: str) -> bytes:
        return self._key.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(peer_hex)))


def published_port(home: Path) -> int | None:
    """Port of the dashboard whose key is published, or None (missing, damaged or old-format file)."""
    try:
        port = json.loads((Path(home) / PUB_FILE).read_text(encoding="ascii")).get("port")
    except (OSError, ValueError, AttributeError):
        return None
    return port if isinstance(port, int) and not isinstance(port, bool) else None


def key_held(home: Path) -> bool:
    """True while a live server holds the key lock, i.e. the published key has its private half in memory somewhere."""
    return held(Path(home), KEY_LOCK)


REPLACE_TRIES = 5          # Windows: os.replace fails while another process briefly has the target open


def _write_private(path: Path, text: str, *, _sleep=time.sleep) -> None:
    """Owner-only file, replaced atomically (unique temp file + os.replace, bounded retry on PermissionError)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(text)
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise
                _sleep(0.05 * (attempt + 1))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _tag(shared: bytes, eph_hex: str, pub_hex: str, code: str) -> bytes:
    """HMAC-SHA256 of the code under an HKDF-SHA256 key bound to both public keys."""
    info = b"jarves-unlock-v1" + bytes.fromhex(eph_hex) + bytes.fromhex(pub_hex)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(shared)
    return hmac.new(key, normalize(code).encode("ascii"), hashlib.sha256).digest()


def issue(home: Path, *, now: float | None = None) -> str:
    """Make a new code (replaces any older one) and return it for display in the owner's terminal only.

    Raises UnlockUnavailable when no running server has published a valid key."""
    try:
        pub = str(json.loads((Path(home) / PUB_FILE).read_text(encoding="ascii"))["pub"])
        peer = X25519PublicKey.from_public_bytes(bytes.fromhex(pub))
        eph = X25519PrivateKey.generate()
        shared = eph.exchange(peer)          # ValueError for a low-order (all-zero shared secret) key
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnlockUnavailable("no running dashboard key; start the dashboard first (jarvis workflow open)") from exc
    raw = f"{secrets.randbelow(10000):04d}"  # owner decision: 4 digits, like the autopilot grant prompt
    eph_hex = eph.public_key().public_bytes_raw().hex()
    record = {"eph": eph_hex, "tag": _tag(shared, eph_hex, pub, raw).hex(),
              "expires": (now or time.time()) + CODE_TTL_S}
    _write_private(Path(home) / FILE, json.dumps(record))
    return raw


def consume(home: Path, code: str, *, key: ServerKey | None = None, now: float | None = None) -> bool:
    """True once for a correct, unexpired code checked with the server's in-memory key (the record is then removed).

    An expired record is removed too; without the key nothing can be confirmed."""
    path = Path(home) / FILE
    try:
        record = json.loads(path.read_text(encoding="ascii"))
        eph, want, expires = str(record["eph"]), bytes.fromhex(record["tag"]), float(record["expires"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if (now or time.time()) > expires:
        path.unlink(missing_ok=True)
        return False
    if key is None or len(normalize(code)) != 4:
        return False
    try:
        shared = key.shared(eph)
    except ValueError:                       # garbage or low-order ephemeral key: never a match
        return False
    if not hmac.compare_digest(_tag(shared, eph, key.pub, code), want):
        return False
    path.unlink(missing_ok=True)
    return True
