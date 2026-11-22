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
IDENT = ["-c", "user.name=JARVES", "-c", "user.email=jarves@localhost", "-c", "commit.gpgsign=false",
         "-c", "tag.gpgsign=false"]        # agent work is never signed (JAR-678 council)
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


DIFF_CMDS = ("diff", "show", "log")
NO_CONFIG_CMDS = ("rev-parse", "merge-base")   # run no config-named command: no extra config read (review R5)
_DRIVER = re.compile(r"^(filter|merge)\.(.+)\.(clean|smudge|process|driver|required)$", re.S)
_HOOK = re.compile(r"^hook\.(.+)\.[^.]+$", re.S)
_SIGN = {"gpg.program": "gpg", "gpg.openpgp.program": "gpg", "gpg.x509.program": "gpgsm",
         "gpg.ssh.program": "ssh-keygen", "gpg.ssh.defaultkeycommand": "",
         "gpg.format": "openpgp", "user.signingkey": ""}      # format and key: the owner's too (review R4)
_UNTRUSTED = ("local", "worktree")      # the shared .git/config (+ includes) and config.worktree: agent-writable
FILTER_FAIL = "exit 1"                  # a repository-only filter fails (required), never stores raw content


def _config_overrides(cwd: str | Path, pre: list[str], timeout: float, sign: bool = False
                      ) -> list[str] | subprocess.CompletedProcess:
    """`-c` pairs that override every command the repository's own config names at this read (#1407; a later write
    before git's own read is not covered, #1533); the owner's global/system config is kept, also for a repository
    copy of the same key. A filter the repository config sets but the owner does not fails closed (`exit 1`,
    required): a file it applies to stops the command instead of being stored unconverted (git-crypt). A merge
    driver gets `exit 1` (always a conflict), any config-defined hook (`hook.<name>.command`/`.event`) `exit 0`;
    signing programs and format always come from the owner's scope; the owner's key (else the owner's committer
    email) only when `sign` (ident=False, the ship path), never for agent work (JAR-678 council).
    A failed read is returned as is (the caller fails closed)."""
    p = subprocess.run(["git", *pre, "config", "--list", "--show-scope", "-z"], cwd=str(cwd), capture_output=True,
                       text=True, timeout=timeout, creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0"}),
                       encoding="utf-8", errors="replace")
    if p.returncode == 129:                     # an unknown option: `--show-scope` needs git 2.26
        return subprocess.CompletedProcess(p.args, 129, "", "JARVES projects need git 2.38 or newer (#1407)")
    if p.returncode != 0:
        return p
    items = p.stdout.split("\0")
    owner: dict[str, str] = {}
    out: dict[str, str] = {}
    filters: dict[str, set[str]] = {}           # filter name -> keys the repository config sets
    email = ""
    for scope, entry in zip(items[0::2], items[1::2]):
        key, _, value = entry.partition("\n")
        if key == "user.email" and scope in ("global", "system"):    # never `command` (IDENT) or the repo
            email = value                       # the owner's committer email: the fallback signing key (review R4)
        if scope not in _UNTRUSTED:
            if key in _SIGN or _DRIVER.match(key):
                owner[key] = value
                if key in ("gpg.program", "gpg.openpgp.program"):
                    owner["pgp"] = value                # the last of the two wins, as in git
            continue
        m, h = _DRIVER.match(key), _HOOK.match(key)
        if (m or h) and "=" in key:             # `-c` would split the key there: refuse, never half-apply
            return subprocess.CompletedProcess(p.args, 128, "", f"refusing the config key {key!r} (#1407)")
        if h:                                   # also `.event`: never attach any (owner) command to an event
            out[f"hook.{h.group(1)}.command"] = "exit 0"
        elif m and m.group(1) == "filter":
            filters.setdefault(m.group(2), set()).add(key)
        elif m and m.group(3) == "driver":
            out[key] = owner.get(key) or "exit 1"
        elif key in _SIGN:
            out.update({k: "" for k in _SIGN})
    for name, keys in filters.items():
        req = f"filter.{name}.required"
        cmds = keys - {req}
        if all(k in owner for k in cmds):       # a repository copy of the owner's filter: the owner's values
            out.update({k: owner[k] for k in cmds}, **{req: owner.get(req, "false")})
        else:
            out.update({k: FILTER_FAIL for k in cmds}, **{req: "true"})
    if "gpg.program" in out:                    # git reads gpg.program and gpg.openpgp.program into one setting
        pgp = owner.get("pgp") or "gpg"
        out.update({k: owner.get(k) or d for k, d in _SIGN.items()}, **{"gpg.program": pgp, "gpg.openpgp.program": pgp})
        if not sign:                            # agent work: no owner key is ever offered to a signer
            out.update({"user.signingkey": "", "gpg.ssh.defaultkeycommand": ""})
        elif not out["user.signingkey"] and out["gpg.format"] != "ssh":
            out["user.signingkey"] = email      # an empty key is used as is; git's own default is the committer
    return [x for k, v in out.items() for x in ("-c", f"{k}={v}")]


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh; `submodule.recurse=false` keeps checkout, reset and `worktree add`
    out of submodules (whose own config the overrides cannot name). This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Commands named in the repository's own config, as read just before the
    command, are overridden too (#1407, see `_config_overrides`); a config entry written between that read and the
    command's own read is not (open race, #1533). diff/show/log never run an external diff or textconv command
    (projectmgr calls no blame, `cat-file --textconv` or `stash show -p`). Cost: one extra `git config` process per
    call except rev-parse/merge-base, which run no such command; needs git 2.38 or newer (merge-tree --write-tree)."""
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false",
           "-c", "submodule.recurse=false"]     # no checkout/reset/worktree add child in a submodule (review R1)
    over = [] if args and args[0] in NO_CONFIG_CMDS else _config_overrides(cwd, pre, timeout, sign=not ident)
    if isinstance(over, subprocess.CompletedProcess):          # the config cannot be read: fail closed
        if check:
            raise GitError((over.stderr or "git config failed").strip()[:300])
        return over
    if args and args[0] in ("status", "diff"):     # no child git in a submodule (its own config, review R1)
        args = (args[0], "--ignore-submodules=dirty", *args[1:])
    if args and args[0] in DIFF_CMDS:
        args = (args[0], "--no-ext-diff", "--no-textconv", *args[1:])
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *over, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0"}), encoding="utf-8",
                           errors="replace")
        if p.returncode == 0 or attempt == LOCK_RETRIES or not _lock_contention(p.stderr):
            break
        time.sleep(0.15 * (attempt + 1))
    if check and p.returncode != 0:
        raise GitError(_hint((p.stderr or p.stdout or "git failed").strip()[:300]))
    return p


def _hint(err: str) -> str:
    """What the owner can do about a #1407 override that stopped git."""
    if f"'{FILTER_FAIL}'" in err:
        return err + " (a filter set only in the repository's git config does not run: set it in your global config)"
    if "gpg" in err.lower() or "signing" in err.lower():
        return err + " (check your global git signing settings: user.signingkey, gpg.format, gpg.program)"
    return err


def is_repo(path: str) -> bool:
    try:
        return git(path, "rev-parse", "--git-dir", check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def current_branch(path: str) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def commit_of(path: str | Path, ref: str) -> str:
    """The full commit `ref` names now (#1407): owner text and approvals name this, never a branch that can move."""
    try:
        return git(path, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
    except GitError as e:
        raise GitError(f"no commit for {ref}: {e}") from None


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


def retire_worktree(repo: str, wt: Path, branch: str, tip: str) -> str:
    """Remove a merged task's worktree and branch only while the branch is still exactly `tip` and the folder holds
    nothing else (#1407 review). Nothing is ever deleted by JARVES itself: no untracked file is cleaned (its origin,
    check output or new work, cannot be told apart, JAR-678 council) and the removal is never forced, so git's own
    clean check refuses anything that appears later. The branch is then deleted with a compare-and-swap.
    Returns "" (removed), "moved" (a later commit: the branch keeps it), "dirty" (a tracked edit, checked before
    anything is removed); "kept" (untracked files: folder and branch kept, see `untracked`) or "cleanup_failed" (git did not
    finish removing the folder, maybe partway, e.g. a Windows lock: never treat what is left as work to commit)."""
    if "projects" not in Path(wt).parts:
        raise GitError("refusing to remove a folder outside the JARVES projects area")
    ref = f"refs/heads/{branch}"
    with _repo_lock(repo):
        if git(repo, "rev-parse", "--verify", "-q", ref, check=False).stdout.strip() != tip:
            return "moved"
        if tracked_dirty(wt, tip):
            return "dirty"
        if untracked(wt):
            return "kept"
        if git(repo, "worktree", "remove", str(wt), check=False).returncode != 0:    # never --force: git refuses
            return "cleanup_failed"
        git(repo, "worktree", "prune", check=False)
        if git(repo, "update-ref", "-d", ref, tip, check=False).returncode != 0:
            return "moved"                              # a commit before the removal: the branch keeps it
        return ""


def untracked(wt: str | Path) -> set[str]:
    """Untracked, not ignored files in the worktree."""
    out = git(wt, "ls-files", "--others", "--exclude-standard", "-z").stdout
    return {x for x in out.split("\0") if x}


def tracked_dirty(wt: str | Path, base: str) -> bool:
    """A staged or unstaged change to a tracked file against the commit `base` (also after the branch ref is
    gone); untracked files (check output) do not count. A failed read counts as dirty (keep the work)."""
    git(wt, "update-index", "-q", "--refresh", check=False)
    return any(git(wt, "diff-index", "--quiet", "--ignore-submodules=dirty", *c, base, "--", check=False).returncode
               for c in (["--cached"], []))


def _add_all(wt: Path) -> None:
    """`git add -A` without the child `git status` it runs inside each submodule (review R1): that child reads the
    submodule's own config, whose filters the `-c` overrides cannot name. Submodule pointers are staged directly."""
    idx = git(wt, "ls-files", "-s", "-z").stdout.split("\0")
    links = [e.split("\t", 1)[1] for e in idx if e.startswith("160000 ") and "\t" in e]
    git(wt, "add", "-A", "--", ".", *[f":(exclude,literal){x}" for x in links])
    for x in links:
        sub = Path(wt) / x
        if (sub / ".git").exists():
            r = git(sub, "rev-parse", "--verify", "-q", "HEAD^{commit}", check=False)
            if r.returncode == 0:                   # an unborn submodule: its pointer stays (review R4)
                git(wt, "update-index", "--cacheinfo", f"160000,{r.stdout.strip()},{x}")
        elif not sub.exists():
            git(wt, "update-index", "--force-remove", "--", x)
        elif sub.is_dir() and not any(sub.iterdir()):                # uninitialised (`worktree add`): the pointer stays (council)
            continue
        else:                                       # a submodule replaced by plain files: stage them
            git(wt, "update-index", "--force-remove", "--", x)
            git(wt, "add", "-A", "--", f":(literal){x}")


def commit_all(wt: Path, message: str) -> bool:
    _add_all(wt)
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
    Takes the repository's lock: another task's worktree remove/prune/branch -D must not run during the merge (JAR-189)."""
    common = git(wt, "rev-parse", "--git-common-dir", check=False).stdout.strip()
    root = (Path(wt) / common).resolve() if common else Path(wt)
    with _repo_lock(root.parent if root.name == ".git" else root):
        p = git(wt, "merge", "--no-edit", *(["-m", message] if message else []), branch, check=False, ident=ident)
        if p.returncode == 0:
            return []
        files = [x for x in git(wt, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split() if x]
        git(wt, "merge", "--abort", check=False)
    if files:
        return files
    why = " ".join((p.stderr or p.stdout or "merge failed").split())[:200]
    return [f"(not merged: {_hint(why)})"]


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
