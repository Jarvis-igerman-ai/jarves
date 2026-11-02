"""POSIX limits launcher for helper commands (jarves/isolation.py starts it; stdlib only, never imports jarves).

    python -I -S isolation_exec.py '<limits json>' -- program arg...   set the limits, then exec the program
    python -I -S isolation_exec.py '<limits json>' --probe              print {"applied": [...], "failed": [...]}

Limits (hard and soft): RLIMIT_DATA = memory_mb (per process: data + heap; virtual reservations such as V8's are not
counted, so Node / Go keep working), RLIMIT_CPU = cpu_seconds (per process, 0 = none), RLIMIT_NPROC = the user's
current process count + max_processes (Linux; RLIMIT_NPROC counts ALL processes of the user, so a bare number would
be wrong), RLIMIT_CORE = 0, and nice +5 when below_normal. The caller already started us in a new session.
"""
import json
import os
import sys


def _user_procs() -> int:
    """Tasks (processes AND threads) of this user. RLIMIT_NPROC counts every Linux thread, so counting only
    processes left less headroom than intended and the approved command's own fork (the grep of 'a | grep b')
    could fail with EAGAIN, exit 2 (#660)."""
    uid, n = os.getuid(), 0
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if os.stat("/proc/" + d).st_uid == uid:
                    try:
                        n += max(1, len(os.listdir(f"/proc/{d}/task")))
                    except OSError:
                        n += 1
            except OSError:
                pass
    return n


def apply(lim: dict) -> tuple[list, list]:
    applied, failed = [], []
    try:
        import resource
    except ImportError:
        return [], ["no resource module"]

    def setlim(name, value):
        r = getattr(resource, name, None)
        if r is None:
            failed.append(f"{name} (not on this system)")
            return
        try:
            soft, hard = resource.getrlimit(r)
            if hard != resource.RLIM_INFINITY and value > hard:
                value = hard
            resource.setrlimit(r, (value, value))
            applied.append(name)
        except (ValueError, OSError) as e:
            failed.append(f"{name} ({type(e).__name__})")

    setlim("RLIMIT_DATA", int(lim.get("memory_mb") or 2048) << 20)
    if float(lim.get("cpu_seconds") or 0) > 0:
        setlim("RLIMIT_CPU", max(1, int(float(lim["cpu_seconds"]) + 0.999)))
    setlim("RLIMIT_CORE", 0)
    if os.path.isdir("/proc/self"):
        try:
            setlim("RLIMIT_NPROC", _user_procs() + max(1, int(lim.get("max_processes") or 64)))
        except OSError as e:
            failed.append(f"RLIMIT_NPROC ({type(e).__name__})")
    else:
        failed.append("RLIMIT_NPROC (process count not readable here)")
    if lim.get("below_normal", True):
        try:
            os.nice(5)
            applied.append("nice")
        except OSError as e:
            failed.append(f"nice ({type(e).__name__})")
    return applied, failed


def main(argv) -> int:
    if len(argv) < 2:
        print("usage: isolation_exec.py LIMITS_JSON (--probe | -- PROGRAM ...)", file=sys.stderr)
        return 2
    lim = json.loads(argv[0])
    if argv[1] == "--probe":
        applied, failed = apply(lim)
        print(json.dumps({"applied": applied, "failed": failed}))
        return 0
    if argv[1] != "--" or len(argv) < 3:
        return 2
    apply(lim)
    prog = argv[2:]
    try:
        os.execvp(prog[0], prog)
    except OSError as e:
        print(f"cannot start {prog[0]}: {e.strerror}", file=sys.stderr)
        return 127
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
