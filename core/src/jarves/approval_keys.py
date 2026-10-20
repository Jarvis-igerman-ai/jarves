"""Per-profile approval key (review C, R-01 defence, Issue #17): signs answer files and hides typed codes.

The Core (and only the Core) creates a fresh random key on every start in <home>/approvals/answer.key. The file is
private to the user: mode 0600 on POSIX; on Windows a protected DACL with ONE entry (the current user, full control),
no inherited entries, so other accounts and services without backup privileges cannot read it. The dashboard and the
CLI of the same user read it to sign the answers they write; the key is never put in the environment, a log line or
any other file.

What it is used for (approvals.py):
- answers/<id>.json written by the dashboard / CLI carry an HMAC-SHA256 over their content; the Core ignores
  unsigned or wrongly signed files (one quiet log line).
- pending.json no longer holds the typed confirmation code in plain text: it holds a salt, a keyed hash
  HMAC(key, salt, code) for checking a typed code, and the code sealed with a keystream derived from the key (so the
  dashboard / CLI of the owner can still SHOW the code; a reader of pending.json alone cannot).

Honest limit: a program running as the same user CAN read the key file (an ACL cannot tell two processes of one user
apart). This closes the plain-text code in pending.json and forged answers from anything that can write but not read
the key (a mistaken write, a sync tool, another account), and it makes a forger read and use a second, rotating file.
The OS-level fix (restricted token / AppContainer / separate uid for helper commands) is documented in
docs/reviews/SWV4_R01_DEFENSE.md.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

KEY_NAME = "answer.key"
KEY_BYTES = 32


def key_path(home: Path) -> Path:
    return Path(home) / "approvals" / KEY_NAME


# ---------------------------------------------------------------------- Windows: a private DACL
def _win_user_sid() -> str:
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.LocalFree.argtypes = [wintypes.HLOCAL]
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    tok = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):     # TOKEN_QUERY
        raise OSError(ctypes.get_last_error(), "OpenProcessToken")
    try:
        need = wintypes.DWORD()
        adv.GetTokenInformation(tok, 1, None, 0, ctypes.byref(need))                      # TokenUser
        buf = ctypes.create_string_buffer(need.value)
        if not adv.GetTokenInformation(tok, 1, buf, need, ctypes.byref(need)):
            raise OSError(ctypes.get_last_error(), "GetTokenInformation")
        psid = ctypes.cast(buf, ctypes.POINTER(wintypes.LPVOID))[0]                      # TOKEN_USER.User.Sid
        s = wintypes.LPWSTR()
        if not adv.ConvertSidToStringSidW(psid, ctypes.byref(s)):
            raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW")
        try:
            return str(s.value)
        finally:
            k32.LocalFree(s)
    finally:
        k32.CloseHandle(tok)


def _win_restrict(path: Path) -> None:
    """Replace the file's DACL with a protected one: the current user, full control; nothing inherited."""
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LocalFree.argtypes = [wintypes.HLOCAL]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.ULONG)]
    adv.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL),
                                              ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, wintypes.LPVOID,
                                          wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
    sddl = f"D:P(A;;FA;;;{_win_user_sid()})"
    psd = wintypes.LPVOID()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(psd), None):
        raise OSError(ctypes.get_last_error(), "ConvertStringSecurityDescriptorToSecurityDescriptorW")
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
        if not adv.GetSecurityDescriptorDacl(psd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise OSError(ctypes.get_last_error(), "GetSecurityDescriptorDacl")
        DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION = 0x4, 0x80000000
        rc = adv.SetNamedSecurityInfoW(str(path), 1, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                                       None, None, dacl, None)
        if rc != 0:
            raise OSError(rc, "SetNamedSecurityInfoW")
    finally:
        k32.LocalFree(psd)


def win_dacl_sddl(path: Path) -> str:
    """The file's DACL as SDDL (tests and `jarvis doctor` show it to prove the key file is private)."""
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LocalFree.argtypes = [wintypes.HLOCAL]
    adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
    adv.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, wintypes.LPVOID,
                                          wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID,
                                          ctypes.POINTER(wintypes.LPVOID)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG)]
    psd = wintypes.LPVOID()
    rc = adv.GetNamedSecurityInfoW(str(path), 1, 0x4, None, None, None, None, ctypes.byref(psd))
    if rc != 0:
        raise OSError(rc, "GetNamedSecurityInfoW")
    try:
        s = wintypes.LPWSTR()
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(psd, 1, 0x4, ctypes.byref(s), None):
            raise OSError(ctypes.get_last_error(), "ConvertSecurityDescriptorToStringSecurityDescriptorW")
        try:
            return str(s.value)
        finally:
            k32.LocalFree(s)
    finally:
        k32.LocalFree(psd)


def current_user_sid() -> str:
    return _win_user_sid() if sys.platform == "win32" else str(os.getuid())


# ---------------------------------------------------------------------- the key file
def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        if sys.platform == "win32":
            _win_restrict(tmp)                  # private BEFORE the key is written into it
        os.write(fd, data)
    except BaseException:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    for i in range(10):                         # a same-volume move keeps the protected DACL
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                 # Windows: a reader holds the old file for a moment
            if i == 9:
                tmp.unlink(missing_ok=True)
                raise
            import time
            time.sleep(0.02)


def rotate(home: Path) -> bytes:
    """A new random key (the Core does this on every start). Raises OSError when it cannot be made private."""
    key = secrets.token_bytes(KEY_BYTES)
    _write_private(key_path(home), key.hex().encode("ascii"))
    return key


def load(home: Path) -> bytes | None:
    try:
        raw = key_path(home).read_text(encoding="ascii").strip()
        key = bytes.fromhex(raw)
    except (OSError, ValueError):
        return None
    return key if len(key) == KEY_BYTES else None


def load_or_create(home: Path) -> bytes | None:
    key = load(home)
    if key is None:
        try:
            key = rotate(home)
        except OSError:
            return None
    return key


# ---------------------------------------------------------------------- signing and sealing
def _canon(d: dict) -> bytes:
    body = {k: d.get(k) for k in ("id", "approved", "typed", "channel", "ts", "nonce")}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(key: bytes, d: dict) -> dict:
    out = dict(d)
    out["sig"] = hmac.new(key, b"answer|" + _canon(out), hashlib.sha256).hexdigest()
    return out


def verify(key: bytes | None, d: dict) -> bool:
    sig = d.get("sig") if isinstance(d, dict) else None
    if not key or not isinstance(sig, str):
        return False
    return hmac.compare_digest(hmac.new(key, b"answer|" + _canon(d), hashlib.sha256).hexdigest(), sig)


def code_hash(key: bytes, salt: str, code: str) -> str:
    return hmac.new(key, f"code|{salt}|{str(code).strip()}".encode("utf-8"), hashlib.sha256).hexdigest()


def code_matches(key: bytes | None, salt: str, digest: str, typed: str) -> bool:
    if not key or not salt or not digest:
        return False
    return hmac.compare_digest(code_hash(key, salt, typed), str(digest))


def _stream(key: bytes, salt: str, n: int) -> bytes:
    return hmac.new(key, f"seal|{salt}".encode("utf-8"), hashlib.sha256).digest()[:n]


def seal(key: bytes, salt: str, code: str) -> str:
    raw = code.encode("utf-8")[:32]
    return bytes(a ^ b for a, b in zip(raw, _stream(key, salt, len(raw)))).hex()


def unseal(key: bytes | None, salt: str, sealed: str) -> str:
    if not key or not salt or not sealed:
        return ""
    try:
        raw = bytes.fromhex(sealed)
    except ValueError:
        return ""
    try:
        return bytes(a ^ b for a, b in zip(raw, _stream(key, salt, len(raw)))).decode("utf-8")
    except UnicodeDecodeError:
        return ""
