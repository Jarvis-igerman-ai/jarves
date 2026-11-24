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
    pin = _pin_of(cwd)
    common = pin[1] if pin else _find_common(cwd) if _guard and (_GUARDS or _LAST) else None
    why = _guard_git(common) if common and _guard else ""
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
# baseline of them: before every JARVES git command there and when each run ends, a change is put back (temp file +
# os.replace, the file mode kept; the rejected bytes go to a new `<name>.jarves-rejected-<time>-<n>-<random>` file,
# the newest KEEP_REJECTED per file are kept) and fails every run active at that moment. After the last run, a later
# change (a left-behind process, or your own edit) is never reverted: one that adds or changes a key git may run
# (COMMAND_KEY), or any change to an attributes file, makes JARVES git refuse to run there until JARVES restarts;
# other config edits (`git remote add`, `push -u`) are accepted. A deleted file names no command and is ignored.
# JARVES's own git never writes them (`worktree add --no-track`). The worktree's `.git` / `commondir` pointers are read
# once, when JARVES creates (or, after a restart, first opens and checks) the worktree; every later JARVES git command
# there, or in a folder below it, runs with GIT_DIR/GIT_COMMON_DIR, so a planted pointer is never followed. Still
# open (UNVERIFIED): a write between this check and git's own read; a baseline taken after a restart; the system
# config (admin-owned) and a custom `core.attributesFile` path.
SHARED_GIT = ("config", "config.worktree", "info/attributes")
CONFIG_NAMES = ("config", "config.worktree", ".gitconfig")
COMMAND_KEY = re.compile(
    r"^(filter\..+\.(clean|smudge|process)|merge\..+\.driver|diff\.external|diff\..+\.(command|textconv)|hook\..+|"
    r"gpg\..*program|gpg\.ssh\.defaultkeycommand|core\.(sshcommand|editor|pager|askpass|fsmonitor|hookspath|"
    r"attributesfile|worktree|gitproxy)|credential\..*|include\..*|includeif\..*|alias\..*|pager\..*|"
    r"sequence\.editor|.*\.(command|cmd|program|helper|driver|textconv|uploadpack|receivepack))$", re.IGNORECASE)
REJECTED = ".jarves-rejected-"
KEEP_REJECTED = 5
_SEQ = itertools.count()
_GUARDS: dict[str, list] = {}           # common dir -> [the `changed` lists of the active runs, {file: bytes or None}]
_LAST: dict[str, dict] = {}             # common dir -> the files as the last run left them
_GUARD_LOCK = threading.RLock()
_PIN: dict[str, tuple[str, str, str]] = {}  # JARVES worktree -> (its git dir, the common dir, the worktree)
_COMMON: dict[str, str] = {}            # folder -> its common dir


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _pin_of(cwd: str | Path) -> tuple[str, str, str] | None:
    """The pin of `cwd` or of the JARVES worktree it lies in (review R2)."""
    k = _key(cwd)
    return _PIN.get(k) or next((v for w, v in list(_PIN.items()) if k.startswith(w + os.sep)), None)


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


def _shared(common: str) -> set[Path]:
    c = Path(common)
    return {c / n for n in SHARED_GIT} | set(c.glob("worktrees/*/config.worktree")) | _user_files()


def _name(common: str, f: Path) -> str:
    return f.relative_to(common).as_posix() if f.is_relative_to(common) else f.as_posix()


def _read(f: Path) -> bytes | None:
    try:
        return f.read_bytes()
    except FileNotFoundError:
        return None


def _differ(common: str, snap: dict) -> list[Path]:
    return [f for f in sorted(set(snap) | _shared(common)) if (now := _read(f)) is not None and now != snap.get(f)]


def _commands(data: bytes | None) -> set[bytes] | None:
    """The `key\\nvalue` entries of config text whose key git may run; None if git cannot parse it."""
    if not data:
        return set()
    p = subprocess.run(["git", "config", "-z", "--file", "-", "--list"], input=data, capture_output=True,
                       timeout=30, creationflags=_NOWIN, env=child_env(), cwd=tempfile.gettempdir())
    if p.returncode != 0:
        return None
    return {e for e in p.stdout.split(b"\0") if COMMAND_KEY.match(e.partition(b"\n")[0].decode("utf-8", "replace"))}


def _late(common: str, snap: dict) -> list[str]:
    """Shared files changed after the last run that may make git run a command; harmless edits join `snap` (R4)."""
    bad = []
    for f in sorted(set(snap) | _shared(common)):
        now = _read(f)
        if now is None or now == snap.get(f):
            continue
        new, old = (_commands(now), _commands(snap.get(f))) if f.name in CONFIG_NAMES else (None, None)
        if new is not None and old is not None and new <= old:
            snap[f] = now
        else:
            bad.append(_name(common, f))
    return bad


def _put_back(f: Path, old: bytes | None) -> None:
    prefix = f"{f.name}{REJECTED}{time.time_ns():020d}-{next(_SEQ):08d}-"     # sorts oldest first, even in one tick
    fd, _keep = tempfile.mkstemp(dir=f.parent, prefix=prefix)                  # O_EXCL: never overwrites a kept copy
    with os.fdopen(fd, "wb") as h:
        h.write(f.read_bytes())
    for extra in sorted(f.parent.glob(f.name + REJECTED + "*"))[:-KEEP_REJECTED]:
        extra.unlink(missing_ok=True)
    if old is None:
        f.unlink()
        return
    mode = stat.S_IMODE(f.stat().st_mode)
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f".{f.name}.", suffix=".jarves")
    try:
        with os.fdopen(fd, "wb") as h:
            h.write(old)
        os.chmod(tmp, mode)
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
            late = _late(common, _LAST[common]) if common in _LAST else []
        except OSError as e:
            return f"the shared git files could not be checked or put back: {e}"
    return (f"the shared git files ({', '.join(late[:5])}) changed after the last agent run or check ended; JARVES "
            "left them as they are and runs no git there. If you made this change yourself, restart JARVES to accept "
            "it (#1533)") if late else ""


@contextlib.contextmanager
def shared_config_guard(cwd: str | Path):
    """Around an agent run or a check in `cwd`. Yields a list that holds, on exit, the shared files changed meanwhile
    (already put back). Raises GitError when they changed since the last run, OSError when they cannot be put back."""
    common, changed = common_dir(cwd), []
    if common is not None:
        with _GUARD_LOCK:
            if common not in _GUARDS:
                why = _guard_git(common)
                if why:
                    raise GitError(why)
                _GUARDS[common] = [[], {f: _read(f) for f in _shared(common)}]
            _GUARDS[common][0].append(changed)
    try:
        yield changed
    finally:
        if common is not None:
            with _GUARD_LOCK:
                try:
                    _restore(common)
                finally:
                    g = _GUARDS[common]
                    g[0] = [r for r in g[0] if r is not changed]
                    if not g[0]:
                        _LAST[common] = g[1]
                        del _GUARDS[common]


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
    with _repo_lock(repo):
        remove_worktree(repo, wt, br)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--no-track", "-b", br, str(wt), integration_branch(pid))
        _pin(repo, wt)
    return wt, br


def detached_worktree(home: Path, repo: str, pid: int, sha: str) -> Path:
    """A throw-away worktree at one exact commit (a release runs there, never in your folder)."""
    wt = project_root(home, pid) / "release" / sha[:12]
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
