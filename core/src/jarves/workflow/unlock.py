"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). The code is never stored. The record in unlock.json is an HMAC of the code under an X25519 shared
secret with the running server's key: its private half lives only in server memory, its public half in unlock.pub.
An agent that reads every file in the workflow home therefore cannot test the 10 000 codes offline; online it gets 5
tries in total. Short expiry, single use. Honest limit: a same-user process that WRITES unlock.pub or unlock.json can
still plant its own code (needs OS-level separation, not files). The CLI refuses agent environments and non-terminals
(owner_only_guard)."""
from __future__ import annotations

import hmac
import json
import logging
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
PUB = "unlock.pub"     # public half of the server's in-memory key; written by the server at start


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _mac(shared: bytes, code: str, expires: float) -> bytes:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"jarves-workflow-unlock-v2").derive(shared)
    return hmac.new(key, f"{normalize(code)}|{expires!r}".encode("utf-8"), "sha256").digest()


def server_key(home: Path) -> X25519PrivateKey:
    """New per-process key for the dashboard server; only the public half is written (owner-only, atomic).
    A clean stop removes unlock.pub (release_key), so an existing file means another live server or a crash: warn once."""
    key = X25519PrivateKey.generate()
    path = Path(home) / PUB
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        logging.getLogger(__name__).warning("replacing %s of another dashboard server (still running or not stopped "
                                            "cleanly); unlock codes for that server stop working", PUB)
    tmp = path.with_name(f"{PUB}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(key.public_key().public_bytes_raw().hex())
    os.replace(tmp, path)
    return key


def release_key(home: Path, key: X25519PrivateKey) -> None:
    """On server stop: remove unlock.pub if it is still ours, so `unlock` says the dashboard is not running (#750)."""
    path = Path(home) / PUB
    try:
        if path.read_text(encoding="ascii").strip() == key.public_key().public_bytes_raw().hex():
            path.unlink()
    except (OSError, UnicodeDecodeError):
        pass  # already gone or replaced by another server: nothing of ours to remove


def issue(home: Path, *, now: float | None = None) -> str:
    """Make a new code (replaces any older one) and return it for display in the owner's terminal only.
    FileNotFoundError: the dashboard server is not running (no unlock.pub); ValueError: unlock.pub is damaged."""
    try:
        server_pub = X25519PublicKey.from_public_bytes(bytes.fromhex((Path(home) / PUB).read_text(encoding="ascii").strip()))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{PUB} is damaged; restart the dashboard server") from exc
    raw = f"{secrets.randbelow(10000):04d}"  # owner decision: 4 digits, like the autopilot grant prompt
    eph = X25519PrivateKey.generate()  # discarded after this call: nobody but the server can rebuild the secret
    expires = float((now or time.time()) + CODE_TTL_S)  # consume() rebuilds the MAC from float(record['expires'])
    record = {"salt": eph.public_key().public_bytes_raw().hex(), "hash": _mac(eph.exchange(server_pub), raw, expires).hex(),
              "expires": expires}
    path = Path(home) / FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        json.dump(record, f)
    return raw


def consume(home: Path, code: str, *, key: X25519PrivateKey | None = None, now: float | None = None) -> bool:
    """True once for a correct, unexpired code checked with the server's in-memory key (the record is then removed);
    an expired record is removed too. Without the key nothing can be checked (#750)."""
    path = Path(home) / FILE
    try:
        record = json.loads(path.read_text(encoding="ascii"))
        epk, want, expires = bytes.fromhex(record["salt"]), bytes.fromhex(record["hash"]), float(record["expires"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    if (now or time.time()) > expires:
        path.unlink(missing_ok=True)
        return False
    if key is None or len(normalize(code)) != 4:
        return False
    try:
        shared = key.exchange(X25519PublicKey.from_public_bytes(epk))
    except ValueError:  # wrong length or a low-order point
        return False
    if not hmac.compare_digest(_mac(shared, code, expires), want):
        return False
    path.unlink(missing_ok=True)
    return True
