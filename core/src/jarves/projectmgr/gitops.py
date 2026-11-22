"""Git for the project manager. Every JARVES worktree lives under `<home>/projects/<id>/` (never inside the owner's
repository, so tests/linters at the repo root never see them and agents do not inherit the owner's CLAUDE.md of a parent
folder). The owner's checkout is never touched: the integration branch `jarves/p<ID>/integration` has its own worktree,
merges are fast-forward only after the check ran on the merged result. JARVES never pushes."""
from __future__ import annotations

import fnmatch
import hashlib
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
TRUSTED_SCOPES = ("system", "global", "command")        # local/worktree config (and its includes) is agent-writable
# commands git takes from config (#1407): an agent-writable value is replaced, so git never runs it. `fail`: the
# command fails instead (a merge conflict, no diff/trailer/key). `sub`: no recursive submodule checkout/update (review R4)
_RUN_KEY = re.compile(r"^(?:(?P<filter>filter\.(?P<drv>.+)\.(?:clean|smudge|process))|(?P<hook>hook\..+\.command)"
                      r"|(?P<fail>merge\..+\.driver|gpg\.ssh\.defaultkeycommand|diff\.external|diff\..+\.(?:command|textconv)"
                      r"|trailer\..+\.(?:command|cmd)|core\.editor|sequence\.editor)"
                      r"|(?P<sub>submodule\.recurse|submodule\..+\.update)|(?P<cred>credential\.(?:.+\.)?helper)"
                      r"|(?P<prog>gpg(?:\.[^.]+)?\.program|core\.sshcommand|core\.askpass)"
                      r"|(?P<proto>protocol\.allow|protocol\..+\.allow)|(?P<proxy>core\.gitproxy))$")
# review R5 (#1635): `forge.push` runs ssh/askpass and credential helpers; `remote.*.receivepack` is first-wins in git
# (an override after it is ignored), so `git` passes `--receive-pack` on every push. An editor is never opened
# (merge --no-edit, commit -m), so an untrusted editor just fails
_SIGN_KEY = re.compile(r"^(gpg\..+|user\.signingkey|(commit|tag)\.gpgsign)$")
_NO_CONFIG_CMDS = ("rev-parse", "merge-base", "rev-list")   # never run a config-named command: no config read
_GPG_DEFAULT = {"gpg.program": "gpg", "gpg.openpgp.program": "gpg", "gpg.x509.program": "gpgsm",
                "gpg.ssh.program": "ssh-keygen", "core.sshcommand": "ssh", "core.askpass": ""}
_CFG_CACHE: dict[str, tuple[tuple, list]] = {}         # cwd -> (stamp of every file the config came from, rows)
_CFG_FILES: dict[str, list[str]] = {}                   # cwd -> the files the last read came from (stamped before a read)


def _norm_key(key: str) -> str:
    """git prints `section.Sub.Name` with the subsection as written (case-sensitive); only the section and the name are
    case-insensitive, so `filter.Evil.clean` must be overridden as `filter.Evil.clean`, never `filter.evil.clean`."""
    head, _, rest = key.partition(".")
    sub, dot, name = rest.rpartition(".")
    return f"{head.lower()}.{sub}{dot}{name.lower()}" if sub else f"{head.lower()}.{name.lower()}"


def _stamp(cwd: str, files: list[str]) -> tuple:
    """The content of every file the config came from, plus `.git` (a worktree's `.git` file names its git folder)."""
    out = []
    for f in [*files, ".git"]:
        path = os.path.join(cwd, f)
        try:
            out.append((f, "dir" if os.path.isdir(path) else hashlib.sha256(Path(path).read_bytes()).hexdigest()))
        except OSError:
            out.append((f, None))
    return tuple(out)


_NO_CACHE = ("include.", "includeif.", "extensions.worktreeconfig")


def _config_rows(cwd: str | Path) -> list[tuple[str, str, str]]:
    """(scope, key, value) of the effective config. Cached per folder until the content of a file it came from changes
    (review R3: one read per call doubled the git processes); a read is cached only when the same files hashed the
    same just before and just after it, so the first read of a folder is never cached. Never cached when an agent-writable file could appear or
    apply without a listed file changing: an include, `includeIf` (its branch condition), a worktree config, no
    local rows at all, or no `.git` in the folder."""
    key = os.path.realpath(str(cwd))
    hit = _CFG_CACHE.get(key)
    if hit and _stamp(key, [f for f, _ in hit[0][:-1]]) == hit[0]:
        return hit[1]
    known = _CFG_FILES.get(key)
    before = _stamp(key, known) if known is not None else None   # review R1 (#1635): stamped BEFORE the read too
    try:
        p = subprocess.run(["git", "config", "--list", "--show-scope", "--show-origin", "-z"], cwd=str(cwd),
                           capture_output=True, timeout=30, creationflags=_NOWIN,
                           env=child_env({"GIT_TERMINAL_PROMPT": "0"}))
    except subprocess.SubprocessError as e:                 # OSError (no such folder) propagates as before
        raise GitError(f"could not read the git config: {e}") from e
    if p.returncode != 0:
        raise GitError("could not read the git config: " + p.stderr.decode("utf-8", "replace").strip()[:200])
    items = p.stdout.decode("utf-8", "replace").split("\0")
    rows, files = [], []
    for scope, origin, kv in zip(items[0::3], items[1::3], items[2::3]):
        rows.append((scope, _norm_key(kv.partition("\n")[0]), kv.partition("\n")[2]))
        if origin.startswith("file:") and origin[5:] not in files:
            files.append(origin[5:])
    _CFG_CACHE.pop(key, None)
    _CFG_FILES[key] = files
    after = _stamp(key, files)
    # cached only when nothing changed around the read: a write during or right after it (stamped after, read before)
    # would otherwise store old rows under the new file's stamp, so the agent's command would run on every later call
    if known == files and before == after and os.path.lexists(os.path.join(key, ".git")) and \
            any(s == "local" for s, _, _ in rows) and not any(k.startswith(_NO_CACHE) for _, k, _ in rows):
        _CFG_CACHE[key] = (after, rows)
    return rows


def config_overrides(cwd: str | Path) -> list[tuple[str, str]]:
    """(key, value) pairs that neutralise the config-named commands (`_RUN_KEY`) set in the agent-writable
    local/worktree config (#1407); the owner's global/system value is kept. Untrusted signing settings turn signing
    off on every call, whoever's identity the call uses (reviews R1, R3), so your key never signs on an agent's say-so.
    Transports (review R1, #1635): `ext::` (runs any command) is always `never` unless your own config allows it; an
    untrusted `protocol[.<name>].allow` is replaced; an untrusted `core.gitProxy` (first match wins, so it cannot be
    overridden) turns the `git://` protocol off.
    Unreadable config: `GitError` (fail closed). The read-then-run window is tracked in #1533. `git` passes them as
    GIT_CONFIG_KEY_n/VALUE_n, never `-c k=v`: git splits `-c` at the first `=`, and a subsection may hold one."""
    rows = _config_rows(cwd)
    trusted = {key: val for scope, key, val in rows if scope in TRUSTED_SCOPES}   # all of them first: `command` is last
    out: list[tuple[str, str]] = []
    signing = False
    for scope, key, _val in rows:
        if scope in TRUSTED_SCOPES:
            continue
        signing = signing or bool(_SIGN_KEY.match(key))
        m = _RUN_KEY.match(key)
        if not m:
            continue
        if m["filter"]:                                     # your own same-named value stays (review R4)
            req = f"filter.{m['drv']}.required"
            out += [(key, trusted.get(key, "")), (req, trusted.get(req, "false") if key in trusted else "false")]
        elif m["hook"]:
            out.append((key, trusted.get(key, "exit 0")))
        elif m["fail"]:
            out.append((key, trusted.get(key, "exit 1")))   # fails closed, never the agent's program
        elif m["sub"]:
            out.append((key, trusted.get(key, "false" if key == "submodule.recurse" else "none")))
        elif m["cred"]:                                     # a list: "" clears it, then only your own helpers again
            if (key, "") not in out:
                out += [(key, ""), *[(k, v) for sc, k, v in rows if sc in TRUSTED_SCOPES and k == key]]
        elif m["proto"]:
            out.append((key, trusted.get(key, "never" if key == "protocol.ext.allow" else "user")))
        elif m["proxy"]:
            out.append(("protocol.git.allow", "never"))
        else:
            out.append((key, trusted.get(key, _GPG_DEFAULT.get(key, "gpg"))))
    if signing:
        out += [("commit.gpgsign", "false"), ("tag.gpgsign", "false")]
    return [*out, ("protocol.ext.allow", trusted.get("protocol.ext.allow", "never"))]   # last: it wins


def overridden_names(cwd: str | Path) -> list[str]:
    """What `config_overrides` turns off, safe to show the owner (review R2, #1635): a subsection is the agent's own
    text, so it is shown as `*`; the forced `protocol.ext.allow` is left out. Sorted, unique, each at most 60 chars."""
    names = set()
    for key, _ in config_overrides(cwd)[:-1]:
        head, _, rest = key.partition(".")
        sub, dot, name = rest.rpartition(".")
        names.add((f"{head}.*.{name}" if sub else key)[:60])
    return sorted(names)


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Other config-named commands: `config_overrides` (#1407)."""
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false"]
    env = child_env({"GIT_TERMINAL_PROMPT": "0"})
    if not args or args[0] not in _NO_CONFIG_CMDS:
        try:
            pairs = config_overrides(cwd)
        except GitError as e:                               # the command never runs; check=False still never raises
            if check:
                raise
            return subprocess.CompletedProcess(["git", *args], 128, "", str(e))
        env["GIT_CONFIG_COUNT"] = str(len(pairs))
        for n, (k, v) in enumerate(pairs):
            env[f"GIT_CONFIG_KEY_{n}"], env[f"GIT_CONFIG_VALUE_{n}"] = k, v
    # #1407: no external diff or textconv on any JARVES diff. `log -p`/`show` would also honour textconv (an untrusted
    # one is `exit 1` there, so they would fail, not run it); JARVES uses neither (only `log --oneline`).
    if args and args[0] in ("push", "fetch"):           # never the repo config's receive/upload-pack (review R5,
        # #1635): git takes the FIRST value, so an override cannot win; yours (global/system) is kept (review R4)
        name = next((a for a in args[1:] if not a.startswith("-")), "origin")
        kind = "receivepack" if args[0] == "push" else "uploadpack"
        try:
            mine = [v for sc, k, v in _config_rows(cwd) if sc in TRUSTED_SCOPES and k == f"remote.{name}.{kind}"]
        except GitError:
            mine = []
        prog = mine[0] if mine else f"git-{kind[:-4]}-pack"
        args = (args[0], f"--{kind[:-4]}-pack={prog}", *args[1:])
    if args and args[0] == "diff":
        args = ("diff", "--no-ext-diff", "--no-textconv", *args[1:])
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
