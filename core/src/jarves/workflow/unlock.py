"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). Nothing in the workflow home can check a code offline: each dashboard makes an X25519 key whose
private half lives only in its memory and publishes the public half (unlock_pub.json). `issue` stores only an
ephemeral public key and HMAC(HKDF(ECDH), code), so a file reader cannot test the 10,000 codes; only the running
dashboard can (5 tries a minute, 5 wrong tries cancel the code). Honest limit: write access to the workflow home is
equivalent to issuing a code (anyone can encrypt a code to the public key and write unlock.json), so an agent that
can write there or runs code as the owner's OS user can still make one; closing that needs an OS consent prompt. One dashboard per home is the normal
case (web-check); a second one on another port takes the key over, so codes for the first fail closed. Windows: 0o600
sets no owner-only ACL (the files hold no secret). The CLI refuses agent environments and non-terminals."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .opener import listening

CODE_TTL_S = 300      # how long a shown code can be typed
UNLOCK_TTL_S = 7 * 86400  # default session unlock: 1 week (owner decision 2 Nov; was 8 h); policy watchdog.owner_unlock_s overrides
FILE = "unlock.json"
PUB_FILE = "unlock_pub.json"


class UnlockUnavailable(RuntimeError):
    """No running dashboard has published a usable unlock key."""


class ServerKey:
    """A dashboard's key pair; the private half is never written anywhere."""

    def __init__(self) -> None:
        self._private = X25519PrivateKey.generate()
        self.pub = self._private.public_key().public_bytes_raw()

    def shared(self, eph: bytes) -> bytes:
        return self._private.exchange(X25519PublicKey.from_public_bytes(eph))  # ValueError: bad or low-order key


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _tag(shared: bytes, pub: bytes, eph: bytes, code: str) -> bytes:
    key = HKDF(hashes.SHA256(), 32, salt=None, info=b"jarves-unlock-v2" + pub + eph).derive(shared)
    return hmac.new(key, normalize(code).encode("ascii"), hashlib.sha256).digest()


def _write(home: Path, name: str, record: dict) -> None:
    """Atomic write: unique 0o600 temp file in the home, then os.replace (no torn or shared temp file)."""
    Path(home).mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=home, prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            json.dump(record, f)
        os.replace(tmp, Path(home) / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(home: Path, name: str) -> dict:
    record = json.loads((Path(home) / name).read_text(encoding="ascii"))
    if not isinstance(record, dict):
        raise ValueError(f"{name} is not an object")
    return record


def publish(home: Path, key: ServerKey, port: int) -> None:
    _write(home, PUB_FILE, {"pub": key.pub.hex(), "port": int(port)})


def withdraw(home: Path, key: ServerKey) -> None:
    """Remove the published key only if it is still this dashboard's own."""
    try:
        if _read(home, PUB_FILE).get("pub") == key.pub.hex():
            (Path(home) / PUB_FILE).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass  # missing or damaged: nothing of ours to withdraw


def issue(home: Path, *, now: float | None = None) -> str:
    """Make a new code (replaces any older one) for the running dashboard; shown in the owner's terminal only."""
    try:
        published = _read(home, PUB_FILE)
        pub, port = bytes.fromhex(published["pub"]), int(published["port"])
        eph_private = X25519PrivateKey.generate()
        eph = eph_private.public_key().public_bytes_raw()
        shared = eph_private.exchange(X25519PublicKey.from_public_bytes(pub))
    except FileNotFoundError:
        raise UnlockUnavailable("no dashboard is running; start it with: jarvis workflow supervise") from None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnlockUnavailable(f"the dashboard unlock key is damaged ({type(exc).__name__}); restart the dashboard") from None
    if not listening(port):
        raise UnlockUnavailable(f"no dashboard is running on port {port}; start it with: jarvis workflow supervise")
    raw = f"{secrets.randbelow(10000):04d}"  # owner decision: 4 digits, like the autopilot grant prompt
    _write(home, FILE, {"eph": eph.hex(), "tag": _tag(shared, pub, eph, raw).hex(),
                        "expires": (now or time.time()) + CODE_TTL_S})
    return raw


def consume(home: Path, code: str, *, key: ServerKey | None, now: float | None = None) -> bool:
    """True once for a correct, unexpired code checked with the dashboard's in-memory key (the record is then removed)."""
    path = Path(home) / FILE
    if key is None:
        return False
    try:
        record = _read(home, FILE)
        eph, want, expires = bytes.fromhex(record["eph"]), bytes.fromhex(record["tag"]), float(record["expires"])
        shared = key.shared(eph)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if (now or time.time()) > expires:
        path.unlink(missing_ok=True)
        return False
    if len(normalize(code)) != 4 or not hmac.compare_digest(_tag(shared, key.pub, eph, code), want):
        return False
    path.unlink(missing_ok=True)
    return True
