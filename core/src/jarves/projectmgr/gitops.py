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


# config keys that make git run a command (#1390). An agent can write the shared repository config, so a value set at
# repository scope (local, worktree, or a file it includes) is replaced; the owner's own global/system settings stay
# (e.g. a git-lfs filter). Signing programs fall back to git's default program, everything else to "" (git runs nothing;
# a driver it then cannot run counts as a conflict or a failed command). Editors: GIT_EDITOR/GIT_SEQUENCE_EDITOR=":".
RUN_KEYS = re.compile(r"^(filter\..+\.(clean|smudge|process)|merge\..+\.driver|diff\..+\.(command|textconv)|"
                      r"diff\.external|core\.(sshcommand|askpass|pager|editor)|sequence\.editor|"
                      r"credential\.(.+\.)?helper|gpg\.(program|openpgp\.program|x509\.program|ssh\.program|"
                      r"ssh\.defaultkeycommand)|core\.(gitproxy|alternaterefscommand)|"
                      r"remote\..+\.(uploadpack|receivepack|vcs)|trailer\..+\.(command|cmd))$")
# commit and merge start `maintenance run --auto` / `gc --auto`, which can run `gc.recentObjectsHook` (multi-valued:
# an extra `-c` value cannot remove the shared config's own values). JARVES never needs auto-maintenance (#1468 R1).
# Submodule recursion is off too: a submodule's own config (`.git/modules/<name>/config`, agent-writable, never read
# here) could name a filter that a recursing checkout or reset would run (#1468 round 3 R2).
NO_AUTO = ["-c", "maintenance.auto=false", "-c", "gc.auto=0", "-c", "submodule.recurse=false"]
# fixed-name command keys JARVES never needs (it never fetches, pushes, pages or edits): blanked on every call, so a key
# toggled between the config read and git's own read gains nothing (#1468 round 2 R2). Not diff.external (an empty one
# breaks a patch diff: every diff/show/log gets --no-ext-diff instead, #1468 R2) and not the signing programs (always
# set from the owner's scope, _SIGNING_DEFAULTS). core.gitProxy is first-match in git, so a later `-c` cannot override
# a repository value; JARVES never contacts a git:// remote, where it applies (PR #1486 council nit).
NO_CMDS = [x for k in ("core.sshCommand", "core.askPass", "core.pager", "core.editor", "sequence.editor",
                       "core.gitProxy", "core.alternateRefsCommand", "credential.helper") for x in ("-c", f"{k}=")]
# refused at repository scope: a conditional include can match only in a child git (worktree add checks out with HEAD
# on the new branch and the gitdir under .git/worktrees/), which the one config read cannot see (round 2 R1); a config
# hook (`hook.<name>.*`, git 2.5x) has no safe neutral value: an empty command makes git fail, and its event list is
# multi-valued (round 2 R3, seen on git 2.56 in CI)
_REFUSED = re.compile(r"^(includeif|hook)\.")
CONFIG_REFUSED = ("the repository's git config could not be read, or uses a conditional include (includeIf) or a "
                  "config hook (hook.<name>)")
# `git config --show-scope` (the config read) needs git 2.26 or newer; older git exits 129 (PR #1486 R4)
GIT_TOO_OLD = "git 2.26 or newer is needed (JARVES reads the repository's config with --show-scope): update git"
# fixed-name signing keys: set on every call to the owner's global/system value, else git's default, so the repository
# scope never counts and an agent racing the read gains nothing (#1468 R1)
_SIGNING_DEFAULTS = {"gpg.program": "gpg", "gpg.openpgp.program": "gpg", "gpg.x509.program": "gpgsm",
                     "gpg.ssh.program": "ssh-keygen", "gpg.ssh.defaultkeycommand": ""}
_OPENPGP = ("gpg.program", "gpg.openpgp.program")
_PATCH_CMDS = ("diff", "show", "log")
# textconv runs by default in these (blame too): a blanked repository `diff.<x>.textconv` is skipped with --no-textconv.
# JARVES calls no other textconv-by-default command; add one here if it ever does (PR #1486 R5)
_TEXTCONV_CMDS = (*_PATCH_CMDS, "blame")
_NO_CMD_KEYS = {k.split("=")[0].lower() for k in NO_CMDS[1::2]}
_OWNER_SCOPES = ("system", "global")


def _git_env() -> dict[str, str]:
    return child_env({"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": ":", "GIT_SEQUENCE_EDITOR": ":"})


def _config_overrides(cwd: str | Path) -> list[str] | str:
    """`-c key=<owner value>` for every RUN_KEYS entry set at repository scope (the owner's own global/system value of
    that key, else "", so a repository copy of e.g. `filter.lfs.clean` never hides the owner's: PR #1486 R3), read
    right before each command (the config can change between commands), plus every signing key from the owner's scope
    (_SIGNING_DEFAULTS). A str: why the config could not be used (unreadable, too old a git, or a key that cannot be
    overridden safely; git exits 1 when nothing matches).
    Accepted residual risk (P1 #1483, PR #1468 round 3 R1): `<section>.<name>.*` keys (filter, merge and diff
    drivers) depend on this read, so an agent that toggles one between this read and git's own read can still win."""
    try:
        p = subprocess.run(["git", "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false", "config",
                            "--show-scope", "--null", "--get-regexp", f"{RUN_KEYS.pattern}|{_REFUSED.pattern}"],
                           cwd=str(cwd),
                           capture_output=True, text=True, timeout=30, creationflags=_NOWIN, env=_git_env(),
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return CONFIG_REFUSED
    if p.returncode == 129 and "show-scope" in (p.stderr or ""):
        return GIT_TOO_OLD
    if p.returncode not in (0, 1):
        return CONFIG_REFUSED
    parts, out, seen, sign, owner = p.stdout.split("\0"), [], set(), dict(_SIGNING_DEFAULTS), {}
    for scope, entry in zip(parts[0::2], parts[1::2]):
        key, _, value = entry.partition("\n")
        if scope in _OWNER_SCOPES:
            owner[key] = value                      # system, then global (git's order): the last one wins
            if key.lower() in sign:                 # system, then global: the last one wins, as in git; gpg.program
                for k in _OPENPGP if key.lower() in _OPENPGP else (key.lower(),):   # and gpg.openpgp.program are
                    sign[k] = value                 # one setting in git (#1486 R1)
            continue
        if "=" in key or _REFUSED.match(key.lower()):
            return CONFIG_REFUSED                            # `-c` would split the name at "="; the others: see _REFUSED
        if not RUN_KEYS.match(key.lower()) or key.lower() in sign:
            continue
        if key not in seen:
            seen.add(key)
            out += ["-c", f"{key}={'' if key.lower() in _NO_CMD_KEYS else owner.get(key, '')}"]
    return out + [x for k, v in sign.items() for x in ("-c", f"{k}={v}")]


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Every other command the repository's own config names (filter and merge
    drivers, diff commands, signing programs: RUN_KEYS) is overridden too (#1390), see _config_overrides."""
    over = _config_overrides(cwd)
    if isinstance(over, str):
        if check:
            raise GitError(over)
        return subprocess.CompletedProcess(["git", *args], 128, "", over)
    if args and args[0] in _TEXTCONV_CMDS:          # diff.external has a fixed name: never from the config (#1468 R2);
        args = (args[0], *(["--no-ext-diff"] if args[0] in _PATCH_CMDS else []), "--no-textconv", *args[1:])
        # a blanked textconv cannot run (#1486 R2, R5)
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false", *NO_AUTO, *NO_CMDS,
           *over]
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=_git_env(), encoding="utf-8", errors="replace")
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


def merge_in(wt: Path, branch: str, ident: bool = True, message: str = "") -> list[str]:
    """Bring `branch` into the worktree's branch. Returns conflicting files ([] = merged); a conflict is aborted
    (nothing changes). A refusal without conflicts (e.g. an untracked file in the way) returns git's own reason.
    Takes the repository's lock: another task's worktree remove/prune/branch -D must not run during the merge (JAR-189).
    message: the merge commit's message (default: git's own, e.g. "Merge commit '<sha>'" for a commit); a fast-forward
    makes no merge commit (#1486 R3: an owner with no git identity can still take a fast-forward).
    Raises GitError when git itself fails (e.g. a refused config): never reported as a conflict (PR #1486 R1)."""
    common = git(wt, "rev-parse", "--git-common-dir").stdout.strip()
    root = (Path(wt) / common).resolve() if common else Path(wt)
    with _repo_lock(root.parent if root.name == ".git" else root):
        p = git(wt, "merge", "--no-edit", *(["-m", message] if message else []), branch, check=False, ident=ident)
        if p.returncode == 0:
            return []
        u = git(wt, "diff", "--name-only", "--diff-filter=U", check=False)
        git(wt, "merge", "--abort", check=False)
        if u.returncode:
            raise GitError(" ".join((p.stderr or u.stderr or "git failed").split())[:300])
        files = [x for x in u.stdout.split() if x]
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
