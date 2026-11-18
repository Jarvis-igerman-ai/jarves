"""Owner-presence unlock for the dashboard answer paths (#750).

The private link (access.token) only opens a READ-ONLY session. Answering, deferring, reopening or opening a helper
window needs an unlock code that is shown only in the owner's own terminal (`workflow_console.py unlock`, or `open`
in a terminal). The code is never stored. The record in unlock.json is an HMAC of the code under an X25519 shared
secret with the running server's key: its private half is never written to disk (server memory only), its public half
and port are in unlock.pub.
An agent that reads every file in the workflow home therefore cannot test the 10 000 codes offline; online it gets 5
tries in total. Short expiry, single use. Honest limit: a same-user process that WRITES unlock.pub or unlock.json, or
reads the server process memory, can still plant or check a code (needs OS-level separation, not files; tracked in #1313). The CLI refuses agent environments and non-terminals
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
_RELEASE_GAP_S = 0.05  # issue() re-reads a missing unlock.pub once after this pause (release_key's rename gap)


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def _mac(shared: bytes, code: str, expires: float) -> bytes:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"jarves-workflow-unlock-v2").derive(shared)
    return hmac.new(key, f"{normalize(code)}|{expires!r}".encode("utf-8"), "sha256").digest()


def _read_pub(home: Path, name: str = PUB) -> tuple[str, int | None]:
    """unlock.pub is '<public key hex> <port>'; OSError if missing, ValueError if damaged."""
    parts = (Path(home) / name).read_text(encoding="ascii").split()
    if len(parts) not in (1, 2):
        raise ValueError(f"{PUB} has {len(parts)} fields")
    return parts[0], int(parts[1]) if len(parts) == 2 else None


def server_key(home: Path, port: int | None = None) -> X25519PrivateKey:
    """New per-process key for the dashboard server; only the public half is written (owner-only, atomic).
    A clean stop removes unlock.pub (release_key), so an existing file means another live server or a crash: warn once."""
    key = X25519PrivateKey.generate()
    path = Path(home) / PUB
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        logging.getLogger(__name__).warning("replacing %s of another dashboard server (still running or not stopped "
                                            "cleanly); unlock codes for that server stop working", PUB)
    tmp = path.with_name(f"{PUB}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(key.public_key().public_bytes_raw().hex() + ("" if port is None else f" {int(port)}"))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)  # e.g. a Windows sharing violation on unlock.pub: leave no .tmp behind
        raise
    return key


def release_key(home: Path, key: X25519PrivateKey) -> None:
    """On server stop: remove unlock.pub if it is still ours, so `unlock` says the dashboard is not running (#750).
    The file is renamed away first (atomic), so a key another server writes meanwhile is never the one removed."""
    path = Path(home) / PUB
    held = path.with_name(f"{PUB}.{os.getpid()}.{secrets.token_hex(4)}.release")
    for attempt in (1, 2):
        try:
            os.rename(path, held)  # atomic: a key another server writes from now on lands in a new unlock.pub
            break
        except FileNotFoundError:
            return  # already gone: nothing of ours to remove
        except OSError as exc:  # e.g. a Windows sharing violation while issue() reads the file: try once more
            if attempt == 2:
                logging.getLogger(__name__).warning("could not remove %s on stop: %s", PUB, exc)
                return
            time.sleep(_RELEASE_GAP_S)
    try:
        ours = _read_pub(home, held.name)[0] == key.public_key().public_bytes_raw().hex()
    except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
        ours = False
    if not ours:  # another server's key: put it back unless an even newer one was written meanwhile
        try:
            os.link(held, path)
        except FileExistsError:
            pass  # a newer key is live and wins, like server_key's replace
        except OSError as exc:  # no hard links here (or denied): move it back instead of deleting a live key
            logging.getLogger(__name__).warning("could not link %s back (%s); moving it back instead", PUB, exc)
            if not path.exists():
                try:
                    os.replace(held, path)
                except OSError as err:
                    logging.getLogger(__name__).warning("could not restore %s of another server: %s", PUB, err)
    held.unlink(missing_ok=True)


def issue(home: Path, *, port: int | None = None, now: float | None = None) -> str:
    """Make a new code (replaces any older one) and return it for display in the owner's terminal only.
    FileNotFoundError: the dashboard server is not running (no unlock.pub, or its key belongs to a server on another
    port than `port`); ValueError: unlock.pub is damaged. A missing unlock.pub is read once more after a short pause:
    another server's release_key moves the file away for a moment while it checks whose key it is."""
    try:
        try:
            pub_hex, pub_port = _read_pub(home)
        except FileNotFoundError:
            time.sleep(_RELEASE_GAP_S)
            pub_hex, pub_port = _read_pub(home)
        server_pub = X25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    except ValueError as exc:  # UnicodeDecodeError is a ValueError
        raise ValueError(f"{PUB} is damaged; restart the dashboard server") from exc
    if port is not None and pub_port != port:
        raise FileNotFoundError(f"{PUB} belongs to a dashboard server on port {pub_port}, not {port}")
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
