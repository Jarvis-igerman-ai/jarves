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

# Config keys whose value git runs as a command (#1390). Keys as `git config --list` prints them: section and name in
# lower case, a subsection (`filter.<x>.smudge`) as written. Editors are fixed by GIT_EDITOR below, pagers by --no-pager.
_RUNS = re.compile(r"(?:filter\..+\.(?:clean|smudge|process)|merge\..+\.driver|diff\..+\.(?:command|textconv)"
                   r"|diff\.external|core\.(?:sshcommand|askpass|gitproxy|pager|alternaterefscommand)"
                   r"|gpg\.(?:program|.+\.program|ssh\.defaultkeycommand)|credential\.(?:.+\.)?helper"
                   r"|remote\..+\.(?:receivepack|uploadpack|vcs))", re.S)
_HOOK = re.compile(r"hook\.(.+)\.(?:command|event)", re.S)
# what git uses when nothing is set; an empty value would make these fail instead (only when the shared config set
# them and the owner's own config does not)
_DEFAULTS = {"gpg.program": "gpg", "gpg.openpgp.program": "gpg", "gpg.x509.program": "gpgsm",
             "gpg.ssh.program": "ssh-keygen", "core.sshcommand": "ssh"}
_PACK_DEFAULT = {"receivepack": "git-receive-pack", "uploadpack": "git-upload-pack"}
TRUSTED_SCOPES = ("system", "global")       # the owner's own files; `local`/`worktree` live in the shared .git
# an include whose condition depends on the branch or the git dir can match in a child git (worktree add checks out
# inside the new worktree, on the task branch) but not in the read in `cwd`, so its keys would never be undone (R1)
_COND_INCLUDE = re.compile(r"includeif\.(?:onbranch|gitdir|gitdir/i):.*\.path", re.S)
FIXED_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": ":", "GIT_SEQUENCE_EDITOR": ":"}   # ":" = no editor at all


def _config_entries(cwd: str | Path, timeout: float) -> list[tuple[str, str, str]] | subprocess.CompletedProcess:
    """(scope, key, value) of the effective config, includes followed; the failed run when git cannot read it.
    Reading config runs no command."""
    p = subprocess.run(["git", "--no-pager", "config", "--show-scope", "-z", "--list"], cwd=str(cwd),
                       capture_output=True, text=True, timeout=timeout, creationflags=_NOWIN,
                       env=child_env(FIXED_ENV), encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return p
    parts, out = (p.stdout or "").split("\0"), []
    for i in range(0, len(parts) - 1, 2):
        key, _, value = parts[i + 1].partition("\n")
        out.append((parts[i], key, value))
    return out


def config_overrides(entries: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """Command-scope settings that undo every command the repository's shared config names (#1390). An agent can
    write `.git/config` (and any file it includes), so a filter, merge driver, diff command, credential helper, ssh
    command or gpg program set there must never run under JARVES. Each such key is reset to empty (no filter, no
    driver, an empty helper list), then the owner's own system/global values for it come back in order, so LFS from
    the global config, the owner's credential manager and signing keep working. Config-defined hooks
    (`hook.<name>.command`, which `core.hooksPath` does not stop) lose all events in every scope, like file hooks."""
    untrusted = sorted({k for s, k, _ in entries if s not in TRUSTED_SCOPES and _RUNS.fullmatch(k)})
    trusted = [(k, v) for s, k, v in entries if s in TRUSTED_SCOPES and _RUNS.fullmatch(k)]
    out: list[tuple[str, str]] = []
    for k in untrusted:
        out.append((k, ""))
        if k.startswith("credential."):
            # helpers are a list an empty value clears: the generic reset clears every owner helper, so all come back
            # under their own keys; a URL reset clears them only when its URL matches, so they come back under that
            # same key (a non-matching reset then changes nothing and no owner helper runs twice)
            generic = k == "credential.helper"
            out += [(k2 if generic else k, v) for k2, v in trusted
                    if k2.startswith("credential.") and (generic or k2 in (k, "credential.helper"))]
            continue
        own = [(k2, v) for k2, v in trusted if k2 == k]
        default = _DEFAULTS.get(k) or (_PACK_DEFAULT.get(k.rsplit(".", 1)[-1]) if k.startswith("remote.") else None)
        out += own or ([(k, default)] if default else [])
    hooks = sorted({m.group(1) for _, k, _ in entries if (m := _HOOK.fullmatch(k))})
    return out + [(f"hook.{name}.event", "") for name in hooks]


def _override_env(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """GIT_CONFIG_COUNT/KEY_n/VALUE_n instead of `-c`: a subsection name may hold `=`, which `-c` would split."""
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (k, v) in enumerate(pairs):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return env


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. No other command the shared config names runs either (#1390, see
    config_overrides): filters, merge drivers, diff commands, credential helpers, ssh/gpg programs, config hooks,
    `ext::` remotes, editors and pagers. Submodules never recurse (their config in `.git/modules` is never read)
    and a branch- or gitdir-conditional include in the shared config stops the command (fail closed). Limit: the config is read just before the command, so a key an agent adds in
    the milliseconds between the two reads is not undone."""
    pre = ["--no-pager", *(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false",
           "-c", "protocol.ext.allow=never", "-c", "submodule.recurse=false"]
    entries = _config_entries(cwd, timeout)
    if isinstance(entries, subprocess.CompletedProcess):     # unreadable config: fail closed, the command never runs
        if check:
            raise GitError((entries.stderr or "git config could not be read").strip()[:300])
        return entries
    cond = sorted({k for s, k, _ in entries if s not in TRUSTED_SCOPES and _COND_INCLUDE.fullmatch(k)})
    if cond:                                # a child git could read keys this read never saw: fail closed
        why = f"refusing to run git: the shared repository config has a conditional include ({', '.join(cond)[:200]})"
        if check:
            raise GitError(why)
        return subprocess.CompletedProcess(["git", *args], 1, "", why)
    env = child_env({**FIXED_ENV, **_override_env(config_overrides(entries))})
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=env, encoding="utf-8", errors="replace")
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


def _add_worktree(repo: str, wt: Path, *args: str) -> None:
    """`worktree add` would check out in a child git whose config JARVES never read (R1, #1390): add it empty, then
    check it out with a separate call from inside the new worktree, whose own config is read first."""
    git(repo, "worktree", "add", "--no-checkout", *args)
    git(wt, "reset", "-q", "--hard")


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
            _add_worktree(repo, wt, str(wt), br)
        else:
            _add_worktree(repo, wt, "-b", br, str(wt), start)
    return wt


def new_task_worktree(home: Path, repo: str, pid: int, seq: int, attempt: int) -> tuple[Path, str]:
    """A fresh worktree on a fresh branch from the CURRENT integration branch (earlier tasks' code is in it)."""
    br = task_branch(pid, seq, attempt)
    wt = project_root(home, pid) / "wt" / f"t{seq}-a{attempt}"
    with _repo_lock(repo):
        remove_worktree(repo, wt, br)
        wt.parent.mkdir(parents=True, exist_ok=True)
        _add_worktree(repo, wt, "-b", br, str(wt), integration_branch(pid))
    return wt, br


def detached_worktree(home: Path, repo: str, pid: int, sha: str) -> Path:
    """A throw-away worktree at one exact commit (a release runs there, never in your folder)."""
    wt = project_root(home, pid) / "release" / sha[:12]
    with _repo_lock(repo):
        remove_worktree(repo, wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        _add_worktree(repo, wt, "--detach", str(wt), sha)
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
