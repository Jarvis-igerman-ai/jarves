"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). The code is never stored. The running server keeps an X25519 private key in memory only and publishes
its public key (unlock_pub.json). `issue` writes an ephemeral public key plus HMAC(shared secret, code): a file reader
sees no value that can confirm a guess offline, so token-plus-files callers can only guess online (5 tries, then the
code is cancelled). Honest limit: an agent that can run code as the owner's OS user can still call `issue` itself
(issue #750); the CLI refuses agent environments and non-terminals (owner_only_guard)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

CODE_TTL_S = 300      # how long a shown code can be typed
UNLOCK_TTL_S = 7 * 86400  # default session unlock: 1 week (owner decision 2 Nov; was 8 h); policy watchdog.owner_unlock_s overrides
FILE = "unlock.json"
PUB_FILE = "unlock_pub.json"  # the running server's public key; its private key never leaves server memory


class UnlockUnavailable(RuntimeError):
    """No usable server key: the dashboard is not running (or its key file is damaged)."""


class ServerKey:
    """X25519 key of one running dashboard server; the private half exists only in this object."""

    def __init__(self) -> None:
        self._key = X25519PrivateKey.generate()

    def publish(self, home: Path) -> None:
        pub = self._key.public_key().public_bytes_raw().hex()
        _write_private(Path(home) / PUB_FILE, json.dumps({"pub": pub}))

    def shared(self, peer_hex: str) -> bytes:
        return self._key.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(peer_hex)))


def _write_private(path: Path, text: str) -> None:
    """Owner-only file, replaced atomically (unique temp file + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _tag(shared: bytes, code: str) -> bytes:
    return hmac.new(shared, normalize(code).encode("ascii"), hashlib.sha256).digest()


def issue(home: Path, *, now: float | None = None) -> str:
    """Make a new code (replaces any older one) and return it for display in the owner's terminal only.

    Raises UnlockUnavailable when no running server has published a valid key."""
    try:
        peer = X25519PublicKey.from_public_bytes(bytes.fromhex(json.loads((Path(home) / PUB_FILE).read_text(encoding="ascii"))["pub"]))
        eph = X25519PrivateKey.generate()
        shared = eph.exchange(peer)          # ValueError for a low-order (all-zero shared secret) key
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnlockUnavailable("no running dashboard key; start the dashboard first (jarvis workflow open)") from exc
    raw = f"{secrets.randbelow(10000):04d}"  # owner decision: 4 digits, like the autopilot grant prompt
    record = {"eph": eph.public_key().public_bytes_raw().hex(), "tag": _tag(shared, raw).hex(),
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
    if not hmac.compare_digest(_tag(shared, code), want):
        return False
    path.unlink(missing_ok=True)
    return True
