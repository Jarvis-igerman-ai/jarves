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


# Shared-config keys that name a command git runs (#1390). An agent can write the shared `.git/config` (or a file it
# includes), so for each key set at repository scope JARVES passes `-c key=<owner's global/system value or a safe
# default>`: "" turns a filter off, ":" is git's no-op editor, the rest are git's own defaults.
_CMD_KEYS = {"core.sshcommand": "ssh", "core.editor": ":", "sequence.editor": ":", "core.pager": "", "core.askpass": "",
             "credential.helper": "", "gpg.program": "gpg", "gpg.openpgp.program": "gpg", "gpg.x509.program": "gpgsm",
             "gpg.ssh.program": "ssh-keygen", "gpg.ssh.defaultkeycommand": ""}
_CMD_SUB = {("filter", "clean"): "", ("filter", "smudge"): "", ("filter", "process"): "", ("hook", "command"): "",
            ("credential", "helper"): "", ("merge", "driver"): "git merge-file --marker-size=%L %A %O %B",
            ("trailer", "cmd"): "", ("trailer", "command"): ""}
_ALIAS = {"gpg.openpgp.program": "gpg.program"}         # git reads both keys into one setting
# git runs EVERY credential helper from every scope; an empty value of any matching key clears the one list. So when
# the repository sets any helper key, the list is cleared once and the owner's helper entries are replayed in order.
# `core.gitProxy` (first match wins, so `-c` cannot replace it) is switched off by an empty GIT_PROXY_COMMAND, which
# git reads before the config.
# diff.external, diff.<x>.command and diff.<x>.textconv: every diff/show/log/blame gets --no-ext-diff/--no-textconv.
# git takes the FIRST `remote.<x>.receivepack`/`uploadpack`, so `-c` cannot replace them: a push, fetch, pull or
# ls-remote names git's own program instead. blame runs textconv by default too.
_UPLOAD = ("--upload-pack=git-upload-pack",)
_ARG_FIXES = {"diff": ("--no-ext-diff", "--no-textconv"), "show": ("--no-ext-diff", "--no-textconv"),
              "log": ("--no-ext-diff", "--no-textconv"), "push": ("--receive-pack=git-receive-pack",),
              "blame": ("--no-textconv",), "fetch": _UPLOAD, "pull": _UPLOAD, "ls-remote": _UPLOAD}
# GIT_ALLOW_PROTOCOL replaces every `protocol.*.allow` setting: only https and ssh (the forge's GitHub push). A
# remote, pushurl or `insteadOf`/`pushInsteadOf` from the shared config can then never point a push at a local
# repository (whose receive-pack runs ITS hooks, the -c overrides do not reach it), an ext:: command or a
# `git-remote-<name>` helper (`<name>::` URLs, `remote.<x>.vcs`).
_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_PROXY_COMMAND": "", "GIT_ALLOW_PROTOCOL": "https:ssh"}
_TRUE = ("true", "yes", "on", "1")


def _multi(key: str) -> bool:
    return key.startswith("credential.") and key.endswith(".helper")


def _cmd_default(key: str) -> str | None:
    if key in _CMD_KEYS:
        return _CMD_KEYS[key]
    sec, _, rest = key.partition(".")
    sub, _, var = rest.rpartition(".")
    return _CMD_SUB.get((sec, var)) if sub else None


def _config_overrides(cwd: str | Path) -> list[str]:
    """`-c` pairs that replace every command-naming key set at local/worktree scope (included files count). An
    unreadable config raises GitError even for check=False callers, so it never reads as empty output."""
    p = subprocess.run(["git", "config", "--list", "--show-scope", "--includes", "-z"], cwd=str(cwd),
                       capture_output=True, text=True, timeout=30, creationflags=_NOWIN, encoding="utf-8",
                       errors="replace", env=child_env(_ENV))
    if p.returncode != 0:
        raise GitError(("git config unreadable, nothing run (git 2.26 or newer is needed): "
                        + (p.stderr or "").strip())[:300])
    owner: dict[str, str] = {}
    owner_helpers: list[str] = []
    shared: list[str] = []
    required: set[str] = set()                           # filters the shared config marks required
    items = p.stdout.split("\0")
    for scope, entry in zip(items[0::2], items[1::2]):
        key, nl, value = entry.partition("\n")
        local = scope not in ("system", "global")
        if local and key.startswith("filter.") and key.endswith(".required"):
            (required.add if not nl or value.lower() in _TRUE else required.discard)(key[:-len(".required")])
        if _cmd_default(key) is None:
            continue
        if not local:
            owner[_ALIAS.get(key, key)] = value
            if _multi(key) and "=" not in key:           # `-c` cannot express a URL with '='; that helper is skipped
                owner_helpers.append(f"{key}={value}")
        elif key not in shared:
            shared.append(key)
    pre: list[str] = []
    if any(_multi(k) for k in shared):
        pre += [x for v in ("credential.helper=", *owner_helpers) for x in ("-c", v)]
    for key in shared:
        if "=" in key:                                   # `-c` would split it at the wrong place
            raise GitError(f"refusing a shared git config key with '=' in its name: {key[:80]}")
        if _multi(key):
            continue
        value = owner.get(_ALIAS.get(key, key), _cmd_default(key))
        pre += ["-c", f"{key}={value}"]
        if key.startswith("hook.") and not value:        # newer git: a config hook can also be switched off by name
            pre += ["-c", key.rsplit(".", 1)[0] + ".enabled=false"]
        name = key.rsplit(".", 1)[0]
        if key.startswith("filter.") and not value and name in required:     # never commit unfiltered content
            raise GitError(f"refusing to run with {name} switched off: the repository's own config marks it required"
                           " (e.g. git lfs install --local); set that filter in your global git config")
    return pre


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. No other command named in the shared config runs either (#1390): filters,
    merge drivers, ssh/editor/pager/askpass/credential/gpg programs and config hooks are overridden by
    `_config_overrides`; external diff and textconv are off, a push/fetch runs git's own pack programs, only the
    https and ssh transports are allowed, core.gitProxy is off. `args` must start with the subcommand, so the flags
    above land in the right place.
    `stdin`: text for git's standard input (e.g. `credential fill`)."""
    if not args or args[0].startswith("-"):
        raise GitError("git() takes the subcommand first, not a global option")
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false",
           *_config_overrides(cwd)]
    if args[0] in _ARG_FIXES:
        args = (args[0], *_ARG_FIXES[args[0]], *args[1:])
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=child_env(_ENV), encoding="utf-8", errors="replace",
                           input=stdin)
        if p.returncode == 0 or attempt == LOCK_RETRIES or not _lock_contention(p.stderr):
            break
        time.sleep(0.15 * (attempt + 1))
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout or "git failed").strip()[:300])
    return p


def is_repo(path: str) -> bool:
    try:
        return git(path, "rev-parse", "--git-dir", check=False).returncode == 0
    except (OSError, subprocess.SubprocessError, GitError):
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
