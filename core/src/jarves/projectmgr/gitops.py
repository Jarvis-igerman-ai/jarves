"""Git for the project manager. Every JARVES worktree lives under `<home>/projects/<id>/` (never inside the owner's
repository, so tests/linters at the repo root never see them and agents do not inherit the owner's CLAUDE.md of a parent
folder). The owner's checkout is never touched: the integration branch `jarves/p<ID>/integration` has its own worktree,
merges are fast-forward only after the check ran on the merged result. JARVES never pushes."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
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

# config keys that name a command git runs (#1390). core.hooksPath and core.fsmonitor are not listed: every call
# overrides both. Pagers are not listed: git never starts one without a terminal and every call captures output.
# includeIf is listed because its condition (branch, gitdir) can match only inside a new worktree.
_RUNS = re.compile(
    r"(core\.(sshcommand|editor|askpass|gitproxy)|sequence\.editor|diff\.external|gpg\.(.+\.)?program"
    r"|credential\.(.+\.)?helper|filter\..+\.(clean|smudge|process)|merge\..+\.driver|diff\..+\.(command|textconv)"
    r"|hook\..+\.command|trailer\..+\.(cmd|command)|(merge|diff)tool\..+\.(cmd|path)"
    r"|remote\..+\.(uploadpack|receivepack|vcs)|includeif\..+|gpg\..+\.defaultkeycommand|submodule\..+\.update"
    r"|core\.alternaterefscommand|interactive\.difffilter|(browser|man)\..+\.(cmd|path)|web\.browser|help\.browser"
    r"|instaweb\..+|gc\.recentobjectshook|alias\..+)")
# values that name no command: an empty credential.helper resets the list, and submodule.*.update runs a command
# only as `!cmd` (checkout/rebase/merge/none are what `git submodule init` copies from .gitmodules);
# an alias names a command as `!cmd`, or through leading global options (`-c filter.x.clean=cmd add`, `--exec-path`)
_HARMLESS = {"credential": lambda v: v == "", "submodule": lambda v: not v.startswith("!"),
             "alias": lambda v: not v.startswith(("!", "-"))}
_SHARED_SCOPES = ("local", "worktree")      # the repository's own config (and files it includes): agents can write it
# git options that point git at another repository than `cwd`, which config_commands checked
_ELSEWHERE = ("-C", "--git-dir", "--work-tree")
# global options whose value is the NEXT argument (git's own list); the scan skips that value
_TAKES_VALUE = ("-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix",
                "--attr-source", "--list-cmds")


def _points_elsewhere(args: tuple[str, ...]) -> bool:
    """True when a global option before the subcommand selects another repository (#1390 review): option values
    (`-c k=v`) are skipped, so a later `-C`/`--git-dir`/`--work-tree` is still seen; `-Cdir` counts too."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a.split("=", 1)[0] in _ELSEWHERE or a.startswith("-C"):
            return True
        i += 2 if a in _TAKES_VALUE else 1
    return False


def _names_command(key: str, value: str) -> bool:
    harmless = _HARMLESS.get(key.split(".", 1)[0])
    return bool(_RUNS.fullmatch(key)) and not (harmless and harmless(value.strip()))


def _read_config(cwd: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "config", *args, "--list", "-z"], cwd=str(cwd), capture_output=True,
                          creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0"}), timeout=30)


def _pairs(out: bytes) -> list[tuple[str, str]]:
    return [((kv.split("\n", 1) + [""])[0].lower(), (kv.split("\n", 1) + [""])[1])
            for kv in out.decode("utf-8", "replace").split("\0") if kv]


def _populated_gitlinks(cwd: str | Path) -> tuple[Path, list[Path]] | str:
    """(work tree root, folders of its populated gitlinks) or a refusal text (#1390, #1662 R1-R3). `git status` and
    `git diff` run git inside every gitlink of the index that has a `.git` (a folder or a `gitdir:` file pointing
    anywhere, e.g. `.git/modules/x` or `.git/worktrees/w/modules/x`), with that repository's own config."""
    env = child_env({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    top = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--show-toplevel"], cwd=str(cwd),
                         capture_output=True, creationflags=_NOWIN, env=env, timeout=30)
    err = top.stderr.decode("utf-8", "replace").strip()
    if top.returncode != 0:                  # not a repository at all (e.g. before `git init`): nothing to recurse into
        return (Path(cwd), []) if "not a git repository" in err else f"(unreadable config: gitlinks: {err[:150]})"
    root = Path(os.fsdecode(top.stdout.strip()))
    ls = subprocess.run(["git", "-c", "core.fsmonitor=false", "ls-files", "--stage", "-z"], cwd=str(root),
                        capture_output=True, creationflags=_NOWIN, env=env, timeout=60)
    if ls.returncode != 0:
        return f"(unreadable config: gitlinks: {ls.stderr.decode('utf-8', 'replace').strip()[:150]})"
    paths = [root / os.fsdecode(e.split(b"\t", 1)[1]) for e in ls.stdout.split(b"\0") if e.startswith(b"160000 ")]
    return root, [x for x in paths if os.path.lexists(x / ".git")]


def config_commands(cwd: str | Path, _prefix: str = "", _depth: int = 0) -> list[str]:
    """Keys that name a command (#1390) in the repository's own config and, recursively, in the config of every
    populated gitlink (submodule or nested repository) git would enter; a nested key reads `lib:filter.x.clean`.
    These sit in the shared `.git` folder or in a work tree, both writable by agents; the owner's global/system config
    is not checked. A config or gitlink list git cannot read is refused too (fail closed)."""
    p = _read_config(cwd, "--show-scope")
    if p.returncode != 0:
        return [f"(unreadable config: {_prefix}{p.stderr.decode('utf-8', 'replace').strip()[:150]})"]
    parts = p.stdout.decode("utf-8", "replace").split("\0")
    own = b"\0".join(kv.encode() for scope, kv in zip(parts[::2], parts[1::2]) if scope in _SHARED_SCOPES)
    bad = {_prefix + k for k, v in _pairs(own) if _names_command(k, v)}
    links = _populated_gitlinks(cwd) if not bad else (Path(cwd), [])
    if isinstance(links, str):
        return [links]
    if links[1] and _depth >= 8:
        return [f"(unreadable config: {_prefix}nested repositories deeper than 8)"]
    for sub in links[1]:
        bad.update(config_commands(sub, f"{_prefix}{sub.relative_to(links[0]).as_posix()}:", _depth + 1))
    return sorted(bad)


# a submodule git can enter without a work tree (fetch/push recursion, a checkout that populates one) is never entered:
# config_commands checks only populated gitlinks (#1662 R1)
_NO_RECURSE = ["-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false",
               "-c", "push.recurseSubmodules=no"]


def _refused(args: tuple[str, ...], check: bool, why: str) -> subprocess.CompletedProcess:
    """A #1390 refusal: GitError with check=True; with check=False a failed result (code 128), as before #1390 a
    `check=False` call never raised GitError, and its callers already treat a non-zero code as a failure."""
    if check:
        raise GitError(f"{why[:250]} (#1390)")
    return subprocess.CompletedProcess(["git", *args], 128, "", f"{why} (#1390)")


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Any other command named in the repository's own config (filter and merge
    drivers, diff commands, editors, ssh, gpg, config-defined hooks, see `_RUNS`), or in the config of a populated
    gitlink git would enter, makes the call fail before git starts (#1390); submodule recursion is off otherwise.
    Residual: a key written in the moment between that read and git's own read is not prevented;
    it is detected by a second read after the call, which then fails loudly (prevention: #1655).
    `protocol.ext.allow=never` stops an `ext::` remote (a command as transport) whatever the shared config allows."""
    if _points_elsewhere(args):
        return _refused(args, check, "refused: a git call may not point git at another folder than the one checked")
    bad = config_commands(cwd)
    if bad:
        return _refused(args, check, f"refused: the repository config names a command git would run: {', '.join(bad)}")
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false",
           "-c", "protocol.ext.allow=never", *_NO_RECURSE]
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0"}), encoding="utf-8",
                           errors="replace")
        if p.returncode == 0 or attempt == LOCK_RETRIES or not _lock_contention(p.stderr):
            break
        time.sleep(0.15 * (attempt + 1))
    late = [k for k in config_commands(cwd) if not k.startswith("(")]     # detect a key written during the call
    if late:
        return _refused(args, check, "refused: the repository config changed during the call (which already ran) "
                                     f"to name a command: {', '.join(late)}")
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
    try:
        p = subprocess.run(argv, cwd=str(wt), capture_output=True, text=True, timeout=timeout, creationflags=_NOWIN,
                           env=child_env(extra), encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "check timed out"
    except OSError as e:
        return False, f"check could not start: {e}"
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
