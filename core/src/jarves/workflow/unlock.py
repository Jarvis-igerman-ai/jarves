"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). The code is never stored: only a salted scrypt hash, with a short expiry, single use. An agent that
can read every file in the workflow home can read the hash; a 4-digit code is brute-forceable offline, so this stops
token-only HTTP callers, not a determined file reader (honest limit, see issue #750). The CLI refuses agent environments and non-terminals (owner_only_guard)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

CODE_TTL_S = 300      # how long a shown code can be typed
UNLOCK_TTL_S = 8 * 3600  # default session unlock (owner decision 1 Nov); policy watchdog.owner_unlock_s overrides
FILE = "unlock.json"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _digest(code: str, salt: bytes) -> bytes:
    return hashlib.scrypt(normalize(code).encode("ascii"), salt=salt, **_SCRYPT)


def issue(home: Path, *, now: float | None = None) -> str:
    """Make a new code (replaces any older one) and return it for display in the owner's terminal only."""
    raw = f"{secrets.randbelow(10000):04d}"  # owner decision: 4 digits, like the autopilot grant prompt
    salt = secrets.token_bytes(16)
    record = {"salt": salt.hex(), "hash": _digest(raw, salt).hex(), "expires": (now or time.time()) + CODE_TTL_S}
    path = Path(home) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        json.dump(record, f)
    return raw


def consume(home: Path, code: str, *, now: float | None = None) -> bool:
    """True once for a correct, unexpired code (the record is then removed); an expired record is removed too."""
    path = Path(home) / FILE
    try:
        record = json.loads(path.read_text(encoding="ascii"))
        salt, want, expires = bytes.fromhex(record["salt"]), bytes.fromhex(record["hash"]), float(record["expires"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if (now or time.time()) > expires:
        path.unlink(missing_ok=True)
        return False
    if len(normalize(code)) != 4 or not hmac.compare_digest(_digest(code, salt), want):
        return False
    path.unlink(missing_ok=True)
    return True
