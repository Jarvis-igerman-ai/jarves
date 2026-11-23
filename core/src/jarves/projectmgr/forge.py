"""GitHub, optional and per project: off (default, local git only) | read | write. Through the `gh` CLI with the
owner's own login (JARVES stores no token). Never force-pushes, never merges on GitHub, never deletes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from . import gitops

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
MODES = ("off", "read", "write")
_SLUG = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_OWNER_REPO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*")   # fullmatch: no trailing newline
_OWN_BRANCH = re.compile(r"jarves/p\d+/[\w.\-/]+")                     # fullmatch: the only branches JARVES pushes
_PR_REF = re.compile(r"[1-9][0-9]{0,8}")
_GH_GROUPS = ("auth", "issue", "pr")    # the only gh command groups JARVES runs; a stale gh(path, ...) call is refused


class ForgeError(RuntimeError):
    pass


def gh_cmd() -> list[str]:
    p = shutil.which("gh") or shutil.which("gh.exe")
    return [p] if p else []


def repo_slug(path: str) -> str:
    """owner/repo of the project's `origin` remote on github.com, or ''."""
    url = gitops.git(path, "remote", "get-url", "origin", check=False).stdout.strip()
    m = _SLUG.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def checked_slug(repo: object) -> str:
    if not isinstance(repo, str) or not _OWNER_REPO.fullmatch(repo) or ".." in repo:
        raise ForgeError("a GitHub call needs an explicit owner/repo")
    return repo


def gh(*args: str, repo: str | None = None, input_: str | None = None, timeout: float = 120) -> str:
    """Runs `gh` so that its own git reads never see the project's agent-writable `.git/config` (#1622: a planted
    `remote.*.url`, `url.*.insteadOf`, `core.fsmonitor`): GIT_DIR is pinned to gitops.NO_HOOKS, a path below a file
    that can never be a repository, whatever the cwd holds, and the cwd is the temp area, not the project. A
    repository call passes `repo` (a checked owner/repo from JARVES's record) as an explicit `--repo`; only
    account-level calls such as `auth status` leave it None."""
    if not args or args[0] not in _GH_GROUPS:
        raise ForgeError("unsupported gh command")
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    argv = [*cmd, *args, *(["--repo", checked_slug(repo)] if repo is not None else [])]
    env = gitops.child_env({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "GIT_DIR": gitops.NO_HOOKS})
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):        # the owner's gh login, or a token they set for gh
        if os.environ.get(k):
            env[k] = os.environ[k]
    p = subprocess.run(argv, cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=timeout, env=env,
                       creationflags=_NOWIN, input=input_, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise ForgeError(" ".join((p.stderr or p.stdout or "gh failed").split())[:300])
    return p.stdout


def status(repo: str) -> dict:
    """Read-only facts for `jarvis project github`: gh installed, signed in, and the owner/repo JARVES recorded for
    the project (#1622: never re-read from the project's agent-writable git config)."""
    out = {"gh": bool(gh_cmd()), "signed_in": False, "repo": repo}
    if out["gh"]:
        try:
            gh("auth", "status")
            out["signed_in"] = True
        except (ForgeError, OSError, subprocess.SubprocessError):
            pass
    return out


def issues(repo: str, label: str = "", limit: int = 20) -> list[dict]:
    args = ["issue", "list", "--state", "open", "--limit", str(max(1, min(50, limit))), "--json",
            "number,title,body,labels,url"]
    if label:
        args += ["--label", label]
    rows = json.loads(gh(*args, repo=repo) or "[]")
    return [{"number": r["number"], "title": r["title"], "body": (r.get("body") or "")[:1200], "url": r.get("url", ""),
             "labels": [x.get("name") for x in r.get("labels") or []]} for r in rows]


def _own_branch(b: object) -> bool:
    return isinstance(b, str) and bool(_OWN_BRANCH.fullmatch(b)) and ".." not in b and not b.endswith("/")


_PUSH_RISKY = ("credential.", "http.", "url.", "core.askpass", "core.sshcommand", "core.gitproxy")
_BASE = re.compile(r"[A-Za-z0-9_][\w.\-/]{0,199}")


def _push_risk(path: str) -> str:
    """The first key in the project's own (local or worktree, includes followed) git config that could redirect an
    explicit-URL push or see the owner's token (#1622 review): a remote other than origin (git uses a remote whose
    name equals the URL argument, with its pushurl), credential.*, http.*, url.* (insteadOf), core.askPass,
    core.sshCommand, core.gitProxy. '' when there is none."""
    out = gitops.git(path, "config", "--show-scope", "--includes", "--list", "-z", check=False).stdout
    parts = out.split("\0")                       # -z: scope NUL key LF value NUL ...
    for scope, kv in zip(parts[0::2], parts[1::2]):
        if scope not in ("local", "worktree"):
            continue
        key = kv.split("\n", 1)[0].lower()
        if key.startswith(_PUSH_RISKY) or (key.startswith("remote.") and key.rsplit(".", 1)[0] != "remote.origin"):
            return key
    return ""


def push_branch(path: str, branch: str, repo: str) -> None:
    """A normal push of one branch (never --force, never a tag, never the default branch) to the owner's recorded
    owner/repo as an explicit URL, never to the `origin` remote (#1622: `remote.origin.url` is agent-writable). The
    project's own git config can still redirect that URL or capture the owner's token, so the push is refused while
    it holds any key _push_risk names."""
    if not _own_branch(branch):
        raise ForgeError("JARVES pushes only its own jarves/p<N>/... branches")
    url = f"https://github.com/{checked_slug(repo)}.git"
    risky = _push_risk(path)
    if risky:
        raise ForgeError(f"the project's git config sets {risky[:80]}, which could redirect the push or see the "
                         "GitHub login: JARVES does not push from it")
    gitops.git(path, "push", "--no-verify", url, f"{branch}:{branch}", timeout=300, ident=False)


def create_pr(repo: str, head: str, base: str, title: str, body: str) -> str:
    """repo: an explicit owner/repo from the caller; gh never resolves the target from the project's config (#1622).
    head is one of JARVES's own branches (never owner:branch); base is a plain branch name."""
    if not _own_branch(head):
        raise ForgeError("a pull request comes only from a jarves/p<N>/... branch")
    if not isinstance(base, str) or not _BASE.fullmatch(base) or ".." in base or base.endswith(("/", ".lock")):
        raise ForgeError("the pull request base is a plain branch name")
    out = gh("pr", "create", "--head", head, "--base", base, "--title", title[:200], "--body", body[:6000], repo=repo)
    m = re.search(r"https://github\.com/\S+/pull/\d+", out)
    return m.group(0) if m else out.strip()[:200]


def pr_checks(repo: str, ref: str) -> list[dict]:
    """CI of a PR (number or one of JARVES's own jarves/p<N>/... branches): [{name, state, bucket}] (bucket:
    pass|fail|pending|skipping|cancel). A PR URL is refused: gh resolves it against the repository in the URL, not
    the recorded --repo (#1622)."""
    if not ((isinstance(ref, str) and _PR_REF.fullmatch(ref)) or _own_branch(ref)):
        raise ForgeError("a pull request number or a jarves/p<N>/... branch is needed")
    try:
        out = gh("pr", "checks", ref, "--json", "name,state,bucket", repo=repo)
    except ForgeError as e:
        if "no checks" in str(e).lower():
            return []
        raise
    return [{"name": r.get("name"), "state": r.get("state"), "bucket": r.get("bucket")} for r in json.loads(out or "[]")]
