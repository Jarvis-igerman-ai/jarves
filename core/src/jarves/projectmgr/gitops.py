"""Git for the project manager. Every JARVES worktree lives under `<home>/projects/<id>/` (never inside the owner's
repository, so tests/linters at the repo root never see them and agents do not inherit the owner's CLAUDE.md of a parent
folder). The owner's checkout is never touched: the integration branch `jarves/p<ID>/integration` has its own worktree,
merges are fast-forward only after the check ran on the merged result. JARVES never pushes."""
from __future__ import annotations

import contextlib
import fnmatch
import os
import re
import itertools
import shutil
import stat
import subprocess
import tempfile
import threading
import time
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


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True,
        _guard: bool = True) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Other config-run commands (filter/merge drivers, gpg.program) and
    config-defined hooks (`hook.<name>.command`, newer git) are covered by the shared-file guard below (#1533)."""
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false"]
    pin = _pin_of(cwd, adopt=_guard)
    common = pin[1] if pin else _find_common(cwd) if _guard and (_GUARDS or _LAST) else None
    keys = [k for k in (_user_key(), common) if k] if _guard and (_GUARDS or _LAST) else []
    why = next((w for k in keys if (w := _guard_git(k))), "")
    if why:
        if check:
            raise GitError(why)
        return subprocess.CompletedProcess(["git", *args], 1, "", why)
    env = child_env({"GIT_TERMINAL_PROMPT": "0", **({"GIT_DIR": pin[0], "GIT_COMMON_DIR": pin[1],
                                                       "GIT_WORK_TREE": pin[2]} if pin else {})})
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=env, encoding="utf-8", errors="replace")
        if p.returncode == 0 or attempt == LOCK_RETRIES or not _lock_contention(p.stderr):
            break
        time.sleep(0.15 * (attempt + 1))
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout or "git failed").strip()[:300])
    return p


# #1533: shared files that every JARVES git command reads: the repository's common git folder and the owner's
# user-level git config/attributes (an agent runs as the same user, review R1). An agent, or a check running its
# code, that writes them could name a command (filter/merge driver, config hook) that git runs for JARVES or later for
# the owner; no git option skips them. While any agent run or check is active in a repository, JARVES keeps ONE
# baseline of that repository's files; the user-level files have ONE baseline for the whole process, taken by the
# first run in any repository and checked before every JARVES git command in every repository. Before such a command
# and when each run ends, a change is put back (temp file + os.replace, the baseline's file mode; the rejected
# bytes go to a new `<name>.jarves-rejected-<time>-<n>-<random>` file, the newest KEEP_REJECTED per file are kept)
# and fails every run active at that moment. After the last run, a later
# change (a left-behind process, or your own edit) is never reverted: one that adds or changes a key git may run
# outside LATE_OK, or any change to an attributes file, makes JARVES git refuse to run there until JARVES restarts and
# names the added entries; LATE_OK edits (`git remote add`, `push -u`) are accepted. A deleted file is ignored.
# The files a guarded config includes (`include.path`, `includeIf.*.path`) are guarded with it (review R1 of 82f81a11).
# JARVES's own git never writes them (`worktree add --no-track`). The worktree's `.git` / `commondir` pointers are read
# once, when JARVES creates (or, after a restart, first uses and checks) the worktree; every later JARVES git command
# there, or in a folder below it, runs with GIT_DIR/GIT_COMMON_DIR, so a planted pointer is never followed. Still
# open (UNVERIFIED): a write between this check and git's own read (a parallel agent can race it); a baseline taken after a restart; the system
# config (admin-owned) and a custom `core.attributesFile` path.
SHARED_GIT = ("config", "config.worktree", "info/attributes")
# Review R2 of 82f81a11: the only config edits accepted after the last run (an allowlist; anything else refuses).
LATE_OK = re.compile(r"^(remote\..+\.(url|pushurl|fetch)|branch\..+\.(remote|merge|rebase)|user\.[^.]+)$",
                     re.IGNORECASE | re.DOTALL)
HELPER_URL = re.compile(rb"^\s*[A-Za-z0-9+.-]*::")      # `ext::cmd`, `fd::` and other remote-helper transports
INCLUDE_KEY = r"^include(if\..+)?\.path$"
REJECTED = ".jarves-rejected-"
KEEP_REJECTED = 5
_SEQ = itertools.count()
_GUARDS: dict[str, list] = {}           # common dir -> [the `changed` lists of the active runs, {file: bytes or None}]
_LAST: dict[str, dict] = {}             # common dir -> the files as the last run left them
_GUARD_LOCK = threading.RLock()
_PIN: dict[str, tuple[str, str, str]] = {}  # JARVES worktree -> (its git dir, the common dir, the worktree)
_COMMON: dict[str, str] = {}            # folder -> its common dir
_PROJECTS: dict[str, str] = {}          # a project's JARVES folder -> the owner's repository (review R4 of 82f81a11)


def register_project(home: Path, pid: int, repo: str) -> None:
    """A worktree below this project's JARVES folder is pinned (and checked) on its first use after a restart."""
    _PROJECTS[_key(project_root(home, pid))] = str(repo)


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _pin_of(cwd: str | Path, adopt: bool = True) -> tuple[str, str, str] | None:
    """The pin of `cwd` or of the JARVES worktree it lies in (review R2). adopt: an unpinned worktree below a
    registered project folder is pinned now; GitError when it does not point into its repository (R4 of 82f81a11)."""
    k = _key(cwd)
    pin = _PIN.get(k) or next((v for w, v in list(_PIN.items()) if k.startswith(w + os.sep)), None)
    root = next((r for r in list(_PROJECTS) if k.startswith(r + os.sep)), None) if adopt and not pin else None
    if root:
        d = Path(k)
        while _key(d) != root and not (d / ".git").exists():
            d = d.parent
        if _key(d) != root:
            _pin(_PROJECTS[root], d)
            return _PIN[_key(d)]
    return pin


def _pin(repo: str, wt: Path) -> None:
    """Pin `wt` only if it points into `repo`'s own common git folder (review R3)."""
    _PIN.pop(_key(wt), None)
    _COMMON.pop(_key(wt), None)
    out = git(wt, "rev-parse", "--absolute-git-dir", "--git-common-dir", _guard=False).stdout.splitlines()
    gd, common = out[0].strip(), _key(Path(wt) / out[1].strip())
    own = _key(Path(repo) / git(repo, "rev-parse", "--git-common-dir").stdout.strip())
    if common != own or _key(Path(gd).parent) != _key(Path(own) / "worktrees"):
        raise GitError(f"the worktree {wt} does not point into the repository's own git folder; JARVES does not use "
                       "it (#1533)")
    _PIN[_key(wt)] = (gd, common, os.path.realpath(str(wt)))
    _COMMON[_key(repo)] = common


def common_dir(cwd: str | Path) -> str | None:
    """The repository's common git folder (normalised, cached); None outside a repository."""
    pin = _pin_of(cwd)
    if pin or _key(cwd) in _COMMON:
        return pin[1] if pin else _COMMON[_key(cwd)]
    try:
        p = git(cwd, "rev-parse", "--git-common-dir", check=False, _guard=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    return _COMMON.setdefault(_key(cwd), _key(Path(cwd) / p.stdout.strip()))


def _find_common(cwd: str | Path) -> str | None:
    """Without running git: the common dir of the repository `cwd` lies in, as git would find it (review R2)."""
    d = Path(os.path.realpath(str(cwd)))
    for p in (d, *d.parents):
        g = p / ".git"
        try:
            if g.is_dir():
                return _key(g)
            if g.is_file():
                gd = p / g.read_text(encoding="utf-8", errors="replace").partition("gitdir:")[2].strip()
                cd = gd / "commondir"
                return _key(gd / cd.read_text(encoding="utf-8", errors="replace").strip()) if cd.is_file() else _key(gd)
        except OSError:
            return None
    return None


def _user_files() -> set[Path]:
    """The user-level git config and attributes, where git finds them for JARVES (the same HOME/XDG_CONFIG_HOME)."""
    home = Path(os.environ.get("HOME") or Path.home())
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "git"
    return {home / ".gitconfig", xdg / "config", xdg / "attributes"}


USER = "user:"


def _user_key() -> str:
    """The user-level files have ONE baseline for the whole process, shared by every repository (review R1 of
    3d423b40): a run in one repository must not let them be planted for, or adopted by, another one."""
    return USER + "\n".join(sorted(str(f) for f in _user_files()))


def _shared(key: str) -> set[Path]:
    if key.startswith(USER):
        return {Path(f) for f in key[len(USER):].split("\n")}
    c = Path(key)
    return {c / n for n in SHARED_GIT} | set(c.glob("worktrees/*/config.worktree"))


def _name(key: str, f: Path) -> str:
    return f.as_posix() if key.startswith(USER) else f.relative_to(key).as_posix()


def _includes(files: set[Path]) -> set[Path]:
    """The files that these config files include, directly or through each other (review R1 of 82f81a11)."""
    seen, todo = set(files), [f for f in files if f.name != "attributes"]
    while todo:
        f = todo.pop()
        if not f.is_file():
            continue
        p = subprocess.run(["git", "config", "-z", "--type=path", "--file", str(f), "--get-regexp", INCLUDE_KEY],
                           capture_output=True, timeout=30, creationflags=_NOWIN, env=child_env(),
                           cwd=tempfile.gettempdir())
        for e in p.stdout.split(b"\0") if p.returncode == 0 else []:
            val = e.partition(b"\n")[2].decode("utf-8", "replace")
            t = Path(val) if Path(val).is_absolute() else f.parent / val
            if val and t not in seen:
                seen.add(t)
                todo.append(t)
    return seen


def _entry(f: Path) -> tuple[bytes, int] | None:
    """A baseline entry: the bytes and the mode (review R3 of 3d423b40), or None for a missing file."""
    data = _read(f)
    return None if data is None else (data, stat.S_IMODE(f.stat().st_mode))


def _old(e: tuple[bytes, int] | None) -> bytes | None:
    return e[0] if e else None


def _read(f: Path) -> bytes | None:
    try:
        return f.read_bytes()
    except FileNotFoundError:
        return None


def _differ(common: str, snap: dict) -> list[Path]:
    return [f for f in sorted(set(snap) | _shared(common)) if (now := _read(f)) is not None and now != _old(snap.get(f))]


def _entries(data: bytes | None) -> set[bytes] | None:
    """The `key\\nvalue` entries of config text; None if git cannot parse it."""
    if not data:
        return set()
    p = subprocess.run(["git", "config", "-z", "--file", "-", "--list"], input=data, capture_output=True,
                       timeout=30, creationflags=_NOWIN, env=child_env(), cwd=tempfile.gettempdir())
    return {e for e in p.stdout.split(b"\0") if e} if p.returncode == 0 else None


def _late_ok(e: bytes, added: bool) -> bool:
    key, _, val = e.partition(b"\n")
    return bool(LATE_OK.match(key.decode("utf-8", "replace"))) and not (
        added and key.lower().endswith((b"url", b"pushurl")) and HELPER_URL.match(val))


def _show(e: bytes) -> str:
    key, _, val = e.partition(b"\n")
    v = re.sub(r"\s+", " ", val.decode("utf-8", "replace"))
    return f"{key.decode('utf-8', 'replace')[:80]}={v[:60]}{'...' if len(v) > 60 else ''}"


def _late(common: str, snap: dict) -> tuple[list[str], list[str]]:
    """Shared files changed after the last run in a way outside LATE_OK, and the entries they added (review R3 of
    82f81a11); LATE_OK edits join `snap` (review R2 of 82f81a11)."""
    bad, added = [], []
    for f in sorted(set(snap) | _shared(common)):
        now = _read(f)
        if now is None or now == _old(snap.get(f)):
            continue
        new, old = (_entries(now), _entries(_old(snap.get(f)))) if f.name != "attributes" else (None, None)
        if new is not None and old is not None and all(_late_ok(e, e in new) for e in new ^ old):
            snap[f] = (now, stat.S_IMODE(f.stat().st_mode))
            continue
        bad.append(_name(common, f))
        added += [_show(e) for e in sorted(new - old if new is not None and old is not None else ())
                  if not _late_ok(e, True)]
    return bad, added


def _put_back(f: Path, old: tuple[bytes, int] | None) -> None:
    prefix = f"{f.name}{REJECTED}{time.time_ns():020d}-{next(_SEQ):08d}-"     # sorts oldest first, even in one tick
    fd, _keep = tempfile.mkstemp(dir=f.parent, prefix=prefix)                  # O_EXCL: never overwrites a kept copy
    with os.fdopen(fd, "wb") as h:
        h.write(f.read_bytes())
    for extra in sorted(f.parent.glob(f.name + REJECTED + "*"))[:-KEEP_REJECTED]:
        extra.unlink(missing_ok=True)
    if old is None:
        f.unlink()
        return
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f".{f.name}.", suffix=".jarves")
    try:
        with os.fdopen(fd, "wb") as h:
            h.write(old[0])
        os.chmod(tmp, old[1])
        os.replace(tmp, f)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def tamper_text(names: list[str]) -> str:
    return f"changed the shared git files ({', '.join(names[:5])}); JARVES put them back (#1533)"


def _restore(common: str) -> list[str]:
    """With _GUARD_LOCK held and a run active: put back every changed file; the names go to every active run."""
    g, names = _GUARDS[common], []
    for f in _differ(common, g[1]):
        _put_back(f, g[1].get(f))
        names.append(_name(common, f))
    for run in g[0]:
        run.extend(n for n in names if n not in run)
    return names


def _guard_git(common: str) -> str:
    """'' when a JARVES git command may run in the repository with this common dir, else why not (#1533)."""
    with _GUARD_LOCK:
        try:
            if common in _GUARDS:
                names = _restore(common)
                return f"a running agent or check {tamper_text(names)}" if names else ""
            late, added = _late(common, _LAST[common]) if common in _LAST else ([], [])
        except OSError as e:
            return f"the shared git files could not be checked or put back: {e}"
    what = f"; added: {', '.join(added[:5])}{' ...' if len(added) > 5 else ''}" if added else ""
    return (f"the shared git files ({', '.join(late[:5])}) changed after the last agent run or check ended{what}; "
            "JARVES left them as they are and runs no git there. Check that you made this change yourself before you "
            "restart JARVES to accept it (#1533)") if late else ""


def guard_enter(cwd: str | Path) -> tuple[list[str], list[str]]:
    """Start guarding the shared files for an agent run or a check in `cwd`: the repository's (if any) and the
    user-level ones. Returns (keys, changed). Raises GitError when they changed since the last run."""
    common, changed = common_dir(cwd), []
    keys = [k for k in (common, _user_key()) if k]
    with _GUARD_LOCK:
        for k in keys:
            why = "" if k in _GUARDS else _guard_git(k)
            if why:
                raise GitError(why)
        for k in keys:
            _GUARDS.setdefault(k, [[], {f: _entry(f) for f in _includes(_shared(k))}])[0].append(changed)
    return keys, changed


def guard_exit(token: tuple[list[str], list[str]]) -> list[str]:
    """End it: every changed file is put back; returns the names changed meanwhile. OSError if one cannot be."""
    keys, changed = token
    with _GUARD_LOCK:
        try:
            for k in keys:
                _restore(k)
        finally:
            for k in keys:
                g = _GUARDS[k]
                g[0] = [r for r in g[0] if r is not changed]
                if not g[0]:
                    _LAST[k] = g[1]
                    del _GUARDS[k]
    return changed


@contextlib.contextmanager
def shared_config_guard(cwd: str | Path):
    """guard_enter/guard_exit around a block. Yields the list of changed names, filled on exit."""
    token = guard_enter(cwd)
    try:
        yield token[1]
    finally:
        guard_exit(token)


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
    register_project(home, pid, repo)
    if (wt / ".git").exists():
        if _key(wt) not in _PIN:                # made before this JARVES started: pinned before any agent runs
            _pin(repo, wt)
        return wt
    wt.parent.mkdir(parents=True, exist_ok=True)
    start = base or "HEAD"
    with _repo_lock(repo):
        if git(repo, "rev-parse", "--verify", "--quiet", br, check=False).returncode == 0:
            git(repo, "worktree", "add", str(wt), br)
        else:
            git(repo, "worktree", "add", "--no-track", "-b", br, str(wt), start)
        _pin(repo, wt)
    return wt


def new_task_worktree(home: Path, repo: str, pid: int, seq: int, attempt: int) -> tuple[Path, str]:
    """A fresh worktree on a fresh branch from the CURRENT integration branch (earlier tasks' code is in it)."""
    br = task_branch(pid, seq, attempt)
    wt = project_root(home, pid) / "wt" / f"t{seq}-a{attempt}"
    register_project(home, pid, repo)
    with _repo_lock(repo):
        remove_worktree(repo, wt, br)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--no-track", "-b", br, str(wt), integration_branch(pid))
        _pin(repo, wt)
    return wt, br


def detached_worktree(home: Path, repo: str, pid: int, sha: str) -> Path:
    """A throw-away worktree at one exact commit (a release runs there, never in your folder)."""
    wt = project_root(home, pid) / "release" / sha[:12]
    register_project(home, pid, repo)
    with _repo_lock(repo):
        remove_worktree(repo, wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--detach", str(wt), sha)
        _pin(repo, wt)
    return wt


def remove_worktree(repo: str, wt: Path, branch: str = "") -> None:
    """Only ever called for paths under <home>/projects (callers build them); a missing one is fine."""
    if "projects" not in Path(wt).parts:
        raise GitError("refusing to remove a folder outside the JARVES projects area")
    with _repo_lock(repo):
        _PIN.pop(_key(wt), None)
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
    p, why = None, ""
    try:
        with shared_config_guard(wt) as changed:
            try:
                p = subprocess.run(argv, cwd=str(wt), capture_output=True, text=True, timeout=timeout,
                                   creationflags=_NOWIN, env=child_env(extra), encoding="utf-8", errors="replace",
                                   stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                why = "check timed out"
            except OSError as e:
                why = f"check could not start: {e}"
    except GitError as e:                       # the shared git files changed after the last run (#1533)
        return False, str(e)
    except OSError as e:
        return False, f"{why + '; ' if why else ''}the shared git files could not be checked or put back: {e}"
    if changed or why:
        return False, "; ".join(x for x in (why, changed and "the check " + tamper_text(changed)) if x)
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
