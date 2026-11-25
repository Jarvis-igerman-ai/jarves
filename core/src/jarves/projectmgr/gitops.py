"""Git for the project manager. Every JARVES worktree lives under `<home>/projects/<id>/` (never inside the owner's
repository, so tests/linters at the repo root never see them and agents do not inherit the owner's CLAUDE.md of a parent
folder). The owner's checkout is never touched: the integration branch `jarves/p<ID>/integration` has its own worktree,
merges are fast-forward only after the check ran on the merged result. JARVES never pushes."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import stat
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


# (#1390) config keys whose value is a command git runs: filters on checkout/add/merge, merge drivers on a merge,
# diff drivers and diff.external on a diff, whenever a .gitattributes or `.git/info/attributes` entry names them
_DRIVER_KEY = re.compile(r"^(filter\.(.+)\.(clean|smudge|process|required)|merge\.(.+)\.driver"
                         r"|diff\.(.+)\.(command|textconv)|diff\.external)$", re.S)
# (#1407) programs git runs to sign (the ship merge uses the owner's signing): owner value, else git's default program
_GPG_KEYS = {"gpg.program": "openpgp", "gpg.openpgp.program": "openpgp", "gpg.x509.program": "x509",
             "gpg.ssh.program": "ssh", "gpg.ssh.defaultkeycommand": "sshkey"}
# "" for the key command: git treats an empty value as no command (checked on git 2.43: an ssh-signed commit with a
# user.signingKey still works, nothing runs); without a key git then fails with its own error, closed
_GPG_DEFAULT = {"openpgp": "gpg", "x509": "gpgsm", "ssh": "ssh-keygen", "sshkey": ""}
_GPG_OUT = {"openpgp": ("gpg.program", "gpg.openpgp.program"), "x509": ("gpg.x509.program",),
            "ssh": ("gpg.ssh.program",), "sshkey": ("gpg.ssh.defaultKeyCommand",)}
_HOOK_KEY = re.compile(r"^hook\.(.+)\.[^.]+$", re.S)   # config-defined hooks (newer git): not stopped by core.hooksPath
HOOK_NOOP = "exit 0 #"                     # run through sh with "$@" appended: the arguments become a comment
_SUBMODULE_KEY = re.compile(r"^submodule\.(.+)\.[^.]+$", re.S)
_SHARED_SCOPES = ("local", "worktree")      # the repository's own config: agents can write it through `.git`
MIN_GIT = (2, 31)                           # GIT_CONFIG_COUNT/KEY/VALUE: older git ignores them (fails open)
_GIT_OK: list[bool] = []                    # the version check passed once in this process


def _run(cwd: str | Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True, timeout=30,
                          creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0", **_task_env(cwd)}),
                          encoding="utf-8", errors="replace")


# #1599: a task folder's `.git` is the agent's own copy. JARVES's git there uses the owner-side worktree entry
# (GIT_DIR) instead, so a key the agent writes with `git config` (or a hook, info/attributes) in its folder never
# reaches the config JARVES's git reads, whenever it is written. Real folder path -> env, filled by adopt_task_folder.
_TASK_GIT: dict[str, dict[str, str]] = {}
_TASK_LOCK = threading.Lock()
_ADOPT_LOCK = threading.RLock()     # one adoption at a time (it rebuilds `.git`); reentrant: adoption runs git()


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _task_env(cwd: str | Path) -> dict[str, str]:
    """GIT_DIR/GIT_WORK_TREE for a folder under `<home>/projects/<id>/wt/`; {} elsewhere. A folder not yet adopted in
    this process (a restart, an older install) is adopted through the project's integration worktree; without one
    it fails closed (GitError): the folder's own `.git` is never read."""
    p = Path(os.path.realpath(str(cwd)))
    for d in (p, *p.parents):
        if d.parent.name == "wt" and d.parent.parent.parent.name == "projects":
            with _ADOPT_LOCK:
                with _TASK_LOCK:
                    env = _TASK_GIT.get(_key(d))
                if env is None:
                    integ = d.parent.parent / "integration"
                    if not (integ / ".git").is_file():
                        raise GitError(f"task folder {d.name} is not adopted and its project has no integration "
                                       "folder; JARVES will not run git with the folder's own .git")
                    adopt_task_folder(str(integ), d)
                    with _TASK_LOCK:
                        env = _TASK_GIT[_key(d)]
            return dict(env)
    return {}


def adopt_task_folder(repo: str, wt: Path) -> None:
    """Finds the owner-side worktree entry of `wt` (its `gitdir` file names `wt/.git`), registers it, and gives the
    folder a private `.git` (_private_git) unless it has one. Not covered: an agent writing the owner's
    repository files through their path rather than through git in its folder."""
    common = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    entry = next((e for e in sorted((common / "worktrees").glob("*")) if (e / "gitdir").is_file() and
                  _key(Path((e / "gitdir").read_text(encoding="utf-8").strip()).parent) == _key(wt)), None)
    if entry is None:
        raise GitError(f"no worktree entry for task folder {Path(wt).name}")
    with _TASK_LOCK:
        _TASK_GIT[_key(wt)] = {"GIT_DIR": str(entry), "GIT_WORK_TREE": os.path.realpath(str(wt))}
    dot = Path(wt) / ".git"
    if not dot.is_dir() or dot.is_symlink():
        _private_git(wt)


def _writable(func, path, exc) -> None:
    """rmtree on Windows: git objects are read-only files. A link is never chmod-ed (that would follow it)."""
    if os.path.islink(path):
        raise exc
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _private_git(wt: Path) -> None:
    """(Re)builds the task folder's private `.git` at the task branch's current commit (after adoption and after
    every JARVES commit): a fresh repository on that branch, the index copied, the owner's objects reached read-only
    through `objects/info/alternates`. The agent's own commits there are dropped; JARVES commits the folder's files."""
    env = _task_env(wt)
    entry = Path(env["GIT_DIR"])
    common = Path(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    sha = git(wt, "rev-parse", "HEAD").stdout.strip()
    head = (entry / "HEAD").read_text(encoding="utf-8").strip()
    dot = Path(wt) / ".git"
    if dot.is_dir() and not dot.is_symlink():
        shutil.rmtree(dot, onexc=_writable)            # rmtree unlinks a link inside, never follows it
    else:
        dot.unlink(missing_ok=True)
    git(Path(wt).parent, "init", "-q", "--template=", str(wt))
    (dot / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (dot / "objects" / "info" / "alternates").write_text(str(common / "objects") + "\n", encoding="utf-8")
    ref = head[5:].strip() if head.startswith("ref: ") else ""
    if ref:
        (dot / ref).parent.mkdir(parents=True, exist_ok=True)
        (dot / ref).write_text(sha + "\n", encoding="utf-8")
    (dot / "HEAD").write_text(f"ref: {ref}\n" if ref else sha + "\n", encoding="utf-8")
    if (entry / "index").is_file():
        shutil.copyfile(entry / "index", dot / "index")


def require_git(cwd: str | Path) -> None:
    """GitError below git 2.31: the overrides below would be silently ignored there. Checked once per process."""
    if _GIT_OK:
        return
    m = re.search(r"(\d+)\.(\d+)", _run(cwd, "version").stdout or "")
    if not m or (int(m.group(1)), int(m.group(2))) < MIN_GIT:
        raise GitError(f"JARVES needs git {MIN_GIT[0]}.{MIN_GIT[1]} or later (found: {m.group(0) if m else '?'})")
    _GIT_OK.append(True)


def _pairs(out: str) -> list[tuple[str, str, str]]:
    parts = (out or "").split("\0")
    return [(scope, *(entry.split("\n", 1) + [""])[:2]) for scope, entry in zip(parts[0::2], parts[1::2])]


def shared_config_overrides(cwd: str | Path) -> dict[str, str]:
    """Command-scope overrides (GIT_CONFIG_COUNT/KEY/VALUE env, git 2.31+: any subsection name, `=` included, is
    safe) that blank every driver command the repository's shared config names, include.path/includeIf files
    included (#1390). A blank filter is a pass-through (required=false); a blank merge driver fails the merge (it
    conflicts, so a local `merge.<x>.driver`, even a harmless one, now always aborts merge_in); a blank diff driver
    fails the command, so git() adds --no-ext-diff --no-textconv to its own diff/show/log calls. The system and
    global config (the owner's own) stay as they are. A filter the shared config names that the owner's config also
    defines (Git LFS after `git lfs install --local`) raises GitError: a silent pass-through would commit binaries
    instead of pointers. Every submodule named in any config or `.gitmodules` gets ignore=dirty, so status, diff and
    merge never look inside its work tree (gitlink commit changes still show). Fails closed: an unreadable config
    raises GitError."""
    require_git(cwd)
    p = _run(cwd, "config", "--show-scope", "-z", "--get-regexp", r"^(filter|merge|diff|submodule|gpg|hook)\.")
    if p.returncode not in (0, 1):                                        # 1: no key matched (or not a repository)
        raise GitError(f"cannot read the shared git config: {(p.stderr or '').strip()}"[:300])
    entries = _pairs(p.stdout)
    if (Path(cwd) / ".gitmodules").is_file():                            # agent-written content: names only
        g = _run(cwd, "config", "-f", ".gitmodules", "-z", "--get-regexp", r"^submodule\.")
        if g.returncode not in (0, 1):
            raise GitError(f"cannot read .gitmodules: {(g.stderr or '').strip()}"[:300])
        entries += [("gitmodules", e.split("\n", 1)[0], "") for e in (g.stdout or "").split("\0") if e]
    keys: dict[str, str] = {}
    owner_filters: set[str] = set()
    shared_filters: set[str] = set()
    owner_gpg: dict[str, str] = {}
    shared_gpg: set[str] = set()
    for scope, key, value in entries:
        sm, m, hk, gk = _SUBMODULE_KEY.match(key), _DRIVER_KEY.match(key), _HOOK_KEY.match(key), _GPG_KEYS.get(key)
        if gk and scope in _SHARED_SCOPES:
            shared_gpg.add(gk)
        elif gk:                                                          # read order: the last owner value wins
            owner_gpg[gk] = value
        elif hk:                                                          # every scope: no hook runs (#1381)
            keys[f"hook.{hk.group(1)}.command"] = HOOK_NOOP
        elif sm:
            keys[f"submodule.{sm.group(1)}.ignore"] = "dirty"
        elif not m:
            continue
        elif scope not in _SHARED_SCOPES:
            if m.group(2) is not None:
                owner_filters.add(m.group(2))
        elif m.group(2) is not None:                                      # every command of that filter, not one
            shared_filters.add(m.group(2))
            keys.update({f"filter.{m.group(2)}.{k}": "" for k in ("clean", "smudge", "process")})
            keys[f"filter.{m.group(2)}.required"] = "false"
        else:
            keys[key] = ""
    clash = sorted(owner_filters & shared_filters)
    if clash:
        raise GitError(f"the repository's own git config redefines your filter {clash[0]!r}; JARVES will not run "
                       "git here (remove the repository-level filter keys, e.g. `git config --local "
                       f"--remove-section filter.{clash[0]}`)"[:300])
    for gk in sorted(shared_gpg):
        keys.update({k: owner_gpg.get(gk, _GPG_DEFAULT[gk]) for k in _GPG_OUT[gk]})
    env = {"GIT_CONFIG_COUNT": str(len(keys))} if keys else {}
    for i, (k, v) in enumerate(keys.items()):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return env


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Commands named in the shared config by filter/merge/diff drivers are
    blanked (#1390, see shared_config_overrides); the editors are `:`; submodules are never recursed into or
    checked for changes. `args` must start with the subcommand: any global option (-C, --git-dir, --exec-path...)
    could run git elsewhere than the repository whose config was read. Signing programs from the shared config fall
    back to the owner's (#1407); every config-defined hook command is HOOK_NOOP; replace refs and grafts are ignored.
    Not covered: core.sshCommand, core.askPass, credential helpers, transport commands (remote.<x>.uploadpack/
    receivepack, ext:: URLs), core.alternateRefsCommand, a shallow file, and a key added between the config read
    and the command (one read per call) in a repository other than a task folder (#1599: see adopt_task_folder)."""
    if args and args[0].startswith("-"):
        raise GitError(f"git() takes the subcommand first, not the global option {args[0].split('=', 1)[0]}")
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false",
           "-c", "submodule.recurse=false", "-c", "diff.ignoreSubmodules=dirty"]
    env = child_env({"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": ":", "GIT_SEQUENCE_EDITOR": ":",
                     "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": NO_HOOKS,   # #1407: an id is its own content
                     **shared_config_overrides(cwd), **_task_env(cwd)})
    if args[:1] in (("diff",), ("show",), ("log",)):   # a blank diff driver fails these; the flags show plain text
        args = (args[0], "--no-ext-diff", "--no-textconv", "--ignore-submodules=dirty", *args[1:])
    elif args[:1] == ("status",):
        args = ("status", "--ignore-submodules=dirty", *args[1:])
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
        adopt_task_folder(repo, wt)                    # under the same (reentrant) lock as the add
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
    """`add -A` checks every populated submodule for changes, which runs that submodule's own (agent-writable)
    filters whatever ignore says (#1390): gitlinks are left out (an agent may not change them anyway)."""
    links = [e.split("\t", 1)[1] for e in git(wt, "ls-files", "-s", "-z").stdout.split("\0")
             if e.startswith("160000 ") and "\t" in e]
    git(wt, "add", "-A", "--", ".", *(f":(exclude,literal){x}" for x in links))
    if git(wt, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    git(wt, "commit", "-m", message[:200], "--no-verify")
    if _task_env(wt):
        _private_git(wt)                               # #1599: the agent's copy follows the new commit
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
            if _task_env(wt):
                _private_git(wt)                       # #1599: the agent's copy follows the merge
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
