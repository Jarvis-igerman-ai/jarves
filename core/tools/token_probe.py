"""Restricted-token feasibility probe for helper isolation (SW-V4 L study, docs/reviews/SWV4_L_INSTALLER.md section 8).

NOT wired into the product. It answers one question with a real Windows process: when a child is started with a restricted
copy of the owner's token, which files can it still read or write? That decides whether `isolation.popen` can hide
`<home>` (answer.key, secrets.env) from an approved helper command, the open item of R-01.

    python tools/token_probe.py                 run the probe in a throw-away folder and print a table (Windows only)

Variants (all are restricted COPIES of the caller's own primary token, so CreateProcessAsUserW needs no extra privilege):
    plain             a duplicate, no restriction (baseline)
    deny_user         the user SID and the Administrators group become deny-only, all privileges removed except
                      SeChangeNotifyPrivilege (CreateRestrictedToken DISABLE_MAX_PRIVILEGE): files whose ACL grants access
                      only through the user SID can no longer be read OR written; files readable by Everyone / Users stay
                      readable
    write_restricted  WRITE_RESTRICTED with the restricting SIDs Everyone + the logon SID: reads are unchanged, writes need
                      BOTH checks to pass (a second access check against the restricting SIDs)
Nothing here touches the registry, PATH, an installed JARVES or the network; it creates temp files and runs `cmd.exe /c`.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

if sys.platform == "win32":
    from ctypes import wintypes as wt

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    TOKEN_ASSIGN_PRIMARY, TOKEN_DUPLICATE, TOKEN_QUERY = 0x1, 0x2, 0x8
    TokenUser = 1
    DISABLE_MAX_PRIVILEGE, WRITE_RESTRICTED = 0x1, 0x8
    DETACHED_PROCESS = 0x00000008          # CREATE_NO_WINDOW (0x08000000) fails under these tokens: the console host cannot start (0xC0000142)
    INFINITE_WAIT = 0xFFFFFFFF
    WAIT_TIMEOUT = 0x102

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wt.DWORD)]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                    ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                    ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                    ("hStdError", wt.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]

    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    advapi32.ConvertStringSidToSidW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.CreateRestrictedToken.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                               wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.HANDLE)]
    advapi32.DuplicateTokenEx.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(wt.HANDLE)]
    advapi32.CreateProcessAsUserW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                              wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                                              ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

VARIANTS = ("plain", "deny_user", "write_restricted")


class ProbeError(RuntimeError):
    pass


def _check(ok, what: str):
    if not ok:
        raise ProbeError(f"{what} failed (Win32 error {ctypes.get_last_error()})")


def _own_token() -> int:
    tok = wt.HANDLE()
    _check(advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_DUPLICATE | TOKEN_QUERY | TOKEN_ASSIGN_PRIMARY,
                                     ctypes.byref(tok)), "OpenProcessToken")
    return tok.value


def _user_sid_buffer(token: int):
    need = wt.DWORD()
    advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(need))
    buf = ctypes.create_string_buffer(need.value)
    _check(advapi32.GetTokenInformation(token, TokenUser, buf, need, ctypes.byref(need)), "GetTokenInformation")
    return buf


def _sid(text: str) -> ctypes.c_void_p:
    p = ctypes.c_void_p()
    _check(advapi32.ConvertStringSidToSidW(text, ctypes.byref(p)), f"ConvertStringSidToSid({text})")
    return p


def restricted_token(variant: str) -> int:
    """A primary token for the variant (caller closes it with CloseHandle)."""
    src = _own_token()
    out = wt.HANDLE()
    try:
        if variant == "plain":
            _check(advapi32.DuplicateTokenEx(src, 0xF01FF, None, 2, 1, ctypes.byref(out)), "DuplicateTokenEx")  # impersonation->primary
            return out.value
        if variant == "deny_user":
            ubuf = _user_sid_buffer(src)
            user = ctypes.cast(ubuf, ctypes.POINTER(TOKEN_USER)).contents.User
            admins = _sid("S-1-5-32-544")
            arr = (SID_AND_ATTRIBUTES * 2)(SID_AND_ATTRIBUTES(user.Sid, 0), SID_AND_ATTRIBUTES(admins, 0))
            _check(advapi32.CreateRestrictedToken(src, DISABLE_MAX_PRIVILEGE, 2, arr, 0, None, 0, None, ctypes.byref(out)),
                   "CreateRestrictedToken(deny_user)")
            kernel32.LocalFree(admins)
            return out.value
        if variant == "write_restricted":
            everyone = _sid("S-1-1-0")
            arr = (SID_AND_ATTRIBUTES * 1)(SID_AND_ATTRIBUTES(everyone, 0))
            _check(advapi32.CreateRestrictedToken(src, WRITE_RESTRICTED, 0, None, 0, None, 1, arr, ctypes.byref(out)),
                   "CreateRestrictedToken(write_restricted)")
            kernel32.LocalFree(everyone)
            return out.value
        raise ValueError(variant)
    finally:
        kernel32.CloseHandle(src)


def run_as(variant: str, command_line: str, *, cwd: str | None = None, timeout_s: float = 20) -> int:
    """Run `command_line` with the variant's token; the exit code (-1 = could not start, -2 = timeout)."""
    tok = restricted_token(variant)
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    try:
        cmd = ctypes.create_unicode_buffer(command_line)
        ok = advapi32.CreateProcessAsUserW(tok, None, cmd, None, None, False, DETACHED_PROCESS, None,
                                           cwd or os.environ.get("SystemRoot", r"C:\Windows"), ctypes.byref(si),
                                           ctypes.byref(pi))
        if not ok:
            return -1
        try:
            if kernel32.WaitForSingleObject(pi.hProcess, int(timeout_s * 1000)) == WAIT_TIMEOUT:
                kernel32.TerminateProcess(pi.hProcess, 1)
                return -2
            code = wt.DWORD()
            kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
            return int(code.value)
        finally:
            kernel32.CloseHandle(pi.hProcess)
            kernel32.CloseHandle(pi.hThread)
    finally:
        kernel32.CloseHandle(tok)


def _icacls(path: Path, *args: str) -> None:
    subprocess.run(["icacls", str(path), *args], check=True, capture_output=True, timeout=30)


def lock_to_owner(path: Path, also_everyone_read: bool = False) -> None:
    """ACL = this user (full control) [+ Everyone read]; inheritance off. Stands for <home>/answer.key (owner only) and
    for the program folder (readable by others)."""
    _icacls(path, "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:(F)")
    if also_everyone_read:
        _icacls(path, "/grant", "*S-1-1-0:(R)")


def new_dir(parent) -> Path:
    """A fresh folder that INHERITS the parent's ACL (tempfile.mkdtemp makes it owner-only on Python 3.12+, which would
    hide the "everyone-readable" file from the restricted child)."""
    d = Path(parent) / f"jarves-token-probe-{uuid.uuid4().hex[:8]}"
    d.mkdir()
    return d


def probe(workdir: Path | None = None) -> list[dict]:
    """The matrix, run with `cmd.exe` under every variant, in a folder OUTSIDE the user profile (default %PUBLIC%: restricted
    tokens cannot even traverse the profile root, see the study): can it start, read an owner-only file (stands for
    answer.key), read an everyone-readable file (stands for the program folder) and write an owner-only file?
    Rows: {variant, start, read_owner_only, read_world, read_world_in_profile, write_owner_only}."""
    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    parent = os.environ.get("PUBLIC", r"C:\Users\Public")
    base = Path(workdir) if workdir else new_dir(parent)
    base.mkdir(parents=True, exist_ok=True)
    secret, world, target = base / "answer.key", base / "program.txt", base / "write_target.txt"
    for f in (secret, world, target):
        f.write_text("x", encoding="utf-8")
    lock_to_owner(secret)
    lock_to_owner(world, also_everyone_read=True)
    lock_to_owner(target)
    inside = Path(tempfile.gettempdir()) / f"jarves-token-probe-{uuid.uuid4().hex[:8]}"   # INSIDE the user profile
    inside.mkdir()
    in_profile = inside / "program.txt"
    in_profile.write_text("x", encoding="utf-8")
    lock_to_owner(in_profile, also_everyone_read=True)
    rows = []
    for v in VARIANTS:
        started = run_as(v, f'"{cmd}" /c exit 0') == 0
        rows.append({"variant": v, "start": started,
                     "read_owner_only": run_as(v, f'"{cmd}" /c type "{secret}" >nul 2>nul') == 0,
                     "read_world": run_as(v, f'"{cmd}" /c type "{world}" >nul 2>nul') == 0,
                     "read_world_in_profile": run_as(v, f'"{cmd}" /c type "{in_profile}" >nul 2>nul') == 0,
                     "write_owner_only": run_as(v, f'"{cmd}" /c echo y>>"{target}" 2>nul') == 0})
    cleanup(inside)
    return rows


def cleanup(base: Path) -> None:
    for f in base.glob("*"):
        try:
            _icacls(f, "/grant", f"{os.environ['USERNAME']}:(F)")
            f.unlink()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        base.rmdir()
    except OSError:
        pass


def main() -> int:
    if sys.platform != "win32":
        print("token_probe: Windows only")
        return 0
    base = new_dir(os.environ.get("PUBLIC", r"C:\Users\Public"))
    try:
        rows = probe(base)
    finally:
        cleanup(base)
    print(f"{'variant':<18}{'starts':<8}{'read owner-only':<17}{'read world':<12}{'read world in profile':<23}{'write owner-only'}")
    for r in rows:
        print(f"{r['variant']:<18}{r['start']!s:<8}{r['read_owner_only']!s:<17}{r['read_world']!s:<12}"
              f"{r['read_world_in_profile']!s:<23}{r['write_owner_only']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
