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


# Config keys that name a command git runs (#1407). The repository's own config (`.git/config`, `config.worktree` and
# what they include) is shared with every task worktree, so an agent can write it.
_RUN_KEY = re.compile(r"(filter\.(.+)\.(?:clean|smudge|process)|merge\.(.+)\.driver|hook\.(.+)\.command"
                      r"|gpg\.(?:[^.]+\.)?program|gpg\.ssh\.defaultkeycommand)", re.S)
_GPG_DEFAULT = {"gpg.program": "gpg", "gpg.openpgp.program": "gpg", "gpg.x509.program": "gpgsm",
                "gpg.ssh.program": "ssh-keygen", "gpg.format": "openpgp", "user.signingkey": ""}
_SIGN_CHOICE = ("gpg.format", "user.signingkey")       # no command, but they choose how the owner's commit is signed
_OPENPGP = ("gpg.program", "gpg.openpgp.program")      # git treats these as one setting: the last one read wins
# Commands that run textconv / external diff commands by default (#1407 review R1), and the flags that stop them.
_NO_DIFF_CMDS = {"diff": ("--no-ext-diff", "--no-textconv"), "show": ("--no-ext-diff", "--no-textconv"),
                 "log": ("--no-ext-diff", "--no-textconv"), "whatchanged": ("--no-ext-diff", "--no-textconv"),
                 "format-patch": ("--no-ext-diff", "--no-textconv"), "blame": ("--no-textconv",),
                 "annotate": ("--no-textconv",)}


def _git_true(value: str, has_value: bool) -> bool:
    """git's boolean rule for a config value (review R2): a key with no `=` is true; false/no/off, empty and integer
    zero are false. Anything else counts as true, so an unreadable value fails closed."""
    if not has_value:
        return True
    v = value.strip().lower()
    return not (v in ("false", "no", "off", "") or re.fullmatch(r"[+-]?0+[kmg]?", v))


def _config_overrides(cwd: str | Path, ident: bool = True) -> list[str]:
    """`-c` pairs that neutralise commands named in the repository's own config: a filter driver becomes empty (git
    skips it; a `required` filter then fails), a merge driver becomes `false` (a conflict, nothing merged), a signing
    program falls back to your global/system one (or git's default). Config-defined hooks (`hook.<name>.command`,
    newer git) become `true` in every scope, like `core.hooksPath`. ident=False (the owner's ship merge): the signing
    format and key also come from your global/system config (else git's default), never the repository's.
    A repository-config filter marked `required` (e.g. `git lfs install --local`) fails closed with a clear message:
    emptied, git gives a bare error or stores the raw file without the filter.
    Fails closed (GitError) when the config cannot be read (git older than 2.26 has no --show-scope) or a name cannot
    be overridden exactly on the command line (`=` in it, or bytes that are not UTF-8: git matches subsections
    byte for byte, so a lossy decode would override a different key).
    Residual risk (review R2, #1533): the config is read just before git runs; an entry written in between (by an
    agent process still running beside a task merge) is not seen. `ship --apply` refuses while tasks run."""
    p = subprocess.run(["git", "config", "--show-scope", "-z", "--list"], cwd=str(cwd), capture_output=True,
                       text=True, timeout=30, creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0"}),
                       encoding="utf-8", errors="surrogateescape")
    if p.returncode != 0:
        raise GitError("cannot read the git config (git 2.26 or newer is needed): " + (p.stderr or "").strip()[:200])
    items = p.stdout.split("\0")
    entries, has = [], {}
    for i in range(0, len(items) - 1, 2):            # a value-less key (`[filter "x"] required`) has no newline
        key, nl, val = items[i + 1].partition("\n")
        entries.append((items[i], key, val))
        has[i // 2] = bool(nl)
    owner = {k.lower(): v for scope, k, v in entries if scope in ("global", "system")}
    owner_exact = {k: v for scope, k, v in entries if scope in ("global", "system")}   # subsections match exactly
    pgp = [v for scope, k, v in entries if scope in ("global", "system") and k.lower() in _OPENPGP]
    owner.update(dict.fromkeys(_OPENPGP, pgp[-1] if pgp else "gpg"))     # your effective OpenPGP program, either alias
    required = {k.lower()[7:-9] for n, (_s, k, v) in enumerate(entries)
                if re.fullmatch(r"filter\..+\.required", k, re.I | re.S) and _git_true(v, has[n])}
    out: list[str] = []
    for scope, key, _v in entries:
        m = _RUN_KEY.fullmatch(key)
        mine = scope in ("local", "worktree")
        if not ((m and (mine or m.group(4))) or (not ident and mine and key.lower() in _SIGN_CHOICE)):
            continue
        if "=" in key or "\ufffd" in key or any("\udc80" <= c <= "\udcff" for c in key):
            raise GitError(f"cannot neutralise the git config entry {key!r}: remove it from the repository config")
        low = key.lower()
        drv = owner_exact.get(key) if m and (m.group(2) or m.group(3)) else None   # PR #1657 R3: `-c` would also hide
        if drv is not None:                                              # your own global driver of the same name
            out += ["-c", f"{key}={drv}"]
            continue
        if m and m.group(2) and mine and m.group(2).lower() in required:    # review R2: emptied, a required filter gives
            raise GitError(                                                  # a bare git error or stores the raw file
                f"the filter {m.group(2)!r} is set in the repository's own git config and marked required; JARVES "
                "never runs commands from that config (an agent can write it). Set the filter in your global "
                "config instead (for Git LFS: `git lfs install`, not `--local`), then remove it from the repository")
        val = "" if m and m.group(2) else "false" if m and m.group(3) else "true" if m and m.group(4) else owner.get(
            low, _GPG_DEFAULT.get(low, ""))
        if low == "user.signingkey" and low not in owner:       # PR #1657 R3: git reads `-c user.signingkey=` as
            val = _committer(owner)                              # `gpg -u ""`; its own default is the committer ident
        out += ["-c", f"{key}={val}"]
    return out


def _committer(owner: dict) -> str:
    """Your committer ident as git's default signing key; GitError when there is none to fall back to."""
    name = os.environ.get("GIT_COMMITTER_NAME") or owner.get("user.name", "")
    email = os.environ.get("GIT_COMMITTER_EMAIL") or owner.get("user.email", "")
    if not email:
        raise GitError("the repository config sets user.signingkey and you have no own key or user.email to sign "
                       "with: set user.signingkey or user.email in your global config, or remove it from the repository")
    return f"{name} <{email}>" if name else email


def git(cwd: str | Path, *args: str, check: bool = True, timeout: float = 120, ident: bool = True
        ) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Commands named in the repository's own config are neutralised too (#1407,
    see `_config_overrides`). `diff`, `show`, `log`, `whatchanged` and `format-patch` run with `--no-ext-diff
    --no-textconv`, `blame`/`annotate` with `--no-textconv`: `diff.external`, `diff.<x>.command` and
    `diff.<x>.textconv` (with an agent-written `info/attributes`) name commands too. Other subcommands never get
    these flags, so a new caller of another diff-producing command must add them.
    Raises GitError whatever `check` says when the repository config cannot be read or neutralised (fail closed:
    then no git runs); `check=False` only covers git's own exit status."""
    over = _config_overrides(cwd, ident)
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false", *over]
    if args and args[0] in _NO_DIFF_CMDS:
        args = (args[0], *_NO_DIFF_CMDS[args[0]], *args[1:])
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0"}), encoding="utf-8",
                           errors="replace")
        if p.returncode == 0 or attempt == LOCK_RETRIES or not _lock_contention(p.stderr):
            break
        time.sleep(0.15 * (attempt + 1))
    if check and p.returncode != 0:
        raise GitError((p.stderr or p.stdout or "git failed").strip()[:300])
    return p


def is_repo(path: str) -> bool:
    """True for a git work tree. A repository config JARVES cannot neutralise raises GitError (review R3), so the
    caller shows its guidance instead of 'not a git repository'."""
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


def merge_in(wt: Path, branch: str, ident: bool = True, message: str = "", no_ff: bool = False) -> list[str]:
    """Bring `branch` into the worktree's branch. Returns conflicting files ([] = merged); a conflict is aborted
    (nothing changes). A refusal without conflicts (e.g. an untracked file in the way) returns git's own reason.
    Takes the repository's lock: another task's worktree remove/prune/branch -D must not run during the merge (JAR-189)."""
    common = git(wt, "rev-parse", "--git-common-dir", check=False).stdout.strip()
    root = (Path(wt) / common).resolve() if common else Path(wt)
    with _repo_lock(root.parent if root.name == ".git" else root):
        p = git(wt, "merge", "--no-edit", "--no-stat", *(["--no-ff"] if no_ff else []), *(["-m", message] if message else []),
                branch, check=False, ident=ident)
        if p.returncode == 0:
            return []
        files = [x for x in git(wt, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split() if x]
        git(wt, "merge", "--abort", check=False)
    if files:
        return files
    why = " ".join((p.stderr or p.stdout or "merge failed").split())[:200]
    return [f"(not merged: {why})"]


def fast_forward(integration_wt: Path, branch: str) -> None:
    git(integration_wt, "merge", "--ff-only", "--no-stat", branch)


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
