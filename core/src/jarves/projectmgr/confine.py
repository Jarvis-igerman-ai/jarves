"""Linux confinement for code a task brought in (#1592): a project's check command and an agent that may run it.

That code runs as the owner, so a Claude permission rule cannot stop it from writing the repository's shared git files
(`.git/config`, hooks, `info/attributes`, the `.git` pointer and `commondir`/`gitdir` files of every worktree); git
(JARVES's and the owner's) would then run a planted filter or hook. Here the command runs in its own user + mount
namespace where those paths are read-only bind mounts. Every process it starts stays in that namespace, also after
the run ends, and the mounts are locked for nested namespaces (no umount, no non-recursive bind of the parent).
`/run/user/<uid>` is hidden (an empty tmpfs) so the systemd user manager / D-Bus cannot start a process outside it.

Usable with git writes (`git_writes=True`, an agent's `git add`/`commit`): `objects/`, `refs/`, `logs/` and this
worktree's own `worktrees/<name>/` stay writable, its `commondir`/`gitdir` stay read-only. So such a run can still move
any branch or tag, and can add a `config.worktree` there (git reads it only if the owner set `extensions.worktreeConfig`).

Honest limits: Linux only (Windows/macOS run as before, #1665); where user namespaces are off the command runs as
before and JARVES logs that once. Other files the owner can write (`~/.gitconfig`, other repositories) are not
covered. Stdlib only: run as `python -I confine.py SPEC -- argv...`; a setup failure exits 125 and never runs argv.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path

FAIL = 125
MS_RDONLY, MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 1, 32, 4096, 16384, 1 << 18
_KEEP = 2 | 4 | 8 | 1024 | 2048 | 4096        # nosuid, nodev, noexec, noatime, nodiratime, relatime (locked flags)


def spec(common: Path, own: Path, wt: Path, git_writes: bool) -> dict:
    """Read-only and writable paths for a run in worktree `wt` (git dir `own`) of the repository whose shared git dir is
    `common`, taken when the run starts. Every worktree's `.git` pointer file is listed, also ones outside `wt`."""
    common, own, wt = common.resolve(), own.resolve(), wt.resolve()
    ro: list[str] = []
    for g in sorted((common / "worktrees").glob("*/gitdir")):
        ptr = Path(g.read_text(encoding="utf-8", errors="replace").strip())
        if ptr.name == ".git" and ptr.is_file():
            ro.append(str(ptr.resolve()))
    if (wt / ".git").is_file() and str(wt / ".git") not in ro:
        ro.append(str(wt / ".git"))
    rw: list[str] = []
    if git_writes:
        rw = [str(common / d) for d in ("objects", "refs", "logs") if (common / d).is_dir()]
        if own.parent == common / "worktrees":
            rw.append(str(own))
            ro += [str(own / f) for f in ("commondir", "gitdir") if (own / f).is_file()]
    run = Path(f"/run/user/{os.getuid()}")
    return {"tree": str(common), "ro": ro, "rw": rw, "mask": [str(run)] if run.is_dir() else []}


def wrap(argv: list[str], s: dict) -> list[str]:
    return [sys.executable, "-I", str(Path(__file__).resolve()), json.dumps(s), "--", *argv]


def _mount(libc, src, dst, fs, flags) -> None:
    if libc.mount(src, dst.encode(), fs, flags, None) != 0:
        raise OSError(ctypes.get_errno(), f"mount {dst}: {os.strerror(ctypes.get_errno())}")


def _bind(libc, p: str, ro: bool, rec: bool = False) -> None:
    _mount(libc, p.encode(), p, None, MS_BIND | (MS_REC if rec else 0))
    flags = os.statvfs(p).f_flag & _KEEP
    _mount(libc, None, p, None, MS_REMOUNT | MS_BIND | flags | (MS_RDONLY if ro else 0))


def enter(s: dict) -> None:
    uid, gid = os.getuid(), os.getgid()
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNS)
    for name, text in (("setgroups", "deny"), ("uid_map", f"{uid} {uid} 1"), ("gid_map", f"{gid} {gid} 1")):
        with open(f"/proc/self/{name}", "w", encoding="ascii") as f:
            f.write(text)
    libc = ctypes.CDLL(None, use_errno=True)
    _mount(libc, None, "/", None, MS_REC | MS_PRIVATE)
    for p in s["rw"]:                             # own mounts first, so the recursive bind of the tree keeps them writable
        _bind(libc, p, ro=False)
    _bind(libc, s["tree"], ro=True, rec=True)     # the remount makes only the top of the tree read-only
    for p in s["ro"]:
        _bind(libc, p, ro=True)
    for p in s["mask"]:
        _mount(libc, b"tmpfs", p, b"tmpfs", 2 | 4 | 8)


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
