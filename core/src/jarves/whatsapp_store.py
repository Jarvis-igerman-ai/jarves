"""Encrypted at-rest store of the WhatsApp bridge (SW-V4 F, PROTOCOL.md section 6), Python side.

The Go bridge seals its in-memory SQLite (device keys + messages) this way; the fake bridge seals its JSON state the
same way, so the Core's tests exercise the real format: `store.enc` = b"JWS1" + 12-byte nonce + AES-256-GCM
(ciphertext + tag) with additional data b"jarves-wa-store-v1"; the random 32-byte key is wrapped by Windows DPAPI
(CryptProtectData, CurrentUser scope, entropy b"jarves-wa-store-v1") in `store.key`. A copied directory does not
decrypt for another Windows user or PC (StoreLocked). Off Windows the key file is plain 0600 (key_protection "file").

AES-GCM: the `cryptography` package (already installed with pyjwt[crypto]). DPAPI: ctypes on crypt32.dll.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

MAGIC = b"JWS1"
AAD = b"jarves-wa-store-v1"
ENTROPY = b"jarves-wa-store-v1"


class StoreLocked(Exception):
    """The store cannot be opened here (wrong user/PC, tampered, or the key is gone). Never carries key material."""

    code = "WA_STORE_LOCKED"


# ---------------------------------------------------------------------- DPAPI (Windows)
def dpapi_available() -> bool:
    return sys.platform == "win32"


def _blob_cls():
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    return Blob


def _call(fn_name: str, data: bytes, entropy: bytes) -> bytes:
    import ctypes
    Blob = _blob_cls()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32

    def mk(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf
    src, _keep1 = mk(data)
    ent, _keep2 = mk(entropy)
    out = Blob()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    # same signature for both: (in, description, entropy, reserved, prompt, flags, out)
    ok = getattr(crypt32, fn_name)(ctypes.byref(src), None, ctypes.byref(ent), None, None, CRYPTPROTECT_UI_FORBIDDEN,
                                   ctypes.byref(out))
    if not ok:
        raise StoreLocked("DPAPI refused the key (another Windows user or PC?)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(data: bytes, entropy: bytes = ENTROPY) -> bytes:
    return _call("CryptProtectData", data, entropy)


def unprotect(blob: bytes, entropy: bytes = ENTROPY) -> bytes:
    return _call("CryptUnprotectData", blob, entropy)


# ---------------------------------------------------------------------- AES-256-GCM
def seal(plain: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plain, AAD)


def unseal(blob: bytes, key: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(blob) < 4 + 12 + 16 or blob[:4] != MAGIC:
        raise StoreLocked("not a JARVES WhatsApp store")
    try:
        return AESGCM(key).decrypt(blob[4:16], blob[16:], AAD)
    except InvalidTag:
        raise StoreLocked("the store does not decrypt with this key (copied or tampered)") from None


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class SecureStore:
    """store.enc + store.key in the bridge directory. `load()` -> bytes | None (no store yet); `save(bytes)`."""

    def __init__(self, directory: Path, *, use_dpapi: bool | None = None) -> None:
        self.dir = Path(directory)
        self.key_path, self.path = self.dir / "store.key", self.dir / "store.enc"
        self.use_dpapi = dpapi_available() if use_dpapi is None else bool(use_dpapi)
        self._key: bytes | None = None

    @property
    def key_protection(self) -> str:
        return "dpapi" if self.use_dpapi else "file"

    def _load_key(self, create: bool) -> bytes | None:
        if self._key is not None:
            return self._key
        if self.key_path.exists():
            raw = self.key_path.read_bytes()
            key = unprotect(raw) if self.use_dpapi else raw
            if len(key) != 32:
                raise StoreLocked("the store key is damaged")
            self._key = key
        elif create:
            key = secrets.token_bytes(32)
            _write_private(self.key_path, protect(key) if self.use_dpapi else key)
            self._key = key
        return self._key

    def load(self) -> bytes | None:
        if not self.path.exists():
            return None
        key = self._load_key(create=False)
        if key is None:
            raise StoreLocked("the store key is missing")
        return unseal(self.path.read_bytes(), key)

    def save(self, plain: bytes) -> None:
        _write_private(self.path, seal(plain, self._load_key(create=True)))

    def wipe(self) -> None:
        self._key = None
        for p in (self.path, self.key_path):
            try:
                p.unlink()
            except OSError:
                pass
