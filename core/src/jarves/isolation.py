"""OS-level limits for approved helper commands and generated-Skill children (R-01 defence, Issue #17).

One interface, two mechanisms:
- Windows: a Job Object per command. The child is created SUSPENDED, put in the job, then resumed, so not one
  instruction runs outside it. The job has KILL_ON_JOB_CLOSE (closing our handle, a timeout or a Core crash ends the
  whole tree), an active-process limit, a job memory limit (optional per-process limit), below-normal priority, an
  optional user-CPU-time limit, DIE_ON_UNHANDLED_EXCEPTION (no error dialog keeps a process alive), no breakaway
  (neither BREAKAWAY_OK nor SILENT_BREAKAWAY_OK, so CREATE_BREAKAWAY_FROM_JOB fails for every process in it) and UI
  restrictions (no desktop creation / switching, no clipboard read / write, no global atoms, no ExitWindows, no
  display-settings or system-parameter changes; optionally no USER handles of processes outside the job).
- Linux / macOS: the command runs in its own session / process group (killed as a group) through a tiny stdlib
  launcher (isolation_exec.py) that sets rlimits (data size, CPU seconds, process count, nice) and then execs it.
  The generated-Skill child sets its own stricter rlimits (sandbox_child.py) and only gets the session here.

What this does NOT do (honest limits, see docs/reviews/SWV4_R01_DEFENSE.md): it does not restrict FILE or NETWORK
access. A job is not a sandbox: the command still runs with the owner's rights and can read what the owner can read.

Failures never crash a run: if the job cannot be created or the child cannot be assigned, the command runs as it did
before (taskkill / process-group cleanup) and the status says so: state "active" | "degraded" | "unavailable" plus
a reason (the health row `helper_isolation`, `jarvis doctor`, `jarvis features`, the dashboard, the self-check). The
helper asks for the typed code when the state is "unavailable".

Interface:
    lim = isolation.Limits(memory_mb=2048, max_processes=64)
    p, guard = isolation.popen(argv_or_command, limits=lim, shell=False, **popen_kwargs)
    proc, guard = await isolation.exec_async(*argv, limits=lim, posix_launcher=False, **kw)
    guard.state / guard.reason; guard.kill_tree() (timeout); guard.close() (always; reaps what is left)
    isolation.status() -> {"state", "reason", "mechanism", "limits"}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import logs

_EXEC = Path(__file__).with_name("isolation_exec.py")
STATES = ("active", "degraded", "unavailable")


@dataclass(frozen=True)
class Limits:
    memory_mb: int = 2048              # the whole job (Windows); data size per process (POSIX RLIMIT_DATA)
    process_memory_mb: int = 0         # Windows per-process limit; 0 = only the job limit
    max_processes: int = 64            # alive at once
    cpu_seconds: float = 0             # user CPU time (job total on Windows, per process on POSIX); 0 = no cap
    below_normal: bool = True          # lower scheduling priority
    ui_handles: bool = False           # Windows: no USER handles of windows outside the job (Skills only)

    @classmethod
    def from_config(cls, hc) -> "Limits":
        return cls(memory_mb=int(hc.memory_mb), max_processes=int(hc.max_processes),
                   cpu_seconds=float(hc.cpu_seconds))

    def text(self) -> str:
        cpu = f", CPU {self.cpu_seconds:g} s" if self.cpu_seconds else ""
        return f"memory {self.memory_mb} MB, at most {self.max_processes} process(es){cpu}"


HELPER_LIMITS = Limits()
SKILL_LIMITS = Limits(memory_mb=512, process_memory_mb=512, max_processes=1, cpu_seconds=10, ui_handles=True)

# ---------------------------------------------------------------------- Windows Job Object
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                   "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _UI(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    class _ACCT(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.IsProcessInJob.restype = wintypes.BOOL
    _k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _ntdll.NtResumeProcess.restype = ctypes.c_long
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PRIORITY_CLASS = 0x00000020
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
UILIMIT_HANDLES, UILIMIT_READCLIPBOARD, UILIMIT_WRITECLIPBOARD = 0x1, 0x2, 0x4
UILIMIT_SYSTEMPARAMETERS, UILIMIT_DISPLAYSETTINGS, UILIMIT_GLOBALATOMS = 0x8, 0x10, 0x20
UILIMIT_DESKTOP, UILIMIT_EXITWINDOWS = 0x40, 0x80
UI_BASE = (UILIMIT_READCLIPBOARD | UILIMIT_WRITECLIPBOARD | UILIMIT_SYSTEMPARAMETERS | UILIMIT_DISPLAYSETTINGS
           | UILIMIT_GLOBALATOMS | UILIMIT_DESKTOP | UILIMIT_EXITWINDOWS)
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_SUSPENDED = 0x00000004
_PROC_RIGHTS = 0x0001 | 0x0100 | 0x0800 | 0x1000 | 0x00100000   # TERMINATE, SET_QUOTA, SUSPEND_RESUME, QUERY_LTD, SYNC


class WinJob:
    """One Job Object with the limits applied. `problems` lists what could not be set (empty = all)."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.problems: list[str] = []
        self.handle = _k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        info = _EXT()
        b = info.BasicLimitInformation
        b.LimitFlags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                        | JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
        b.ActiveProcessLimit = max(1, int(limits.max_processes))
        info.JobMemoryLimit = int(limits.memory_mb) << 20
        if limits.process_memory_mb:
            b.LimitFlags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = int(limits.process_memory_mb) << 20
        if limits.below_normal:
            b.LimitFlags |= JOB_OBJECT_LIMIT_PRIORITY_CLASS
            b.PriorityClass = BELOW_NORMAL_PRIORITY_CLASS
        if limits.cpu_seconds:
            b.LimitFlags |= JOB_OBJECT_LIMIT_JOB_TIME
            b.PerJobUserTimeLimit = int(float(limits.cpu_seconds) * 10_000_000)     # 100 ns units
        if not _k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            _k32.CloseHandle(self.handle)
            self.handle = None
            raise OSError(err, "SetInformationJobObject(limits)")
        ui = _UI(UI_BASE | (UILIMIT_HANDLES if limits.ui_handles else 0))
        if not _k32.SetInformationJobObject(self.handle, 4, ctypes.byref(ui), ctypes.sizeof(ui)):
            self.problems.append(f"UI restrictions not applied (error {ctypes.get_last_error()})")

    def assign_suspended(self, pid: int) -> tuple[bool, str]:
        """Assign the suspended child, then resume it. Returns (in_job, why_not). Raises OSError only when the
        child could not be resumed (it is terminated then: never left hanging)."""
        h = _k32.OpenProcess(_PROC_RIGHTS, False, int(pid))
        if not h:
            return False, f"cannot open the child (error {ctypes.get_last_error()})"
        try:
            ok = bool(_k32.AssignProcessToJobObject(self.handle, h))
            why = "" if ok else f"the child could not join the job (error {ctypes.get_last_error()})"
            st = _ntdll.NtResumeProcess(h)
            if st != 0:
                _k32.TerminateProcess(h, 1)
                raise OSError(st, "NtResumeProcess")
            return ok, why
        finally:
            _k32.CloseHandle(h)

    def contains(self, pid: int) -> bool:
        h = _k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        try:
            r = wintypes.BOOL()
            return bool(_k32.IsProcessInJob(h, self.handle, ctypes.byref(r))) and bool(r.value)
        finally:
            _k32.CloseHandle(h)

    def query(self) -> dict:
        """The limits as the OS holds them (tests, doctor) and the live process count."""
        info, ui, acct = _EXT(), _UI(), _ACCT()
        _k32.QueryInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info), None)
        _k32.QueryInformationJobObject(self.handle, 4, ctypes.byref(ui), ctypes.sizeof(ui), None)
        _k32.QueryInformationJobObject(self.handle, 1, ctypes.byref(acct), ctypes.sizeof(acct), None)
        b = info.BasicLimitInformation
        return {"limit_flags": int(b.LimitFlags), "active_process_limit": int(b.ActiveProcessLimit),
                "job_memory_limit": int(info.JobMemoryLimit), "process_memory_limit": int(info.ProcessMemoryLimit),
                "priority_class": int(b.PriorityClass), "job_user_time_limit": int(b.PerJobUserTimeLimit),
                "ui_restrictions": int(ui.UIRestrictionsClass), "active_processes": int(acct.ActiveProcesses),
                "total_processes": int(acct.TotalProcesses)}

    def terminate(self, code: int = 1) -> None:
        if self.handle:
            _k32.TerminateJobObject(self.handle, code)

    def close(self) -> None:
        """KILL_ON_JOB_CLOSE: every process still in the job ends here."""
        h, self.handle = self.handle, None
        if h:
            _k32.CloseHandle(h)


# ---------------------------------------------------------------------- the guard (one per run)
class Guard:
    """What a run holds: the job (Windows) or the process group (POSIX), and the honest state of this run."""

    def __init__(self, pid: int, state: str, reason: str, job: "WinJob | None" = None, group: bool = False) -> None:
        self.pid, self.state, self.reason, self.job, self.group = pid, state, reason, job, group
        self._closed = False

    def kill_tree(self) -> None:
        """Timeout / cancellation: end the command AND everything it started."""
        if self.job is not None:
            try:
                self.job.terminate(1)
                return
            except OSError:
                pass
        _kill_tree_fallback(self.pid, self.group)

    def close(self) -> None:
        """Always after the run: Windows closes the job (kill-on-close reaps any process left behind); POSIX kills
        what is left in the command's process group."""
        if self._closed:
            return
        self._closed = True
        if self.job is not None:
            self.job.close()
        elif self.group and sys.platform != "win32":
            import signal
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def _kill_tree_fallback(pid: int, group: bool) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
        elif group:
            import signal
            os.killpg(pid, signal.SIGKILL)
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


# ---------------------------------------------------------------------- status (health row)
_lock = threading.Lock()
_last: dict | None = None            # the last REAL run (wins over the probe: the row reflects what happened)
_probe_cache: dict | None = None
listeners: dict[str, Callable[[dict], None]] = {}   # by owner name: the newest registration wins


_restricted_token = False


def set_restricted_token(on: bool) -> None:
    """[helper] restricted_token (default off): on Windows the child also gets a restricted token at the Low
    integrity level (winsandbox.py). It fails closed: if that launch fails the command does not run."""
    global _restricted_token
    _restricted_token = bool(on)


def restricted_token_enabled() -> bool:
    return _restricted_token and sys.platform == "win32"


def mechanism() -> str:
    if sys.platform == "win32":
        if restricted_token_enabled():
            return "Windows Job Object + restricted token (Low integrity)"
        return "Windows Job Object"
    return "POSIX rlimits + own session"


def _record(state: str, reason: str, limits: Limits) -> None:
    global _last
    new = {"state": state, "reason": reason, "mechanism": mechanism(), "limits": asdict(limits), "source": "last run"}
    with _lock:
        changed = _last is None or (_last["state"], _last["reason"]) != (state, reason)
        _last = new
    if changed:
        if state != "active":
            logs.warn("isolation", "ISOLATION_DEGRADED", "helper isolation is not active", state=state,
                      reason=reason[:200])
        for fn in list(listeners.values()):
            try:
                fn(dict(new))
            except Exception:
                logs.error("isolation", "ISOLATION_LISTENER_FAILED", "isolation listener failed", exc=True)


def _probe(limits: Limits) -> dict:
    if sys.platform == "win32":
        try:
            job = WinJob(limits)
        except OSError as e:
            return {"state": "unavailable", "reason": f"no Windows job object ({e.strerror or e})"}
        except Exception as e:                    # noqa: BLE001 - ctypes missing / broken: degrade, never crash
            return {"state": "unavailable", "reason": f"no Windows job object ({type(e).__name__})"}
        try:
            if job.problems:
                return {"state": "degraded", "reason": "; ".join(job.problems)}
            return {"state": "active", "reason": f"each command runs in its own job: {limits.text()}, kill on close, "
                                                 f"no breakaway, UI restricted"}
        finally:
            job.close()
    try:
        r = subprocess.run([sys.executable, "-I", "-S", str(_EXEC), json.dumps(asdict(limits)), "--probe"],
                           capture_output=True, timeout=20, text=True)
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:                        # noqa: BLE001
        return {"state": "unavailable", "reason": f"the limits launcher does not run ({type(e).__name__})"}
    failed = d.get("failed") or []
    if not d.get("applied"):
        return {"state": "unavailable", "reason": "no POSIX resource limits: " + "; ".join(failed)[:160]}
    if failed:
        return {"state": "degraded", "reason": "own session; not applied: " + "; ".join(failed)[:160]}
    return {"state": "active", "reason": f"own session + rlimits: {limits.text()}"}


def status(limits: Limits | None = None, *, refresh: bool = False) -> dict:
    """{"state": active|degraded|unavailable, "reason", "mechanism", "limits", "source"}. The last real run wins;
    without one, a cheap probe (create a job with the limits and close it / run the launcher's self-test)."""
    global _probe_cache
    limits = limits or _helper_limits
    with _lock:
        last = dict(_last) if _last else None
    if last and not refresh:
        return last
    if _probe_cache is None or refresh or _probe_cache.get("limits") != asdict(limits):
        p = _probe(limits)
        _probe_cache = {**p, "mechanism": mechanism(), "limits": asdict(limits), "source": "probe"}
    return dict(_probe_cache)


def reset_status() -> None:
    """Tests: forget the last run and the probe."""
    global _last, _probe_cache
    with _lock:
        _last, _probe_cache = None, None


_helper_limits = HELPER_LIMITS


def configure(limits: Limits) -> None:
    """The Core passes the [helper] limits from config.toml."""
    global _helper_limits
    _helper_limits = limits


def helper_limits() -> Limits:
    return _helper_limits


# ---------------------------------------------------------------------- spawning
def _win_flags(kw: dict) -> None:
    kw["creationflags"] = int(kw.get("creationflags") or 0) | CREATE_SUSPENDED


def _win_attach(pid: int, limits: Limits, job: "WinJob | None", job_err: str) -> Guard:
    if job is None:
        # still suspended: resume it, run it as before
        h = _k32.OpenProcess(_PROC_RIGHTS, False, int(pid))
        try:
            st = _ntdll.NtResumeProcess(h) if h else -1
            if st != 0:
                if h:
                    _k32.TerminateProcess(h, 1)
                raise OSError(st, "NtResumeProcess")
        finally:
            if h:
                _k32.CloseHandle(h)
        return Guard(pid, "unavailable", job_err)
    ok, why = job.assign_suspended(pid)
    if not ok:
        job.close()
        return Guard(pid, "unavailable", why)
    if job.problems:
        return Guard(pid, "degraded", "; ".join(job.problems), job)
    return Guard(pid, "active", f"job object: {limits.text()}", job)


def _win_job(limits: Limits) -> tuple["WinJob | None", str]:
    try:
        return WinJob(limits), ""
    except OSError as e:
        return None, f"no Windows job object ({e.strerror or e})"
    except Exception as e:                        # noqa: BLE001
        return None, f"no Windows job object ({type(e).__name__})"


def _stdio(kw: dict) -> dict:
    """The subprocess stdio arguments as winsandbox.spawn wants them (DEVNULL / PIPE / STDOUT only)."""
    return dict(stdin="pipe" if kw.get("stdin") == subprocess.PIPE else "null",
                stderr="stdout" if kw.get("stderr") == subprocess.STDOUT else "null")


def _restricted_popen(args, shell: bool, kw: dict):
    """Popen look-alike started with the restricted token, suspended (the caller joins the job, then resumes)."""
    from . import winsandbox
    cmd = f'"{os.environ.get("COMSPEC", "cmd.exe")}" /c {args}' if shell and isinstance(args, str) else args
    return winsandbox.spawn(cmd, cwd=kw.get("cwd"), env=kw.get("env"), suspended=True, **_stdio(kw))


async def _restricted_exec(args, kw: dict):
    from . import winsandbox
    p = winsandbox.spawn(list(args), cwd=kw.get("cwd"), env=kw.get("env"), suspended=True, **_stdio(kw))
    return winsandbox.AsyncProc(p, int(kw.get("limit") or (1 << 20)))


def _note_token(g: "Guard") -> None:
    if restricted_token_enabled() and g.state == "active":
        g.reason += "; restricted token, Low integrity"


def _posix_wrap(args: list[str], limits: Limits) -> list[str]:
    return [sys.executable, "-I", "-S", str(_EXEC), json.dumps(asdict(limits)), "--", *args]


_posix_probes: dict[str, dict] = {}


def _posix_state(limits: Limits, launched: bool) -> tuple[str, str]:
    if not launched:
        return "active", "own session; the Skill child sets its own rlimits"
    k = json.dumps(asdict(limits), sort_keys=True)
    if k not in _posix_probes:
        _posix_probes[k] = _probe(limits)
    p = _posix_probes[k]
    return p["state"], p["reason"]


def popen(args, *, limits: Limits | None = None, shell: bool = False, record: bool = True,
          **kw) -> tuple[subprocess.Popen, Guard]:
    """subprocess.Popen under the limits. Windows: created suspended, assigned, resumed. POSIX: `args` must be a
    list (use ["/bin/sh", "-c", line] for a shell line); it runs through the limits launcher in its own session."""
    limits = limits or _helper_limits
    if sys.platform == "win32":
        job, err = _win_job(limits)
        _win_flags(kw)
        try:
            p = _restricted_popen(args, shell, kw) if restricted_token_enabled() else subprocess.Popen(
                args, shell=shell, **kw)
        except BaseException:
            if job is not None:
                job.close()
            raise
        try:
            g = _win_attach(p.pid, limits, job, err)
        except OSError:
            if job is not None:
                job.close()
            p.wait(timeout=5)
            raise
        _note_token(g)
    else:
        kw["start_new_session"] = True
        argv = list(args) if not isinstance(args, str) else ["/bin/sh", "-c", args]
        state, reason = _posix_state(limits, True)
        p = subprocess.Popen(_posix_wrap(argv, limits), **kw)
        g = Guard(p.pid, state, reason, group=True)
    if record:
        _record(g.state, g.reason, limits)
    return p, g


async def exec_async(*args: str, limits: Limits = SKILL_LIMITS, posix_launcher: bool = False, record: bool = False,
                     **kw):
    """asyncio.create_subprocess_exec under the limits. Returns (process, guard)."""
    import asyncio
    if sys.platform == "win32":
        job, err = _win_job(limits)
        _win_flags(kw)
        try:
            proc = await _restricted_exec(args, kw) if restricted_token_enabled() else \
                await asyncio.create_subprocess_exec(*args, **kw)
        except BaseException:
            if job is not None:
                job.close()
            raise
        try:
            g = _win_attach(proc.pid, limits, job, err)
        except OSError:
            if job is not None:
                job.close()
            await proc.wait()
            raise
        _note_token(g)
    else:
        kw["start_new_session"] = True
        argv = _posix_wrap(list(args), limits) if posix_launcher else list(args)
        state, reason = _posix_state(limits, posix_launcher)
        proc = await asyncio.create_subprocess_exec(*argv, **kw)
        g = Guard(proc.pid, state, reason, group=True)
    if record:
        _record(g.state, g.reason, limits)
    return proc, g


def row(cfg=None, components: dict | None = None) -> dict:
    """The `helper_isolation` row for views outside the Core (doctor, features, dashboard, self-check): the running
    Core's live health row when there is one, else this process's own probe with the configured limits."""
    live = (components or {}).get("helper_isolation") or {}
    if live.get("status"):
        detail = str(live.get("detail") or "")
        state = detail.split(":", 1)[0] if detail.split(":", 1)[0] in STATES else \
            {"ok": "active", "degraded": "degraded"}.get(live["status"], "unavailable")
        return {"state": state, "reason": detail.split(":", 1)[-1].strip(), "mechanism": mechanism(),
                "status": live["status"], "source": "running Core"}
    lim = Limits.from_config(cfg.helper) if cfg is not None and hasattr(cfg, "helper") else _helper_limits
    st = status(lim)
    return {**st, "status": health_status(st)[0], "source": "this check (Core not running)"
            if st.get("source") == "probe" else st.get("source")}


# ---------------------------------------------------------------------- texts
HE_STATE = {"active": "פעיל", "degraded": "חלקי", "unavailable": "לא זמין"}


def health_status(st: dict) -> tuple[str, str, str]:
    """(health status, detail, action) for the health row `helper_isolation`."""
    state, reason = st.get("state"), str(st.get("reason") or "")
    if state == "active":
        return "ok", f"active: {reason}", ""
    action = "helper commands now need the typed code; see docs/reviews/SWV4_R01_DEFENSE.md" \
        if state == "unavailable" else "see docs/reviews/SWV4_R01_DEFENSE.md"
    return ("degraded" if state == "degraded" else "unavailable"), f"{state}: {reason}", action
