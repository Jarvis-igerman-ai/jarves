"""Git for the project manager. Every JARVES worktree lives under `<home>/projects/<id>/` (never inside the owner's
repository, so tests/linters at the repo root never see them and agents do not inherit the owner's CLAUDE.md of a parent
folder). The owner's checkout is never touched: the integration branch `jarves/p<ID>/integration` has its own worktree,
merges are fast-forward only after the check ran on the merged result. Nothing here pushes on its own; the only push is
`forge.push_branch` (GitHub write mode), which runs through `git()`."""
from __future__ import annotations

import contextlib
import fnmatch
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import logs

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
# #1592 review R3: JARVES's own git calls must not write config while a guarded run is active (it would be reverted and
# every active run rejected): no upstream tracking on `worktree add -b` (branch.<n>.* entries) and no relative-worktree
# format bump (extensions.relativeWorktrees, newer git). Unknown keys are ignored by older git.
CONFIG_PINS = ["-c", "branch.autoSetupMerge=false", "-c", "worktree.useRelativePaths=false"]


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Other config-run commands (filter/merge drivers, gpg.program) and
    config-defined hooks (`hook.<name>.command`, newer git) are not covered here. env: extra variables (forge's push)."""
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false", *CONFIG_PINS]
    if _GUARDS and not check_guards():    # #1592: never read an entry a running agent or check planted
        p = subprocess.CompletedProcess(["git", *args], 1, "", "a git config file was changed and could not be "
                                        "restored: git not run")
        if check:
            raise GitError(p.stderr)
        return p
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0", **(env or {})}), encoding="utf-8",
                           errors="replace")
        if p.returncode == 0 or attempt == LOCK_RETRIES or not _lock_contention(p.stderr):
            break
        time.sleep(0.15 * (attempt + 1))
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout or "git failed").strip()[:300])
    return p


# #1592: agent-written code (the agent, its tests, the check command) runs as the owner's user and can write git config
# that git reads: the repository's shared `.git/config`, the owner's global `~/.gitconfig` and XDG `git/config` (a
# `filter.<n>.clean` entry runs a command on the next JARVES or owner git call), `config.worktree` files, every file
# those configs include (also a named but missing one), and the worktree's `.git` pointer and
# `commondir`/`gitdir` files (they would point later git calls at another, unguarded config). While such code runs these
# files are guarded: a copy (or "absent") taken when the run starts; every JARVES git call and the end of the run
# compare and restore them, and the run is rejected. Every run active on that file is rejected, because the writer
# cannot be told apart. Limits: not an OS sandbox (a process the run leaves behind can still write after it ends; the
# owner's own git can read a planted entry during a run; the system config, an owner-set GIT_CONFIG_GLOBAL, an include
# inside an included file that does not apply to the guarded worktree and a conditional include that applies only in
# another folder are not guarded). A file that could not be restored stays guarded after the run ends ("failed"):
# every JARVES git call retries the restore and refuses to run until it succeeds. ANY change during a run is reverted, the owner's own `git config`/`git remote set-url` included: the
# replaced content is kept next to the file in `<name>.jarves-rejected-*/<name>`, named in the rejection and in a
# PROJECT_GIT_CONFIG_RESTORED log line. JARVES's own git calls write no config (CONFIG_PINS).
_GUARDS: dict[str, dict] = {}            # file path -> {"data": trusted bytes or None (absent), "runs": [state dicts]}
_GUARDS_LOCK = threading.RLock()


def _global_configs() -> list[Path]:
    """The global config files git reads for JARVES's children (child_env keeps HOME/XDG_CONFIG_HOME, drops
    GIT_CONFIG_GLOBAL)."""
    home = Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [home / ".gitconfig", xdg / "git" / "config"]


def _guarded_files(wt: str | Path) -> list[Path]:
    """Every file `config_guard` watches for the repository at `wt`; [] when `wt` is not a git repository."""
    p = git(wt, "rev-parse", "--git-common-dir", "--git-dir", check=False)
    lines = p.stdout.split("\n")
    if p.returncode != 0 or len(lines) < 2 or not lines[0].strip():
        return []
    common, gdir = ((Path(wt) / x.strip()).resolve() for x in lines[:2])
    files = [common / "config", common / "config.worktree", *_global_configs()]
    if gdir != common:                     # a linked worktree: its pointer files
        files += [Path(wt) / ".git", gdir / "commondir", gdir / "gitdir", gdir / "config.worktree"]
    return [Path(os.path.abspath(f)) for f in files + _config_files(wt)]


def _config_files(wt: str | Path) -> list[Path]:
    """Every non-system file git reads as config at `wt` (includes too), plus every file an include names, even a
    missing one: a run could create it (round-4 R1)."""
    recs = git(wt, "config", "--list", "--show-origin", "--show-scope", "-z", check=False).stdout.split("\0")
    out: list[Path] = []
    for i in range(0, len(recs) - 2, 3):
        scope, origin, (key, _, val) = recs[i], recs[i + 1], recs[i + 2].partition("\n")
        if scope == "system" or not origin.startswith("file:"):
            continue
        src = Path(wt) / origin[5:]
        out.append(src)
        key = key.lower()
        if val and (key == "include.path" or (key.startswith("includeif.") and key.endswith(".path"))):
            target = Path(os.path.expanduser(val))
            out.append(target if target.is_absolute() else src.parent / target)
    return out


def _keep_rejected(path: Path, move: bool = False) -> str:
    """Move (a folder, or move=True) or copy what replaced the trusted file into a fresh `<name>.jarves-rejected-*`
    folder (nothing is lost). '' when nothing was there."""
    if not os.path.lexists(path):
        return ""
    side = Path(tempfile.mkdtemp(prefix=f"{path.name}.jarves-rejected-", dir=path.parent))
    if move or (path.is_dir() and not path.is_symlink()):
        os.replace(path, side / path.name)
    else:
        try:
            shutil.copyfile(path, side / path.name)
        except OSError:                   # a dangling or unreadable link: the link itself is replaced below
            pass
    if not any(side.iterdir()):
        side.rmdir()
        return ""
    return str(side / path.name)


def _restore(path: Path, data: bytes, mode: int | None = None) -> None:
    """Atomically put the trusted bytes back, with the file's original permission bits (mkstemp makes 0600; R4)."""
    fd, tmp = tempfile.mkstemp(prefix=f"{path.name}.jarves-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


_UNREADABLE = object()                   # a guarded path that exists but cannot be read as a file
_UNSET = object()


def _remove(path: Path) -> None:
    """Remove what a run put where no file was."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


def _read(path: Path) -> bytes | None:
    """The file's bytes, None when absent; raises OSError when it exists but cannot be read as a file."""
    if not os.path.lexists(path):
        return None
    return path.read_bytes()


def check_guards() -> bool:
    """Restore every guarded shared config that changed; marks that repository's active runs ("changed", "kept",
    "unrestored"). Never raises: an unreadable config counts as changed. False when a config could NOT be restored
    (it is still untrusted, so no git command may run). Every call retries the restore; the same unrestorable content
    is kept and logged once (review R2). A guard whose runs all ended unrestored stays until a restore succeeds."""
    safe = True
    with _GUARDS_LOCK:
        for key, g in list(_GUARDS.items()):
            path = Path(key)
            try:
                cur: object = _read(path)
            except OSError:               # a folder, locked on Windows: changed
                cur = _UNREADABLE
            if cur == g["data"]:
                if "failed" in g:         # a quarantined file is trusted again (put back by the owner)
                    g.pop("failed")
                    if not g["runs"]:
                        del _GUARDS[key]
                continue
            new = g.get("failed", _UNSET) != cur     # the same unrestorable content is kept and logged once
            kept, err = "", ""
            if new:
                try:
                    kept = _keep_rejected(path, move=g["data"] is None)
                except OSError as e:      # round-4 R2: no backup, but the restore is still tried
                    err = f"backup failed: {e}"[:200]
            try:
                _remove(path) if g["data"] is None else _restore(path, g["data"], g.get("mode"))
                g.pop("failed", None)
            except OSError as e:
                err, safe, g["failed"] = str(e)[:200], False, cur
            if new or "failed" not in g:
                logs.warn("projects", "PROJECT_GIT_CONFIG_RESTORED", "git config changed during an agent run",
                          path=key, backup=kept, ok="failed" not in g, error=err)
            for st in g["runs"]:
                st["changed"] = True
                st["kept"] = st.get("kept") or kept
                st["unrestored"] = st.get("unrestored") or "failed" in g
            if "failed" not in g and not g["runs"]:
                del _GUARDS[key]          # a quarantined guard is released once its restore succeeded
    return safe


def guard_note(state: dict) -> str:
    """The rejection text's tail: where the replaced config was kept, or that it could not be restored."""
    if state.get("unrestored"):
        return "; it could NOT be restored: check it before running git there"
    return "; it was restored" + (f", the replaced content is kept in {state['kept']}" if state.get("kept") else "")


@contextlib.contextmanager
def config_guard(wt: str | Path):
    """Guard the git config files of the repository at `wt` (`_guarded_files`) while agent-written code runs. Yields a dict whose
    "changed" is True after the block when the config was changed (it is already restored). Not a repository: no-op."""
    state = {"changed": False}
    try:
        files = _guarded_files(wt)
    except (OSError, subprocess.SubprocessError):
        files = []
    if not files:
        yield state
        return
    keys, held = list(dict.fromkeys(str(f) for f in files)), []
    with _GUARDS_LOCK:                    # review R1: snapshot under the lock, after restoring, only for new keys:
        check_guards()                    # a run ending meanwhile can never hand us its planted bytes as trusted
        for k in keys:
            if k not in _GUARDS:
                try:
                    _GUARDS[k] = {"data": _read(Path(k)), "mode": _mode(Path(k)), "runs": []}
                except OSError:           # unreadable now (a folder): nothing trusted to restore
                    continue
            _GUARDS[k]["runs"].append(state)
            held.append(k)
    try:
        yield state
    finally:
        with _GUARDS_LOCK:
            try:
                check_guards()
            finally:
                for k in held:
                    g = _GUARDS.get(k)
                    if g is None:
                        continue
                    g["runs"] = [st for st in g["runs"] if st is not state]   # by identity: states can be equal
                    if not g["runs"] and "failed" not in g:    # round-4 R2: an unrestored file stays guarded
                        del _GUARDS[k]


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
            git(repo, "worktree", "add", "-b", br, str(wt), start)
    return wt


def new_task_worktree(home: Path, repo: str, pid: int, seq: int, attempt: int) -> tuple[Path, str]:
    """A fresh worktree on a fresh branch from the CURRENT integration branch (earlier tasks' code is in it)."""
    br = task_branch(pid, seq, attempt)
    wt = project_root(home, pid) / "wt" / f"t{seq}-a{attempt}"
    with _repo_lock(repo):
        remove_worktree(repo, wt, br)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-b", br, str(wt), integration_branch(pid))
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


def run_check(wt: Path, argv: list[str], timeout: float = 900, env_keep: list[str] | None = None) -> tuple[bool, str]:
    """The project's check command, run by JARVES (never by the agent): shell=False, allow-listed env, in the worktree.
    env_keep: extra variable names the owner allowed for this command (a release may need a token)."""
    if not argv:
        return True, ""
    extra = {k: os.environ[k] for k in (env_keep or []) if k in os.environ}
    with config_guard(wt) as guard:
        try:
            p = subprocess.run(argv, cwd=str(wt), capture_output=True, text=True, timeout=timeout, creationflags=_NOWIN,
                               env=child_env(extra), encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            p = None
        except OSError as e:
            return False, f"check could not start: {e}"
    if guard["changed"]:
        return False, "the check changed a git config file and failed (#1592)" + \
            guard_note(guard)
    if p is None:
        return False, "check timed out"
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
