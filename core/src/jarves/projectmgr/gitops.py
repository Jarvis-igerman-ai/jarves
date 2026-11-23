"""Git for the project manager. Every JARVES worktree lives under `<home>/projects/<id>/` (never inside the owner's
repository, so tests/linters at the repo root never see them and agents do not inherit the owner's CLAUDE.md of a parent
folder). The owner's checkout is never touched: the integration branch `jarves/p<ID>/integration` has its own worktree,
merges are fast-forward only after the check ran on the merged result. JARVES never pushes."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
IDENT = ["-c", "user.name=JARVES", "-c", "user.email=jarves@localhost", "-c", "commit.gpgsign=false"]
# files an agent may never change: they would widen the permissions of every later task or run code on its own
# (.github/ holds CI workflows that run with the repository's secrets). Matched case-insensitively (JAR-317): Windows
# and macOS file systems ignore case, so `.Claude/settings.json` or `.GitHub/workflows/x.yml` is the same file.
PROTECTED = (".claude/", ".codex/", ".opencode/", ".git/", ".githooks/", ".github/", "opencode.json", ".mcp.json",
             ".gitmodules")
PROTECTED_NAMES = ("opencode.json", ".mcp.json")       # protected in any folder
ENV_KEEP = ("PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "TERM", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
            "CLAUDE_CONFIG_DIR", "CODEX_HOME", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "HOMEDRIVE",
            "HOMEPATH", "USERNAME", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")


class GitError(RuntimeError):
    pass


# Tasks run in parallel threads on ONE repository. Worktree add/remove/prune and branch create/delete all touch the repo's
# shared metadata (.git/worktrees, refs, packed-refs), so they take turns per repository; a lock file another process
# still holds (git itself, an antivirus scan on Windows) is retried a few times instead of failing the whole task.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
LOCK_RETRIES = 4


def _repo_lock(repo: str | Path) -> threading.RLock:
    key = os.path.normcase(os.path.realpath(str(repo)))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _lock_contention(text: str) -> bool:
    t = (text or "").lower()
    return ".lock" in t and ("file exists" in t or "unable to create" in t or "another git process" in t)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """An allow-listed environment for agents and check commands: no API keys or tokens unless named in `extra`."""
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_KEEP}
    env.update(extra or {})
    return env


def _no_hooks_path() -> str:
    """`core.hooksPath` for every JARVES git command (#1381): a path BELOW a file, so it can never be a folder and no
    process (an agent included) can put a hook there. POSIX: `/dev/null` (root-owned, in root-owned /dev). Windows: a
    path below `kernel32.dll` in System32 (owned by TrustedInstaller); `NUL\\x` would be a plain relative path there."""
    if os.name == "nt":
        return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "kernel32.dll", "no-hooks")
    return "/dev/null"


NO_HOOKS = _no_hooks_path()


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Other config-run commands (filter/merge drivers, gpg.program) and
    config-defined hooks (`hook.<name>.command`, newer git) are not covered here."""
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false"]
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0"}), encoding="utf-8",
                           errors="replace")
        if p.returncode == 0 or attempt == LOCK_RETRIES or not _lock_contention(p.stderr):
            break
        time.sleep(0.15 * (attempt + 1))
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout or "git failed").strip()[:300])
    return p


def is_repo(path: str) -> bool:
    try:
        return git(path, "rev-parse", "--git-dir", check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def current_branch(path: str) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def project_root(home: Path, pid: int) -> Path:
    return Path(home) / "projects" / str(pid)


def integration_branch(pid: int) -> str:
    return f"jarves/p{pid}/integration"


def task_branch(pid: int, seq: int, attempt: int) -> str:
    return f"jarves/p{pid}/t{seq}-a{attempt}"


def ensure_integration(home: Path, repo: str, pid: int, base: str = "") -> Path:
    """The integration worktree, created from `base` (default: the repo's current HEAD) once."""
    wt = project_root(home, pid) / "integration"
    br = integration_branch(pid)
    if (wt / ".git").exists():
        return wt
    wt.parent.mkdir(parents=True, exist_ok=True)
    start = base or "HEAD"
    with _repo_lock(repo):
        if git(repo, "rev-parse", "--verify", "--quiet", br, check=False).returncode == 0:
            git(repo, "worktree", "add", str(wt), br)
        else:
            git(repo, "worktree", "add", "--no-track", "-b", br, str(wt), start)
    return wt


def new_task_worktree(home: Path, repo: str, pid: int, seq: int, attempt: int) -> tuple[Path, str]:
    """A fresh worktree on a fresh branch from the CURRENT integration branch (earlier tasks' code is in it)."""
    br = task_branch(pid, seq, attempt)
    wt = project_root(home, pid) / "wt" / f"t{seq}-a{attempt}"
    with _repo_lock(repo):
        remove_worktree(repo, wt, br)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--no-track", "-b", br, str(wt), integration_branch(pid))
    return wt, br


def detached_worktree(home: Path, repo: str, pid: int, sha: str) -> Path:
    """A throw-away worktree at one exact commit (a release runs there, never in your folder)."""
    wt = project_root(home, pid) / "release" / sha[:12]
    with _repo_lock(repo):
        remove_worktree(repo, wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--detach", str(wt), sha)
    return wt


def remove_worktree(repo: str, wt: Path, branch: str = "") -> None:
    """Only ever called for paths under <home>/projects (callers build them); a missing one is fine."""
    if "projects" not in Path(wt).parts:
        raise GitError("refusing to remove a folder outside the JARVES projects area")
    with _repo_lock(repo):
        git(repo, "worktree", "remove", "--force", str(wt), check=False)
        if Path(wt).exists():
            shutil.rmtree(wt, ignore_errors=True)
        git(repo, "worktree", "prune", check=False)
        if branch:
            git(repo, "branch", "-D", branch, check=False)


def commit_all(wt: Path, message: str) -> bool:
    git(wt, "add", "-A")
    if git(wt, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    git(wt, "commit", "-m", message[:200], "--no-verify")
    return True


def changed_files(wt: Path, base: str) -> list[str]:
    """Every path the task changed, as plain text. -z: git would otherwise print a non-ASCII name in quotes
    (octal escapes inside `"..."`), which no PROTECTED prefix matches. --no-renames: a rename lists both
    sides, so moving a file out of `.github/` still shows the protected source (JAR-574)."""
    out = git(wt, "diff", "--name-only", "-z", "--no-renames", f"{base}...HEAD").stdout
    return [x.replace("\\", "/") for x in out.split("\0") if x]


LINK_MODES = ("120000", "160000")       # a symlink and a gitlink (submodule entry)


def link_files(wt: Path, base: str) -> list[str]:
    """Paths the task added or changed as a symlink or a gitlink (#1381). Their path text can look harmless while a
    symlink points into `.github/` or `.claude/` and a gitlink pulls in another repository, so they count as protected."""
    out = git(wt, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", f"{base}...HEAD").stdout.split("\0")
    return [out[i + 1].replace("\\", "/") for i in range(0, len(out) - 1, 2)
            if out[i].startswith(":") and out[i].split()[1:2] and out[i].split()[1] in LINK_MODES]


def diffstat(wt: Path, base: str) -> str:
    out = git(wt, "diff", "--shortstat", f"{base}...HEAD", check=False).stdout.strip()
    return out or "no changes"


def in_scope(path: str, scope: list[str]) -> bool:
    if not scope:
        return True
    p = path.replace("\\", "/")
    for g in scope:
        g = g.replace("\\", "/").rstrip("/")
        if fnmatch.fnmatchcase(p, g) or p == g or p.startswith(g + "/") or (
                "*" in g and fnmatch.fnmatchcase(p, g + "/*")):
            return True
    return False


# a Windows 8.3 short name (`CLAUDE~1`, `GITHUB~1.YML`): a Windows checkout writes `CLAUDE~1/x` into the existing
# `.claude/` folder, so the name cannot be matched against PROTECTED. Refused in any folder (JAR-574)
_SHORT_NAME = re.compile(r"^[^.~/]{1,6}~\d+(\.[^./]{0,3})?$")


def is_protected(path: str) -> bool:
    """True for a file an agent may never change, in any letter case and with either slash."""
    p = path.replace("\\", "/").casefold()
    parts = p.split("/")
    return (p.startswith(PROTECTED) or parts[-1] in PROTECTED_NAMES
            or any(_SHORT_NAME.match(x) for x in parts))


def violations(files: list[str], scope: list[str]) -> tuple[list[str], list[str]]:
    """(protected files touched, files outside the declared scope)."""
    prot = [f for f in files if is_protected(f)]
    out = [f for f in files if f not in prot and not in_scope(f, scope)]
    return prot, out


def merge_in(wt: Path, branch: str, ident: bool = True) -> list[str]:
    """Bring `branch` into the worktree's branch. Returns conflicting files ([] = merged); a conflict is aborted
    (nothing changes). A refusal without conflicts (e.g. an untracked file in the way) returns git's own reason.
    Takes the repository's lock: another task's worktree remove/prune/branch -D must not run during the merge (JAR-189)."""
    common = git(wt, "rev-parse", "--git-common-dir", check=False).stdout.strip()
    root = (Path(wt) / common).resolve() if common else Path(wt)
    with _repo_lock(root.parent if root.name == ".git" else root):
        p = git(wt, "merge", "--no-edit", branch, check=False, ident=ident)
        if p.returncode == 0:
            return []
        files = [x for x in git(wt, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split() if x]
        git(wt, "merge", "--abort", check=False)
    if files:
        return files
    why = " ".join((p.stderr or p.stdout or "merge failed").split())[:200]
    return [f"(not merged: {why})"]


def fast_forward(integration_wt: Path, branch: str) -> None:
    git(integration_wt, "merge", "--ff-only", branch)


# #1592: a check command (run by JARVES, or by the agent through its `Bash(<check>:*)` rule) executes worktree code
# as the owner's user. That code can plant something the owner's own git later runs: a `filter.<n>.clean` entry in the
# shared config, a hook in `<common>/hooks/`, a `config.worktree` or an `info/attributes` file. No permission rule stops
# executed code, so each such run is wrapped in `config_guard`: the guarded files (GUARDED_FILES below, the worktree's
# `.git` pointer, `<gitdir>/commondir` and `<gitdir>/gitdir`, and every file in `<common>/hooks/`) are read when the
# run starts. A symlinked one is guarded as a link: the link itself and the content behind it. If any changed when it
# ends, it is put back, the rejected text is kept in `<common>/jarves-rejected/`, and the run fails. Overlapping runs
# on one repository share the snapshot taken by the first, so a later run never takes a planted entry as its baseline.
# On Linux the run is contained (`contain`): every process it started, a detached one included, is killed before the
# guard compares. A fork loop that outruns that sweep makes the reaper exit 125 (the run fails), but a survivor's
# later write is not seen. NOT contained on Windows or macOS (UNVERIFIED there, tracked in #1665): a detached
# descendant can still write after the run. Also not guarded: ~/.gitconfig and config include targets; a change undone
# before the run ends is not seen. Any change during a run is reverted, the owner's own `git config` in that repository
# included (the text is kept). JARVES's own worktree calls never change a guarded file (`--no-track`: no `branch.*`
# entry), so the snapshot is never re-taken during a run: a write timed to land during a JARVES call is still undone.
# A task branch made before this fix may still have a `branch.*` entry: its `branch -D` during another run is then
# taken for a change and undone (a false alarm, once per such branch, until that branch is removed outside a run).
# A file over SNAP_CAP is never read whole: one at the start refuses the run, one at the end counts as a change.
# At most KEEP_REJECTED copies (each at most SNAP_CAP) are kept.
SNAP_CAP = 1 << 20
KEEP_REJECTED = 20
GUARDED_FILES = ("config", "config.worktree", "info/attributes")
_GUARDS: dict[str, dict] = {}
_GUARDS_LOCK = threading.RLock()
TAMPERED = "changed the shared git config; the change was undone"
_REAPER = str(Path(__file__).with_name("_reaper.py"))
REAPER_TIMEOUT = "jarves-reaper: the run timed out"            # the reaper's last stderr line (exit 124)


def guarded_files(wt: str | Path) -> list[tuple[str, str, str]]:
    """(path, kind, rejected-dir) for every guarded file or folder of the worktree, found on disk without running git.
    Raises GitError/OSError when `wt` is not a git worktree: the caller refuses the run."""
    dot = Path(wt) / ".git"
    gd, out = dot, []
    if not dot.is_dir():
        text = dot.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            raise GitError(f"{dot} is not a git pointer")
        gd = Path(wt) / text[7:].strip()                 # an absolute gitdir replaces the worktree part
        out.append((dot, "file"))
    common = gd / (gd / "commondir").read_text(encoding="utf-8").strip() if (gd / "commondir").is_file() else gd
    if not (common / "config").is_file():
        raise GitError(f"no shared git config found for {wt}")
    out += [(common / n, "file") for n in GUARDED_FILES] + [(gd / n, "file") for n in ("config.worktree", "commondir", "gitdir")]
    out.append((common / "hooks", "dir"))
    rej = os.path.join(os.path.realpath(common), "jarves-rejected")
    seen: dict[str, tuple[str, str, str]] = {}
    for path, kind in out:                               # the folder resolved, the name itself not: a link is guarded as a link
        key = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
        seen.setdefault(key, (key, kind, rej))
    return list(seen.values())


def _read(path: str):
    """(first SNAP_CAP bytes, mode, full size) or None for a missing file; never reads more than SNAP_CAP + 1."""
    try:
        with open(path, "rb") as f:
            st = os.fstat(f.fileno())
            return f.read(SNAP_CAP), st.st_mode & 0o7777, st.st_size
    except FileNotFoundError:
        return None


def _content(path: str, kind: str):
    if kind == "file":
        return _read(path)
    try:
        names = sorted(e.name for e in os.scandir(path) if e.is_file())
    except FileNotFoundError:
        return {}
    return {n: _read(os.path.join(path, n)) for n in names}


def _snap(path: str, kind: str):
    """(symlink target or None, content read through it): a link swapped for a planted file is a change too."""
    return (os.readlink(path) if os.path.islink(path) else None), _content(path, kind)


def _too_large(snap, kind: str) -> bool:
    return any(d and d[2] > SNAP_CAP for d in ([snap[1]] if kind == "file" else snap[1].values()))


def _reject(rej: str, path: str, now) -> None:
    """Keep the rejected text for the owner: one new file per incident, never overwritten."""
    os.makedirs(rej, exist_ok=True)
    fd, _ = tempfile.mkstemp(prefix=time.strftime("%Y%m%d-%H%M%S-") + os.path.basename(path) + "-", dir=rej)
    with os.fdopen(fd, "wb") as f:
        f.write(now[0])                                  # at most SNAP_CAP bytes
    kept = sorted(os.scandir(rej), key=lambda e: (e.stat().st_mtime, e.name))
    for old in kept[:-KEEP_REJECTED]:                    # the oldest go first
        os.remove(old.path)


def _put(path: str, data) -> None:
    """Put `data` (bytes, mode) back at `path` atomically (unique temp file + os.replace); None removes the file."""
    if data is None:
        os.remove(path)
        return
    fd, tmp = tempfile.mkstemp(prefix=".jarves-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data[0])
        os.chmod(tmp, data[1])
        os.replace(tmp, path)
    except OSError:
        os.unlink(tmp)
        raise


def _relink(path: str, kind: str, rej: str, want: str | None, have: str | None) -> None:
    """Put the original link (or no link) back at `path`; whatever stands there now is kept in `rej` first."""
    if have is not None:
        _reject(rej, path, (f"symlink to {have}".encode(),))
        os.remove(path)
    elif os.path.isdir(path):                            # a planted real folder where the link was: moved aside whole
        os.makedirs(rej, exist_ok=True)
        os.rename(path, tempfile.mkdtemp(prefix=os.path.basename(path) + "-", dir=rej))
    elif os.path.lexists(path):
        _reject(rej, path, _read(path))
        os.remove(path)
    if want is None:
        if kind == "dir":
            os.makedirs(path, exist_ok=True)
        return
    tmp = os.path.join(os.path.dirname(path), f".jarves-{os.getpid()}-{threading.get_ident()}-link")
    os.symlink(want, tmp)                                # unique temp link + os.replace: atomic
    os.replace(tmp, path)


def _restore(path: str, kind: str, rej: str, data, now) -> None:
    if data[0] != now[0]:
        _relink(path, kind, rej, data[0], now[0])
        now = _snap(path, kind)
    real = os.path.realpath(path)                        # content goes to the link's target, so a link stays a link
    pairs = [(real, data[1], now[1])] if kind == "file" else [
        (os.path.join(real, n), data[1].get(n), now[1].get(n)) for n in sorted(set(data[1]) | set(now[1]))]
    for p, want, have in pairs:
        if want != have:
            if have is not None:
                _reject(rej, p, have)
            _put(p, want)


@contextmanager
def config_guard(files: list[tuple[str, str, str]]):
    """Guards `guarded_files(wt)`. Yields a dict whose "tampered" turns True when one of them changed during the run
    (see #1592 above); "error" says why a changed file could not be put back."""
    mark: dict = {"tampered": False}
    with _GUARDS_LOCK:
        for path, kind, rej in files:
            if path not in _GUARDS:
                try:
                    data = _snap(path, kind)
                    why = "is too large to guard" if _too_large(data, kind) else ""
                except OSError as e:                     # e.g. a folder where a file belongs, or no permission
                    why = f"cannot be read ({e.strerror or e})"
                if why:
                    for p2, _k, _r in files[:files.index((path, kind, rej))]:    # undo this run's registrations
                        _GUARDS[p2]["marks"].remove(mark)
                        if not _GUARDS[p2]["marks"]:
                            del _GUARDS[p2]
                    raise GitError(f"{path} {why}")
                _GUARDS[path] = {"kind": kind, "rej": rej, "data": data, "marks": []}
            _GUARDS[path]["marks"].append(mark)
    try:
        yield mark
    finally:
        with _GUARDS_LOCK:
            for path, _kind, _rej in files:
                g = _GUARDS[path]
                try:
                    now = _snap(path, g["kind"])
                    if now != g["data"]:
                        for m in g["marks"]:             # every run active on this file is suspect, not only this one
                            m["tampered"] = True
                        _restore(path, g["kind"], g["rej"], g["data"], now)
                except OSError as e:                     # e.g. the file was replaced by a folder: still a failure
                    mark.update(tampered=True, error=f"{path} could not be restored: {e}")
                g["marks"].remove(mark)
                if not g["marks"]:
                    del _GUARDS[path]


@contextmanager
def term_blocked(on: bool = True):
    """SIGTERM blocked in this thread while a reaper starts: the child inherits the mask, and the reaper unblocks it
    only after its handler is in place, so an early SIGTERM is never the default (die, leaving its processes)."""
    if not on or not hasattr(signal, "pthread_sigmask"):
        yield
        return
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def contain(argv: list[str], timeout: float = 0) -> tuple[list[str], bool]:
    """On Linux: argv run under `_reaper.py`, a child subreaper that kills every process the run left behind (a
    detached one too) before it exits; REAPER_TIMEOUT on stderr = it hit `timeout`. Elsewhere argv is unchanged (see #1592 above)."""
    if not sys.platform.startswith("linux"):
        return list(argv), False
    return [sys.executable, "-I", _REAPER, str(float(timeout)), *argv], True


def run_check(wt: Path, argv: list[str], timeout: float = 900, env_keep: list[str] | None = None) -> tuple[bool, str]:
    """The project's check command, run by JARVES (never by the agent): shell=False, allow-listed env, in the worktree,
    inside `config_guard` and `contain` (#1592). env_keep: extra variable names the owner allowed for this command."""
    if not argv:
        return True, ""
    extra = {k: os.environ[k] for k in (env_keep or []) if k in os.environ}
    try:
        files = guarded_files(wt)
    except (GitError, OSError) as e:
        return False, f"check refused: the shared git config cannot be guarded ({e})"
    cmd, contained = contain(argv, timeout)
    timed_out = False
    try:
        with config_guard(files) as guard:
            try:
                p = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True,
                                   timeout=timeout + 30 if contained else timeout, creationflags=_NOWIN,
                                   env=child_env(extra), encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
                timed_out = contained and p.returncode == 124 and (p.stderr or "").rstrip().endswith(REAPER_TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
    except (GitError, OSError) as e:
        return False, f"check could not start: {e}"
    if timed_out:
        return False, "check timed out" + (f"; it {TAMPERED} {guard.get('error', '')}".rstrip() if guard["tampered"] else "")
    if guard["tampered"]:
        return False, f"check {TAMPERED} {guard.get('error', '')}".strip()
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, re.sub(r"\s+", " ", out[-600:])


def repo_state(path: str) -> dict:
    """Branch, commit count and uncommitted changes of a folder (read-only)."""
    out = {"branch": "", "commits": 0, "dirty": 0, "untracked": 0}
    try:
        out["branch"] = current_branch(path)
        out["commits"] = int(git(path, "rev-list", "--count", "HEAD", check=False).stdout.strip() or 0)
        for line in git(path, "status", "--porcelain", check=False).stdout.splitlines():
            if line.startswith("??"):
                out["untracked"] += 1
            elif line.strip():
                out["dirty"] += 1
    except (GitError, OSError, ValueError):
        pass
    return out
