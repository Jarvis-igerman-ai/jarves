"""Linux only, stdlib only, run as `python -I _reaper.py TIMEOUT ARGV...` (#1592). The process becomes a child subreaper,
so every process its command leaves behind (a detached `setsid` grandchild too) is re-parented to it; when the command
ends, times out (exit 124) or this process gets SIGTERM, all of them are killed before it exits. A JARVES guard
compares the shared git files only after that, so nothing the run started can write after the comparison."""
import ctypes
import os
import signal
import subprocess
import sys
import time

PR_SET_CHILD_SUBREAPER = 36


class _Stop(Exception):
    pass


def _children(me: int) -> list[int]:
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat", "rb") as f:
                    stat = f.read()
            except OSError:
                continue
            if int(stat[stat.rfind(b")") + 2:].split()[1]) == me:
                out.append(int(d))
    return out


def _stop_all(me: int) -> bool:
    for _ in range(500):
        kids = _children(me)
        if not kids:
            return True
        for k in kids:
            try:
                os.kill(k, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.01)
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
    return not _children(me)


def main(argv: list[str]) -> int:
    timeout, cmd = float(argv[0]), argv[1:]
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        print("jarves: the run cannot be contained (no child subreaper)", file=sys.stderr)
        return 126

    def stop(*_a):
        raise _Stop
    signal.signal(signal.SIGTERM, stop)
    try:
        p = subprocess.Popen(cmd)
    except OSError as e:
        print(f"jarves: could not start: {e}", file=sys.stderr)
        return 127
    rc = 0
    try:
        rc = p.wait(timeout if timeout > 0 else None)
    except subprocess.TimeoutExpired:
        rc = 124
    except _Stop:
        rc = 143
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if not _stop_all(os.getpid()):
            print("jarves: some processes of the run could not be stopped", file=sys.stderr)
            rc = 125
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
