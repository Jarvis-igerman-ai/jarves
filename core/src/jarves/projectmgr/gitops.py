"""Git for the project manager. Every JARVES folder lives under `<home>/projects/<id>/` (never inside the owner's
repository, so tests/linters at the repo root never see them and agents do not inherit the owner's CLAUDE.md of a parent
folder). The owner's checkout is never touched: the integration branch `jarves/p<ID>/integration` has its own worktree,
merges are fast-forward only after the check ran on the merged result. JARVES never pushes.

Task folders are separate clones, not worktrees (#1599, #1390). An agent works in `<home>/projects/<id>/wt/t<S>-a<A>`,
a `git clone --shared` with its OWN `.git` (config, hooks, info/attributes), so nothing an agent writes there is
the config, hooks or attributes of the owner's repository. JARVES never runs git with a task folder's `.git`: it
reads the folder's files with the owner's git folder and a private index (`commit_all`, `folder_state`), merges with
`merge-tree` in the owner's repository (`merge_in`) and answers every diff from the task branch there (`where`).
No config read can race an agent's config write: JARVES's git only reads a config the agent never writes."""
from __future__ import annotations

import fnmatch
import logging
import os
import re
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
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """ident=False: the owner's own git identity and signing settings (a merge into the owner's branch).
    No git hook ever runs (#1381): `core.hooksPath` points at NO_HOOKS, which can never hold a hook, so neither the
    shared `.git/hooks` nor a `core.hooksPath` from the shared config (both writable by agents) runs on a merge,
    commit, index or ref write; `core.fsmonitor=false` stops an fsmonitor command set in the shared config, which
    git would otherwise run on every index refresh. This also skips the owner's own hooks (LFS, post-merge) when
    JARVES merges into the owner's branch. Other config-run commands (filter/merge drivers, gpg.program, config hooks)
    are kept out by never running git with a config an agent can write (module docstring, #1599).
    env: extra variables (GIT_INDEX_FILE for a private index)."""
    pre = [*(IDENT if ident else []), "-c", f"core.hooksPath={NO_HOOKS}", "-c", "core.fsmonitor=false"]
    for attempt in range(LOCK_RETRIES + 1):
        p = subprocess.run(["git", *pre, *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, env=child_env({"GIT_TERMINAL_PROMPT": "0", **(env or {})}),
                           encoding="utf-8", errors="replace")
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
    """A fresh task folder on a fresh branch from the CURRENT integration branch (earlier tasks' code is in it).
    The branch lives in the owner's repository. The folder is a separate clone of it with its own `.git` (#1599):
    `--shared` borrows the owner's objects read-only (cheap), and the remote is removed, so nothing points back."""
    _need_task_git()
    br = task_branch(pid, seq, attempt)
    wt = project_root(home, pid) / "wt" / f"t{seq}-a{attempt}"
    with _repo_lock(repo):
        remove_worktree(repo, wt, br)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "branch", br, integration_branch(pid))
        try:
            git(repo, "clone", "-q", "--shared", "--no-tags", "--single-branch", "--branch", br, "--origin",
                "jarves", os.path.abspath(str(repo)), str(wt))
            git(wt, "remote", "remove", "jarves")      # a fresh clone: its config is JARVES's own, no agent ran yet
        except GitError:
            remove_worktree(repo, wt, br)
            raise
    return wt, br


_TASK_DIR = re.compile(r"^t(\d+)-a(\d+)$")


def task_ref(wt: str | Path) -> tuple[Path, str] | None:
    """(the integration worktree, the task branch) for a task folder `<home>/projects/<pid>/wt/t<S>-a<A>`, from the
    path alone (never from anything inside the folder, which the agent controls). None for any other folder."""
    w = Path(wt)
    m = _TASK_DIR.match(w.name)
    if not m or w.parent.name != "wt" or not w.parent.parent.name.isdigit() or w.parent.parent.parent.name != "projects":
        return None
    return w.parent.parent / "integration", task_branch(int(w.parent.parent.name), int(m.group(1)), int(m.group(2)))


def where(wt: str | Path) -> tuple[Path, str]:
    """Where to ask git about a folder's work, and the revision that stands for its HEAD: for a task folder the
    integration worktree (the owner's repository) and the task branch, never the folder's `.git`; else (folder, HEAD)."""
    return task_ref(wt) or (Path(wt), "HEAD")


def _rev(cwd: Path, rev: str) -> str:
    return git(cwd, "rev-parse", "--verify", "--quiet", rev, check=False).stdout.strip()


_GIT_VERSION: list[tuple[int, ...]] = []


def git_version() -> tuple[int, ...]:
    """git's version as numbers, (0,) when unknown; read once."""
    if not _GIT_VERSION:
        m = re.search(r"(\d+)\.(\d+)", git(Path.cwd(), "--version", check=False).stdout)
        _GIT_VERSION.append(tuple(int(x) for x in m.groups()) if m else (0,))
    return _GIT_VERSION[0]


TASK_GIT = (2, 38)        # merge-tree --write-tree (2.38), rev-parse --path-format=absolute (2.31)


def _need_task_git() -> None:
    if git_version() < TASK_GIT:
        raise GitError(f"git {'.'.join(map(str, TASK_GIT))} or later is needed for task folders; "
                       f"this git is {'.'.join(map(str, git_version()))}")


class _FolderIndex:
    """git on a task folder's FILES with the owner's git folder and a private index outside the task folder, so the
    folder's `.git` (config, hooks, info/attributes, index) is never read. git never adds a `.git` entry itself."""

    def __init__(self, wt: str | Path):
        ref = task_ref(wt)
        if ref is None:
            raise GitError("not a JARVES task folder")
        _need_task_git()
        self.wt, (self.cwd, self.branch) = Path(wt), ref
        self.gitdir = git(self.cwd, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        self.index = ""

    def __enter__(self) -> "_FolderIndex":
        tmp = tempfile.mkdtemp(prefix="idx-", dir=str(self.cwd.parent))
        self.index = os.path.join(tmp, "index")
        return self

    def __exit__(self, *_exc) -> None:
        shutil.rmtree(os.path.dirname(self.index), ignore_errors=True)

    def run(self, *args: str, check: bool = True, index: str = "") -> subprocess.CompletedProcess:
        return git(self.cwd, f"--git-dir={self.gitdir}", f"--work-tree={self.wt}", *args, check=check,
                   env={"GIT_INDEX_FILE": index or self.index})

    def sync_clone(self, sha: str) -> None:
        """Move the clone's own branch and index to `sha` (review R1), so the agent's `git status` matches the task
        branch: the ref is written as text and the index by the owner's git (objects come through `--shared`), never
        by git with the folder's `.git`. Skipped and logged when the clone is on another branch or any path is a link."""
        g = self.wt / ".git"
        ref = g.joinpath("refs", "heads", *self.branch.split("/"))
        chain = [g, *[g.joinpath("refs", "heads", *self.branch.split("/")[:i]) for i in range(len(self.branch.split("/")))],
                 g / "refs", ref, g / "index"]
        if folder_branch(self.wt) != self.branch or any(x.is_symlink() for x in chain) or not ref.parent.is_dir():
            _LOG.warning("task folder %s: its own git view was not updated (a link or another branch)", self.wt)
            return
        fd, tmp = tempfile.mkstemp(prefix=".jarves-", dir=str(ref.parent))
        try:
            with os.fdopen(fd, "w", encoding="ascii") as fh:
                fh.write(sha + "\n")
            os.replace(tmp, ref)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
        if self.run("read-tree", sha, check=False, index=str(g / "index")).returncode == 0:
            self.run("update-index", "-q", "--refresh", check=False, index=str(g / "index"))

    def head(self) -> str:
        sha = _rev(self.cwd, f"{self.branch}^{{commit}}")
        if not sha:
            raise GitError(f"the task branch {self.branch} is missing")
        return sha

    def tree(self, head: str) -> str:
        """The tree of the folder's files now (a tracked file that matches .gitignore stays, as with `add -A`)."""
        self.run("read-tree", head)
        self.run("add", "-A")
        return self.run("write-tree").stdout.strip()


def folder_branch(wt: str | Path) -> str:
    """The branch a task clone has checked out, read from its `.git/HEAD` file as text (no git process, so nothing
    in the folder's config runs). "" when unknown: an older worktree, a detached HEAD or anything odd."""
    head = Path(wt) / ".git" / "HEAD"
    try:
        if head.parent.is_symlink() or head.is_symlink() or not head.is_file() or head.stat().st_size > 4096:
            return ""
        text = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[len("ref: refs/heads/"):] if text.startswith("ref: refs/heads/") else ""


def folder_state(wt: str | Path) -> tuple[str, bool]:
    """(the task branch's commit, do the folder's files differ from it). Writes only loose objects."""
    with _FolderIndex(wt) as f:
        head = f.head()
        return head, f.tree(head) != _rev(f.cwd, f"{head}^{{tree}}")


def detached_worktree(home: Path, repo: str, pid: int, sha: str) -> Path:
    """A throw-away worktree at one exact commit (a release runs there, never in your folder)."""
    wt = project_root(home, pid) / "release" / sha[:12]
    with _repo_lock(repo):
        remove_worktree(repo, wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--detach", str(wt), sha)
    return wt


def _rmtree(path: Path) -> None:
    """Remove a folder, git's read-only object files included (Windows refuses to delete a read-only file). Only a
    plain file is made writable, never through a link: an agent fills the folder and may plant a link to an owner
    file outside it (#1599). On POSIX a mode change cannot help (removal depends on the parent folder), so nothing
    is changed there. What still cannot be removed is left in place and logged (cleanup is not a task failure)."""
    left: list[str] = []

    def again(func, p, exc):
        if _WIN:
            try:
                st = os.lstat(p)
                if stat.S_ISREG(st.st_mode) and not getattr(st, "st_file_attributes", 0) & _REPARSE:
                    os.chmod(p, st.st_mode | stat.S_IWRITE, **_NOFOLLOW)
                    func(p)
                    return
            except OSError as e:
                exc = e
        left.append(f"{p}: {exc}")
    shutil.rmtree(path, onexc=again)
    if left:
        _LOG.warning("could not remove %d item(s) under %s, first: %s", len(left), path, left[0][:300])


_LOG = logging.getLogger(__name__)
_WIN = os.name == "nt"
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)              # a symlink or a junction on Windows
_NOFOLLOW = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}


def remove_worktree(repo: str, wt: Path, branch: str = "") -> None:
    """Only ever called for paths under <home>/projects (callers build them); a missing one is fine."""
    if "projects" not in Path(wt).parts:
        raise GitError("refusing to remove a folder outside the JARVES projects area")
    with _repo_lock(repo):
        git(repo, "worktree", "remove", "--force", str(wt), check=False)     # a worktree from an older install
        if Path(wt).exists():
            _rmtree(Path(wt))
        git(repo, "worktree", "prune", check=False)
        if branch:
            git(repo, "branch", "-D", branch, check=False)


def commit_all(wt: Path, message: str) -> bool:
    """Commit what is in a task folder onto its task branch, in the owner's repository (#1599): the files go through
    a private index (`_FolderIndex`), then commit-tree and a compare-and-swap ref update; the clone's own branch and
    index follow (`sync_clone`). The task branch is the record. False: nothing changed."""
    with _FolderIndex(wt) as f:
        old = f.head()
        tree = f.tree(old)
        if tree == _rev(f.cwd, f"{old}^{{tree}}"):
            return False
        new = git(f.cwd, "commit-tree", tree, "-p", old, "-m", message[:200]).stdout.strip()
        git(f.cwd, "update-ref", f"refs/heads/{f.branch}", new, old)
        f.sync_clone(new)
    return True


def changed_files(wt: Path, base: str) -> list[str]:
    """Every path the task changed, as plain text. -z: git would otherwise print a non-ASCII name in quotes
    (octal escapes inside `"..."`), which no PROTECTED prefix matches. --no-renames: a rename lists both
    sides, so moving a file out of `.github/` still shows the protected source (JAR-574)."""
    cwd, head = where(wt)
    out = git(cwd, "diff", "--name-only", "-z", "--no-renames", f"{base}...{head}").stdout
    return [x.replace("\\", "/") for x in out.split("\0") if x]


LINK_MODES = ("120000", "160000")       # a symlink and a gitlink (submodule entry)


def link_files(wt: Path, base: str) -> list[str]:
    """Paths the task added or changed as a symlink or a gitlink (#1381). Their path text can look harmless while a
    symlink points into `.github/` or `.claude/` and a gitlink pulls in another repository, so they count as protected."""
    cwd, head = where(wt)
    out = git(cwd, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", f"{base}...{head}").stdout.split("\0")
    return [out[i + 1].replace("\\", "/") for i in range(0, len(out) - 1, 2)
            if out[i].startswith(":") and out[i].split()[1:2] and out[i].split()[1] in LINK_MODES]


def diffstat(wt: Path, base: str) -> str:
    cwd, head = where(wt)
    out = git(cwd, "diff", "--shortstat", f"{base}...{head}", check=False).stdout.strip()
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
    Takes the repository's lock: another task's worktree remove/prune/branch -D must not run during the merge (JAR-189).
    A task folder is merged in the owner's repository instead (`_merge_task`, #1599): commit_all it first."""
    if task_ref(wt):
        return _merge_task(Path(wt), branch)
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


def _not_merged(p: subprocess.CompletedProcess) -> list[str]:
    return [f"(not merged: {' '.join((p.stderr or p.stdout or 'merge failed').split())[:200]})"]


def _merge_task(wt: Path, branch: str) -> list[str]:
    """merge_in for a task folder: `merge-tree` of the task branch and `branch` in the owner's repository (its config
    only), a merge commit there, then the folder's files move from the old tree to the new one through the private
    index. git never runs with the folder's own `.git`; its branch and index follow as text (`sync_clone`)."""
    try:
        f = _FolderIndex(wt)
    except GitError as e:
        return [f"(not merged: {e})"]
    root = Path(f.gitdir)
    with _repo_lock(root.parent if root.name == ".git" else root), f:
        old, other = f.head(), _rev(f.cwd, f"{branch}^{{commit}}")
        if not other:
            return [f"(not merged: no branch {branch})"]
        if git(f.cwd, "merge-base", "--is-ancestor", other, old, check=False).returncode == 0:
            return []                                                      # already in
        if git(f.cwd, "merge-base", "--is-ancestor", old, other, check=False).returncode == 0:
            new = other                                                    # fast-forward
        else:
            p = git(f.cwd, "merge-tree", "--write-tree", "--name-only", "--no-messages", "-z", old, other, check=False)
            parts = p.stdout.split("\0")
            if p.returncode == 1:
                return sorted({x for x in parts[1:] if x}) or _not_merged(p)
            if p.returncode != 0 or not parts[0].strip():
                return _not_merged(p)
            new = git(f.cwd, "commit-tree", parts[0].strip(), "-p", old, "-p", other, "-m",
                      f"Merge branch '{branch}' into {f.branch}").stdout.strip()
        f.run("read-tree", old)
        f.run("update-index", "-q", "--refresh", check=False)
        p = f.run("read-tree", "-m", "-u", old, new, check=False)          # the folder's files: old tree -> new tree
        if p.returncode != 0:
            return _not_merged(p)
        try:
            git(f.cwd, "update-ref", f"refs/heads/{f.branch}", new, old)
        except GitError as e:                                              # the branch moved: files back to `old`
            f.run("read-tree", "-m", "-u", new, old, check=False)
            return [f"(not merged: {e})"]
        f.sync_clone(new)
    return []


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
