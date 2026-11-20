"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). The code is never stored, and the files give no offline oracle for it (#750): each dashboard holds an
X25519 key whose private half lives only in its memory and publishes the public half (unlock_pub.json). issue() stores
an ephemeral public key and HMAC(HKDF(ECDH), code); only the live dashboard can check a guess, online, under its
5-per-minute / 5-wrong-cancel limits. Short expiry, single use. Honest limit: an agent that can WRITE the home or run
code as the owner's OS user can still mint a code; closing that needs an OS consent prompt. A key left by a crashed
or older dashboard yields a dead code (fails closed) until the next dashboard publishes. On Windows 0o600 sets no
owner-only ACL; the files hold only public values. The CLI refuses agent environments and non-terminals (owner_only_guard)."""
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

CODE_TTL_S = 300      # how long a shown code can be typed
UNLOCK_TTL_S = 7 * 86400  # default session unlock: 1 week (owner decision 2 Nov; was 8 h); policy watchdog.owner_unlock_s overrides
FILE = "unlock.json"
PUB_FILE = "unlock_pub.json"  # the live dashboard's public key; the private half is never written


class UnlockUnavailable(RuntimeError):
    """No usable dashboard key is published: start (or restart) the dashboard, then ask for a code."""


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


class ServerKey:
    """The dashboard's in-memory X25519 key (#750)."""

    def __init__(self) -> None:
        self._key = X25519PrivateKey.generate()
        self.pub = self._key.public_key().public_bytes_raw()

    def publish(self, home: Path) -> None:
        """Atomically replace unlock_pub.json (unique temp file + os.replace) with this key's public half."""
        path = Path(home) / PUB_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{PUB_FILE}.{secrets.token_hex(8)}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as f:
                json.dump({"pub": self.pub.hex()}, f)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def shared(self, eph: bytes) -> bytes:
        return self._key.exchange(X25519PublicKey.from_public_bytes(eph))  # ValueError for a malformed or low-order key


def _tag(shared: bytes, eph: bytes, pub: bytes, code: str) -> bytes:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"jarves-unlock-v1" + eph + pub).derive(shared)
    return hmac.new(key, normalize(code).encode("ascii"), hashlib.sha256).digest()


def _published(home: Path) -> bytes:
    try:
        pub = bytes.fromhex(json.loads((Path(home) / PUB_FILE).read_text(encoding="ascii"))["pub"])
        X25519PublicKey.from_public_bytes(pub)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnlockUnavailable("no dashboard unlock key is published; start or restart the dashboard, then run this again") from exc
    return pub


def issue(home: Path, *, now: float | None = None) -> str:
    """Make a new code (replaces any older one) and return it for display in the owner's terminal only."""
    pub = _published(home)
    eph_key = X25519PrivateKey.generate()
    eph = eph_key.public_key().public_bytes_raw()
    try:
        shared = eph_key.exchange(X25519PublicKey.from_public_bytes(pub))
    except ValueError as exc:  # low-order published key
        raise UnlockUnavailable("the published dashboard unlock key is invalid; restart the dashboard") from exc
    raw = f"{secrets.randbelow(10000):04d}"  # owner decision: 4 digits, like the autopilot grant prompt
    record = {"eph": eph.hex(), "tag": _tag(shared, eph, pub, raw).hex(), "expires": (now or time.time()) + CODE_TTL_S}
    path = Path(home) / FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        json.dump(record, f)
    return raw


def consume(home: Path, code: str, *, key: ServerKey | None = None, now: float | None = None) -> bool:
    """True once for a correct, unexpired code checked with the dashboard's in-memory key (the record is then
    removed); an expired record is removed too. Without the key, or for a malformed record, always False."""
    path = Path(home) / FILE
    try:
        record = json.loads(path.read_text(encoding="ascii"))
        eph, want, expires = bytes.fromhex(record["eph"]), bytes.fromhex(record["tag"]), float(record["expires"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if (now or time.time()) > expires:
        path.unlink(missing_ok=True)
        return False
    if key is None or len(normalize(code)) != 4:
        return False
    try:
        shared = key.shared(eph)
    except ValueError:
        return False
    if not hmac.compare_digest(_tag(shared, eph, key.pub, code), want):
        return False
    path.unlink(missing_ok=True)
    return True
