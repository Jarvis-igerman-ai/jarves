"""Linux confinement for code a task brought in (#1592): a project's check command and the release command.

That code runs as the owner, so a Claude permission rule cannot stop it from writing the repository's shared git files
or anything that later runs code outside the run (`~/.gitconfig`, `~/.bashrc`, systemd user units); git (JARVES's and
the owner's) would then run a planted filter or hook. Here the command runs in its own user + mount namespace where
the WHOLE file system is read-only (so no file and no parent folder can be changed or renamed), except:
- the run's worktree (its `.git` pointer file stays read-only),
- empty private tmpfs folders over `/tmp`, `/var/tmp`, `/dev/shm`, `/run/user/<uid>` and `~/.cache` (this also hides
  the owner's tmux/screen/X11/D-Bus sockets there),
- with `git_writes` (the release command, which may tag): `objects/`, `refs/`, `logs/` and its own
  `worktrees/<name>/` of the shared git dir (its `commondir`/`gitdir` stay read-only). Such a run can move any branch
  or tag and add `config.worktree` there (git reads it only if the owner set `extensions.worktreeConfig`).
A check also gets its own network namespace (only a loopback): no network and no abstract unix socket of the owner.
Every process the command starts stays in the namespace for good, also after the run ends; the mounts are locked for
nested namespaces (no umount, no clearing read-only).

Honest limits: Linux with user namespaces and kernel 5.12+ only (Windows/macOS run as before, #1665; elsewhere JARVES
logs once that it cannot confine). The release command keeps the network, so abstract unix sockets stay reachable for
it. Stdlib only: run as `python -I confine.py SPEC -- argv...`; a setup failure exits 125 and never runs argv.
"""
from __future__ import annotations

import ctypes
import fcntl
import json
import os
import socket
import struct
import sys
from pathlib import Path

FAIL = 125
AT_FDCWD, AT_EMPTY_PATH, AT_RECURSIVE = -100, 0x1000, 0x8000
OPEN_TREE_CLONE, MOVE_MOUNT_F_EMPTY_PATH, MOUNT_ATTR_RDONLY = 1, 4, 1
SYS_OPEN_TREE, SYS_MOVE_MOUNT, SYS_MOUNT_SETATTR = 428, 429, 442     # the same number on x86_64 and arm64
MS_REC, MS_PRIVATE = 16384, 1 << 18
HIDE = ("/tmp", "/var/tmp", "/dev/shm")          # each becomes an empty private tmpfs (plus /run/user/<uid>, ~/.cache)


def spec(common: Path, own: Path, wt: Path, git_writes: bool = False, net: bool = False) -> dict:
    """What a run in worktree `wt` (git dir `own`) of the repository with shared git dir `common` may write."""
    common, own, wt = common.resolve(), own.resolve(), wt.resolve()
    rw, ro = [str(wt)], [str(common)]
    if (wt / ".git").is_file():
        ro.append(str(wt / ".git"))
    if git_writes:
        rw += [str(common / d) for d in ("objects", "refs", "logs") if (common / d).is_dir()]
        if own.parent == common / "worktrees":
            rw.append(str(own))
            ro += [str(own / f) for f in ("commondir", "gitdir") if (own / f).is_file()]
    hide = (*HIDE, f"/run/user/{os.getuid()}", os.path.join(os.path.expanduser("~"), ".cache"))
    mask = [p for p in hide if os.path.isdir(p)]
    return {"rw": rw, "ro": ro, "mask": mask, "net": bool(net)}


def wrap(argv: list[str], s: dict) -> list[str]:
    return [sys.executable, "-I", str(Path(__file__).resolve()), json.dumps(s), "--", *argv]


def _ok(r: int, what: str) -> int:
    if r < 0:
        e = ctypes.get_errno()
        raise OSError(e, f"{what}: {os.strerror(e)}")
    return r


def _setattr(libc, fd: int, path: bytes, flags: int) -> None:
    attr = struct.pack("QQQQ", MOUNT_ATTR_RDONLY, 0, 0, 0)
    _ok(libc.syscall(SYS_MOUNT_SETATTR, fd, path, flags, attr, len(attr)), f"read-only {path.decode() or fd}")


def _loopback_up() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        req = struct.pack("16sH", b"lo", 0)
        flags = struct.unpack("16sH", fcntl.ioctl(s, 0x8913, req)[:18])[1]          # SIOCGIFFLAGS
        fcntl.ioctl(s, 0x8914, struct.pack("16sH", b"lo", flags | 1))                # SIOCSIFFLAGS, IFF_UP


def enter(s: dict) -> None:
    uid, gid, cwd = os.getuid(), os.getgid(), os.getcwd()
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNS | (0 if s.get("net") else os.CLONE_NEWNET))
    for name, text in (("setgroups", "deny"), ("uid_map", f"{uid} {uid} 1"), ("gid_map", f"{gid} {gid} 1")):
        with open(f"/proc/self/{name}", "w", encoding="ascii") as f:
            f.write(text)
    if not s.get("net"):
        _loopback_up()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    _ok(libc.mount(None, b"/", None, MS_REC | MS_PRIVATE, None), "private /")
    keep = [(p, False) for p in s["rw"]] + [(p, True) for p in s["ro"]]
    trees = []                                    # writable copies taken BEFORE everything turns read-only
    for p, ro in keep:
        fd = _ok(libc.syscall(SYS_OPEN_TREE, AT_FDCWD, p.encode(), OPEN_TREE_CLONE | AT_RECURSIVE), f"copy {p}")
        if ro:
            _setattr(libc, fd, b"", AT_EMPTY_PATH | AT_RECURSIVE)
        trees.append((p, fd))
    _setattr(libc, AT_FDCWD, b"/", AT_RECURSIVE)
    if os.path.ismount("/proc"):                  # /proc stays writable (a process's own maps, oom score); its
        attr = struct.pack("QQQQ", 0, MOUNT_ATTR_RDONLY, 0, 0)        # system files belong to the real root
        _ok(libc.syscall(SYS_MOUNT_SETATTR, AT_FDCWD, b"/proc", 0, attr, len(attr)), "writable /proc")
    for p in s["mask"]:
        _ok(libc.mount(b"tmpfs", p.encode(), b"tmpfs", 2 | 4, b"mode=700"), f"hide {p}")   # nosuid, nodev
    for p, fd in sorted(trees, key=lambda t: len(Path(t[0]).parts)):   # a parent before what is mounted inside it
        if not os.path.lexists(p):                # under a hidden (tmpfs) folder: make the mount point there
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.isdir(f"/proc/self/fd/{fd}"):
                os.mkdir(p)
            else:
                open(p, "w").close()              # noqa: SIM115
        _ok(libc.syscall(SYS_MOVE_MOUNT, fd, b"", AT_FDCWD, p.encode(), MOVE_MOUNT_F_EMPTY_PATH), f"mount {p}")
        os.close(fd)
    os.chdir(cwd)                                 # the old working folder is below the read-only copy now


def main(args: list[str]) -> int:
    if len(args) < 3 or args[1] != "--":
        print("jarves-confine: usage: confine.py SPEC -- argv...", file=sys.stderr)
        return FAIL
    try:
        enter(json.loads(args[0]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"jarves-confine: could not confine the run: {e}", file=sys.stderr)
        return FAIL
    try:
        os.execvp(args[2], args[2:])
    except OSError as e:
        print(f"jarves-confine: could not start {args[2]}: {e}", file=sys.stderr)
    return FAIL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
