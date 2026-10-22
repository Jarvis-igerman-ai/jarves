"""Restricted-token / low-integrity launch of child processes on Windows (V4 package J, issue #17), ctypes only.

What it adds on top of the Job Object limits (skills/generated.py `_win_job_limit`: memory cap, one process,
kill-on-close): the child runs with
- a RESTRICTED copy of our primary token (CreateRestrictedToken DISABLE_MAX_PRIVILEGE: every privilege except
  SeChangeNotify is stripped), and
- the LOW mandatory integrity level (S-1-16-4096): the child can read what a normal process can read, but cannot
  WRITE to anything the owner's normal (medium) processes own: documents, the state dir, the registry hive of the
  user, other processes (no PROCESS_VM_WRITE / injection into medium processes).

It is behind a flag, OFF by default (`[helper] restricted_token = true` in config.toml; isolation.py reads it), because a low-integrity child cannot write into the owner's normal folders: a helper command like `pip install`
or `git commit` would fail there by design. Honest limits: reads and network are NOT restricted (that needs an
AppContainer), and a child that can already write to low-integrity locations (%LOCALAPPDATA%Low) can still do so.

CreateProcessAsUser does not need SeAssignPrimaryTokenPrivilege here: the token is a restricted version of the
caller's own primary token. Python's subprocess cannot take a token, so this module owns the pipes and exposes a
small Popen look-alike (`RestrictedProc`: pid, stdin, stdout, poll, wait, kill, returncode).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

LOW_INTEGRITY_SID = "S-1-16-4096"


class RestrictedLaunchError(OSError):
    """The restricted launch is unavailable or failed. Callers must NOT fall back to an unrestricted launch."""


def available() -> bool:
    return sys.platform == "win32"


def status(enabled: bool) -> dict:
    """For the status row / doctor: is the launcher usable here and switched on."""
    return {"supported": available(), "enabled": bool(enabled) and available(),
            "integrity": "low" if enabled and available() else "inherited"}


class RestrictedProc:
    """Popen look-alike for a child started with the restricted token (Windows only)."""

    def __init__(self, h_process, h_thread, pid: int, stdin, stdout, api) -> None:
        self._h, self._api = h_process, api
        self.pid, self.stdin, self.stdout, self.returncode = pid, stdin, stdout, None
        api.CloseHandle(h_thread)

    def poll(self):
        if self.returncode is None and self._h:
            if self._api.WaitForSingleObject(self._h, 0) == 0:          # WAIT_OBJECT_0
                code = self._exit_code()
                self.returncode = code
                self._close()
        return self.returncode

    def _exit_code(self) -> int:
        import ctypes
        from ctypes import wintypes
        c = wintypes.DWORD()
        self._api.GetExitCodeProcess(self._h, ctypes.byref(c))
        return int(c.value)

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        ms = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        r = self._api.WaitForSingleObject(self._h, ms)
        if r == 0x102:                                                  # WAIT_TIMEOUT
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        self.returncode = self._exit_code()
        self._close()
        return self.returncode

    def kill(self) -> None:
        if self.returncode is None and self._h:
            self._api.TerminateProcess(self._h, 1)

    def _close(self) -> None:
        if self._h:
            self._api.CloseHandle(self._h)
            self._h = None


def _api():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    H, D, B = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
    PH, V = ctypes.POINTER(wintypes.HANDLE), wintypes.LPVOID
    k.GetCurrentProcess.restype = H
    k.CloseHandle.argtypes, k.CloseHandle.restype = [H], B
    k.CreatePipe.argtypes, k.CreatePipe.restype = [PH, PH, V, D], B
    k.SetHandleInformation.argtypes, k.SetHandleInformation.restype = [H, D, D], B
    k.WaitForSingleObject.argtypes, k.WaitForSingleObject.restype = [H, D], D
    k.GetExitCodeProcess.argtypes, k.GetExitCodeProcess.restype = [H, ctypes.POINTER(D)], B
    k.TerminateProcess.argtypes, k.TerminateProcess.restype = [H, wintypes.UINT], B
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, D, D, V, D, D, H]
    k.CreateFileW.restype = H
    k.LocalFree.argtypes, k.LocalFree.restype = [V], V
    a.OpenProcessToken.argtypes, a.OpenProcessToken.restype = [H, D, PH], B
    a.CreateRestrictedToken.argtypes = [H, D, D, V, D, V, D, V, PH]
    a.CreateRestrictedToken.restype = B
    a.SetTokenInformation.argtypes, a.SetTokenInformation.restype = [H, ctypes.c_int, V, D], B
    a.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(V)]
    a.ConvertStringSidToSidW.restype = B
    a.GetLengthSid.argtypes, a.GetLengthSid.restype = [V], D
    a.CreateProcessAsUserW.argtypes = [H, wintypes.LPCWSTR, wintypes.LPWSTR, V, V, B, D, V, wintypes.LPCWSTR, V, V]
    a.CreateProcessAsUserW.restype = B

    class API:
        pass
    api = API()
    for lib in (k, a):
        for n in dir(lib):
            if not n.startswith("_"):
                try:
                    setattr(api, n, getattr(lib, n))
                except Exception:
                    pass
    return api


def _fail(what: str):
    import ctypes
    err = ctypes.get_last_error()
    raise RestrictedLaunchError(err, f"{what} failed (Windows error {err})")


def restricted_token(low_integrity: bool = True):
    """A new primary token handle (the caller closes it): our token with privileges stripped and, by default, the
    Low integrity level."""
    import ctypes
    from ctypes import wintypes
    if not available():
        raise RestrictedLaunchError("restricted tokens are Windows only")
    api = _api()
    TOKEN_ASSIGN_PRIMARY, TOKEN_DUPLICATE, TOKEN_QUERY, TOKEN_ADJUST_DEFAULT = 0x1, 0x2, 0x8, 0x80
    cur = wintypes.HANDLE()
    if not api.OpenProcessToken(api.GetCurrentProcess(), TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY
                                | TOKEN_ADJUST_DEFAULT, ctypes.byref(cur)):
        _fail("OpenProcessToken")
    new = wintypes.HANDLE()
    try:
        DISABLE_MAX_PRIVILEGE = 0x1
        if not api.CreateRestrictedToken(cur, DISABLE_MAX_PRIVILEGE, 0, None, 0, None, 0, None, ctypes.byref(new)):
            _fail("CreateRestrictedToken")
    finally:
        api.CloseHandle(cur)
    if low_integrity:
        sid = ctypes.c_void_p()
        if not api.ConvertStringSidToSidW(LOW_INTEGRITY_SID, ctypes.byref(sid)):
            api.CloseHandle(new)
            _fail("ConvertStringSidToSid")
        try:
            class _LABEL(ctypes.Structure):
                _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]
            lab = _LABEL(sid, 0x20)                                    # SE_GROUP_INTEGRITY
            TokenIntegrityLevel = 25
            size = ctypes.sizeof(_LABEL) + api.GetLengthSid(sid)
            if not api.SetTokenInformation(new, TokenIntegrityLevel, ctypes.byref(lab), size):
                api.CloseHandle(new)
                _fail("SetTokenInformation(IntegrityLevel)")
        finally:
            api.LocalFree(sid)
    return new, api


def spawn(argv: "list[str] | str", *, cwd: str | None, env: dict[str, str] | None, stdin: str = "null",
          stdout: str = "pipe", stderr: str = "null", low_integrity: bool = True,
          suspended: bool = False) -> RestrictedProc:
    """Start `argv` with the restricted token. stdin: "null" | "pipe"; stdout: "pipe"; stderr: "null" | "stdout"
    (merged into the stdout pipe). No console window. suspended=True: the caller resumes it (isolation.py
    puts it into the Job Object first). Raises RestrictedLaunchError; never launches unrestricted."""
    if not available():
        raise RestrictedLaunchError("restricted tokens are Windows only")
    import ctypes
    import msvcrt
    from ctypes import wintypes
    token, api = restricted_token(low_integrity)
    opened: list = []                                                  # handles to close on failure / after launch

    class _SA(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]

    class _SI(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class _PI(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                    ("dwThreadId", wintypes.DWORD)]

    sa = _SA(ctypes.sizeof(_SA), None, True)

    def pipe() -> tuple:
        r, w = wintypes.HANDLE(), wintypes.HANDLE()
        if not api.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
            _fail("CreatePipe")
        opened.extend([r, w])
        return r, w

    def nul(write: bool):
        GENERIC_READ, GENERIC_WRITE, SHARE_RW, OPEN_EXISTING = 0x80000000, 0x40000000, 0x3, 3
        h = api.CreateFileW("NUL", GENERIC_WRITE if write else GENERIC_READ, SHARE_RW, ctypes.byref(sa),
                            OPEN_EXISTING, 0, None)
        if not h or h == wintypes.HANDLE(-1).value:
            _fail("open NUL")
        opened.append(wintypes.HANDLE(h))
        return wintypes.HANDLE(h)

    parent_in = parent_out = None
    try:
        si = _SI()
        si.cb = ctypes.sizeof(_SI)
        si.dwFlags = 0x100 | 0x1                                       # STARTF_USESTDHANDLES | USESHOWWINDOW
        si.wShowWindow = 0
        if stdin == "pipe":
            r_in, w_in = pipe()
            api.SetHandleInformation(w_in, 1, 0)                       # our end is not inherited
            si.hStdInput, parent_in = r_in, w_in
        else:
            si.hStdInput = nul(False)
        r_out, w_out = pipe()
        api.SetHandleInformation(r_out, 1, 0)
        si.hStdOutput, parent_out = w_out, r_out
        si.hStdError = w_out if stderr == "stdout" else nul(True)

        block = None
        if env is not None:
            block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")
        cmd = ctypes.create_unicode_buffer(argv if isinstance(argv, str) else subprocess.list2cmdline([str(x) for x in argv]))
        pi = _PI()
        CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT, CREATE_NEW_PROCESS_GROUP = 0x08000000, 0x400, 0x200
        if not api.CreateProcessAsUserW(token, None, cmd, None, None, True,
                                        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_NEW_PROCESS_GROUP
                                        | (0x4 if suspended else 0),
                                        block, cwd, ctypes.byref(si), ctypes.byref(pi)):
            _fail("CreateProcessAsUser")
        # the child owns its ends now: close ours so EOF reaches us when it exits
        child_ends = {int(h.value) for h in opened} - {int(x.value) for x in (parent_in, parent_out) if x}
        for h in opened:
            if h.value in child_ends:
                api.CloseHandle(h)
        opened.clear()

        def wrap(h, mode):
            fd = msvcrt.open_osfhandle(int(h.value), os.O_RDONLY if mode == "rb" else os.O_WRONLY)
            return os.fdopen(fd, mode)
        f_in = wrap(parent_in, "wb") if parent_in else None
        f_out = wrap(parent_out, "rb")
        parent_in = parent_out = None
        return RestrictedProc(pi.hProcess, pi.hThread, int(pi.dwProcessId), f_in, f_out, api)
    except BaseException:
        for h in opened:
            api.CloseHandle(h)
        raise
    finally:
        api.CloseHandle(token)


def integrity_of_current_process() -> str:
    """"low" | "medium" | "high" | "system" | "unknown" for THIS process (what a child reports about itself)."""
    if not available():
        return "unknown"
    import ctypes
    from ctypes import wintypes
    api = _api()
    h = wintypes.HANDLE()
    api.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    if not api.OpenProcessToken(api.GetCurrentProcess(), 0x8, ctypes.byref(h)):
        return "unknown"
    try:
        gt = ctypes.WinDLL("advapi32", use_last_error=True)
        gt.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)]
        gt.GetSidSubAuthorityCount.argtypes, gt.GetSidSubAuthorityCount.restype = [ctypes.c_void_p], ctypes.c_void_p
        gt.GetSidSubAuthority.argtypes, gt.GetSidSubAuthority.restype = [ctypes.c_void_p, wintypes.DWORD], ctypes.c_void_p
        n = wintypes.DWORD()
        gt.GetTokenInformation(h, 25, None, 0, ctypes.byref(n))
        buf = ctypes.create_string_buffer(n.value)
        if not gt.GetTokenInformation(h, 25, buf, n, ctypes.byref(n)):
            return "unknown"
        sid = ctypes.c_void_p.from_buffer(buf).value
        cnt = ctypes.c_ubyte.from_address(gt.GetSidSubAuthorityCount(sid)).value
        rid = ctypes.c_ulong.from_address(gt.GetSidSubAuthority(sid, cnt - 1)).value
        return ("low" if rid < 0x2000 else "medium" if rid < 0x3000 else "high" if rid < 0x4000 else "system")
    finally:
        api.CloseHandle(h)


def wait_exit(p: RestrictedProc, timeout: float) -> int:
    """Convenience for tests / callers: wait, kill on timeout."""
    t0 = time.monotonic()
    try:
        return p.wait(timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        raise RestrictedLaunchError(f"child did not end within {time.monotonic() - t0:.0f} s")


class AsyncProc:
    """The small asyncio-process surface the Skill runner uses (stdin.write/drain, stdout.readline, returncode, kill,
    wait), on top of a RestrictedProc (blocking pipe I/O in worker threads)."""

    class _In:
        def __init__(self, f) -> None:
            self._f = f

        def write(self, b: bytes) -> None:
            self._f.write(b)

        async def drain(self) -> None:
            import asyncio
            await asyncio.to_thread(self._f.flush)

    class _Out:
        def __init__(self, f, limit: int) -> None:
            self._f, self._limit = f, limit

        async def readline(self) -> bytes:
            import asyncio
            return await asyncio.to_thread(self._f.readline, self._limit)

    def __init__(self, p: RestrictedProc, limit: int = 1 << 20) -> None:
        self._p, self.pid = p, p.pid
        self.stdin, self.stdout = self._In(p.stdin), self._Out(p.stdout, limit)

    @property
    def returncode(self):
        return self._p.poll()

    def kill(self) -> None:
        self._p.kill()

    async def wait(self) -> int:
        import asyncio
        return await asyncio.to_thread(self._p.wait, 30)
