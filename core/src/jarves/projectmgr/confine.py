"""Linux confinement for code a task brought in (#1592): a project's check command, also when an agent runs it.

That code runs as the owner, so a Claude permission rule cannot stop it from writing the repository's shared git files
or anything that later runs code outside the run (`~/.gitconfig`, `~/.bashrc`, systemd user units); git (JARVES's and
the owner's) would then run a planted filter or hook. Here the command runs in its own user, mount, PID and network
namespaces:
- the WHOLE file system is read-only (no file and no parent folder can be changed or renamed), except the run's
  worktree (its `.git` pointer file stays read-only);
- `/tmp`, `/var/tmp`, `/dev/shm`, `/run` and `~/.cache` are empty private tmpfs folders, and every other bound unix
  socket path the host lists (`/proc/net/unix`, for example a Docker socket in the owner's home) is covered by
  `/dev/null`, so no daemon, agent, tmux/screen, X11 or D-Bus server of the owner can be reached;
- no network and no abstract unix socket of the owner (only a loopback);
- the command is PID 1 of its own PID namespace with a fresh `/proc`: no process outside can be seen, traced or
  signalled, every process it starts dies with it, and it dies when the run is stopped (parent-death signal).
The mounts are locked for nested namespaces (no umount, no clearing read-only).

Honest limits: Linux with user namespaces and kernel 5.12+ only (Windows/macOS run as before, #1665; elsewhere JARVES
logs once that it cannot confine). A socket bound after the run started is not covered. The owner's release command
is not confined (its scope is an owner decision, see #1592). Stdlib only: run as `python -I confine.py SPEC --
argv...`; a setup failure exits 125 and never runs argv.
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import struct
import sys
from pathlib import Path

FAIL = 125
AT_FDCWD, AT_EMPTY_PATH, AT_RECURSIVE = -100, 0x1000, 0x8000
OPEN_TREE_CLONE, MOVE_MOUNT_F_EMPTY_PATH, MOUNT_ATTR_RDONLY = 1, 4, 1
SYS_OPEN_TREE, SYS_MOVE_MOUNT, SYS_MOUNT_SETATTR = 428, 429, 442     # the same number on x86_64 and arm64
MS_NOSUID, MS_NODEV, MS_NOEXEC, MS_BIND, MS_REC, MS_PRIVATE = 2, 4, 8, 4096, 16384, 1 << 18
PR_SET_PDEATHSIG = 1
HIDE = ("/tmp", "/var/tmp", "/dev/shm", "/run")  # each becomes an empty private tmpfs (plus ~/.cache)
MAX_SOCKETS = 4096


def _sockets(hidden: list[str]) -> list[str]:
    """Bound unix socket paths of the host (not abstract ones, not under a hidden folder), at most MAX_SOCKETS."""
    out: list[str] = []
    try:
        with open("/proc/net/unix", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        parts = line.split(None, 7)
        p = parts[7] if len(parts) == 8 else ""
        if p.startswith("/") and not any(p == h or p.startswith(h + "/") for h in hidden) and p not in out \
                and os.path.exists(p):
            out.append(p)
        if len(out) >= MAX_SOCKETS:
            break
    return out


def spec(wt: Path, common: Path | None = None) -> dict:
    """What a run in worktree `wt` may write (only `wt`), and what it must not reach. `common` (the repository's shared
    git dir) is kept visible, read-only, also when it lies under a hidden folder; None when it cannot be read."""
    wt = wt.resolve()
    ro = [str(common.resolve())] if common else []
    if (wt / ".git").is_file():
        ro.append(str(wt / ".git"))
    real = {os.path.realpath(p) for p in (*HIDE, os.path.join(os.path.expanduser("~"), ".cache")) if os.path.isdir(p)}
    mask = sorted(real)
    return {"rw": [str(wt)], "ro": ro, "mask": mask, "sockets": _sockets(mask)}


def wrap(argv: list[str], s: dict) -> list[str]:
    return [sys.executable, "-I", str(Path(__file__).resolve()), json.dumps(s), "--", *argv]


def _ok(r: int, what: str) -> int:
    if r < 0:
        e = ctypes.get_errno()
        raise OSError(e, f"{what}: {os.strerror(e)}")
    return r


def _setattr(libc, fd: int, path: bytes, flags: int, clear: bool = False) -> None:
    attr = struct.pack("QQQQ", 0 if clear else MOUNT_ATTR_RDONLY, MOUNT_ATTR_RDONLY if clear else 0, 0, 0)
    _ok(libc.syscall(SYS_MOUNT_SETATTR, fd, path, flags, attr, len(attr)), f"read-only {path.decode() or fd}")


def _loopback_up() -> None:
    import fcntl                                  # POSIX only: this module is imported on Windows too
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        req = struct.pack("16sH", b"lo", 0)
        flags = struct.unpack("16sH", fcntl.ioctl(s, 0x8913, req)[:18])[1]          # SIOCGIFFLAGS
        fcntl.ioctl(s, 0x8914, struct.pack("16sH", b"lo", flags | 1))                # SIOCSIFFLAGS, IFF_UP


def enter(s: dict):
    """Everything but the PID namespace's own /proc (that needs the forked PID 1). Returns libc."""
    uid, gid, cwd = os.getuid(), os.getgid(), os.getcwd()
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNS | os.CLONE_NEWNET | os.CLONE_NEWPID)
    for name, text in (("setgroups", "deny"), ("uid_map", f"{uid} {uid} 1"), ("gid_map", f"{gid} {gid} 1")):
        with open(f"/proc/self/{name}", "w", encoding="ascii") as f:
            f.write(text)
    _loopback_up()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    _ok(libc.mount(None, b"/", None, MS_REC | MS_PRIVATE, None), "private /")
    trees = []                                    # copies taken BEFORE everything turns read-only
    for p, ro in [(p, False) for p in s["rw"]] + [(p, True) for p in s["ro"]]:
        fd = _ok(libc.syscall(SYS_OPEN_TREE, AT_FDCWD, p.encode(), OPEN_TREE_CLONE | AT_RECURSIVE), f"copy {p}")
        if ro:
            _setattr(libc, fd, b"", AT_EMPTY_PATH | AT_RECURSIVE)
        trees.append((p, fd))
    _setattr(libc, AT_FDCWD, b"/", AT_RECURSIVE)
    for p in s["mask"]:
        _ok(libc.mount(b"tmpfs", p.encode(), b"tmpfs", MS_NOSUID | MS_NODEV, b"mode=700"), f"hide {p}")
    for p, fd in sorted(trees, key=lambda t: len(Path(t[0]).parts)):   # a parent before what is mounted inside it
        if not os.path.lexists(p):                # under a hidden (tmpfs) folder: make the mount point there
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.isdir(f"/proc/self/fd/{fd}"):
                os.mkdir(p)
            else:
                open(p, "w").close()              # noqa: SIM115
        _ok(libc.syscall(SYS_MOVE_MOUNT, fd, b"", AT_FDCWD, p.encode(), MOVE_MOUNT_F_EMPTY_PATH), f"mount {p}")
        os.close(fd)
    for p in s.get("sockets", []):
        if os.path.lexists(p):
            _ok(libc.mount(b"/dev/null", p.encode(), None, MS_BIND, None), f"cover socket {p}")
    os.chdir(cwd)                                 # the old working folder is below the read-only copy now
    return libc


def _pid1(libc, argv: list[str]) -> None:
    """In the forked child, PID 1 of the new PID namespace: die with the parent, see only this namespace, run argv."""
    try:
        _ok(libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0), "parent-death signal")
        _ok(libc.mount(b"proc", b"/proc", b"proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, None), "fresh /proc")
        os.execvp(argv[0], argv)
    except OSError as e:
        print(f"jarves-confine: could not start {argv[0]}: {e}", file=sys.stderr)
    os._exit(FAIL)


def main(args: list[str]) -> int:
    if len(args) < 3 or args[1] != "--":
        print("jarves-confine: usage: confine.py SPEC -- argv...", file=sys.stderr)
        return FAIL
    try:
        libc = enter(json.loads(args[0]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"jarves-confine: could not confine the run: {e}", file=sys.stderr)
        return FAIL
    sys.stdout.flush()
    pid = os.fork()
    if pid == 0:
        _pid1(libc, args[2:])
    signal.signal(signal.SIGTERM, lambda *_: os.kill(pid, signal.SIGKILL))
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
