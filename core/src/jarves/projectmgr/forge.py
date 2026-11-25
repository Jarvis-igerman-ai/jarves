"""GitHub, optional and per project: off (default, local git only) | read | write. Through the `gh` CLI with the
owner's own login (JARVES stores no token). Never force-pushes, never merges on GitHub, never deletes."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import gitops

_log = logging.getLogger(__name__)
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
MODES = ("off", "read", "write")
_SLUG = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


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


_PART = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")


def _repo_arg(path: str) -> str:
    """#1622: gh is told the repository (read through gitops.git, never by gh's own git reads), as the LAST option."""
    slug = repo_slug(path)
    if not all(_PART.match(x) for x in slug.split("/")) or slug.count("/") != 1:
        raise ForgeError("this project's origin is not a github.com repository")
    return f"--repo={slug}"


def gh(path: str, *args: str, input_: str | None = None, timeout: float = 120, repo: bool = True) -> str:
    """Runs gh for the project at `path`. #1622: gh resolves remotes with its own git reads, which bypass gitops.git()
    and would read the project's agent-writable config (core.fsmonitor, url.*.insteadOf, remote.*.url). So gh runs
    in a fresh empty folder outside the project with GIT_DIR pinned to a missing path (no repository is found there),
    and repo=True appends --repo=<owner/repo> taken from origin through gitops.git(). repo=False: `auth status`."""
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    tail = [_repo_arg(path)] if repo else []
    with tempfile.TemporaryDirectory(prefix="jarves-gh-", ignore_cleanup_errors=True) as box:
        env = gitops.child_env({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
                                "GIT_DIR": os.path.join(box, "no-repository")})
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):        # the owner's gh login, or a token they set for gh
            if os.environ.get(k):
                env[k] = os.environ[k]
        p = subprocess.run([*cmd, *args, *tail], cwd=box, capture_output=True, text=True, timeout=timeout, env=env,
                           creationflags=_NOWIN, input=input_, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise ForgeError(" ".join((p.stderr or p.stdout or "gh failed").split())[:300])
    return p.stdout


def status(path: str) -> dict:
    """Read-only facts for `jarvis project github`: git usable (2.31+, #1722), gh installed, signed in, the slug."""
    try:
        gitops.require_git(path)
    except (gitops.GitError, OSError, subprocess.SubprocessError) as e:
        return {"git": False, "git_error": str(e)[:200], "gh": bool(gh_cmd()), "signed_in": False, "repo": ""}
    out = {"git": True, "gh": bool(gh_cmd()), "signed_in": False, "repo": repo_slug(path)}
    if out["gh"]:
        try:
            gh(path, "auth", "status", repo=False)
            out["signed_in"] = True
        except (ForgeError, OSError, subprocess.SubprocessError):
            pass
    return out


def issues(path: str, label: str = "", limit: int = 20) -> list[dict]:
    args = ["issue", "list", "--state", "open", "--limit", str(max(1, min(50, limit))), "--json",
            "number,title,body,labels,url"]
    if label:
        args += ["--label", label]
    rows = json.loads(gh(path, *args) or "[]")
    return [{"number": r["number"], "title": r["title"], "body": (r.get("body") or "")[:1200], "url": r.get("url", ""),
             "labels": [x.get("name") for x in r.get("labels") or []]} for r in rows]


# (#1722) the push URL: github.com over https or ssh only. No `<helper>::` (ext::, fd::), no local path, no option,
# no user/token in an https URL (git's error text could carry it into logs; the owner's credential helper signs in)
PUSH_URL_FORMS = "https://github.com/OWNER/REPO, ssh://git@github.com[:22]/OWNER/REPO or git@github.com:OWNER/REPO"
_PUSH_URL = re.compile(r"^(?:https://(?i:github\.com)/|ssh://git@(?i:github\.com)(?::22)?/"
                       r"|git@(?i:github\.com):)[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*?(?:\.git)?/?\Z")


def _check_push_target(url: str, got: list[str]) -> None:
    """#1748: the URL(s) git will really push to after the owner's global config (insteadOf, pushInsteadOf, an
    agent-writable ~/.gitconfig) must be exactly one github.com URL of the same repository as the checked origin."""
    def slug(u: str) -> re.Match | None:          # review R3: _SLUG would read "ssh://...github.com:22/o/r" as 22/o
        return _SLUG.search(re.sub(r"(?i)^ssh://git@github\.com:22/", "ssh://git@github.com/", u))
    want = slug(url)
    m = slug(got[0]) if len(got) == 1 and _PUSH_URL.match(got[0]) else None
    if not (m and want and [x.lower() for x in m.group(1, 2)] == [x.lower() for x in want.group(1, 2)]):
        shown = ", ".join(_no_userinfo(g) for g in got)[:200] or "none"
        raise ForgeError("JARVES will not push: your global git config rewrites the origin URL to another target or "
                         f"an unsupported URL form ({shown}; allowed: {PUSH_URL_FORMS} of the same repository). Remove "
                         "that url.*.insteadOf/pushInsteadOf rule; a user or token in a rewritten URL is refused too: "
                         "sign in with a credential helper (gh auth setup-git) or use ssh")


def _no_userinfo(u: str) -> str:
    """Review R1: a token in a rewritten URL (https://user:tok@github.com/) never reaches the error text or logs."""
    return re.sub(r"^[^/@:]*@", "***@", re.sub(r"(?<=//)[^/]*@", "***@", u))


def push_branch(path: str, branch: str) -> None:
    """A normal push of one branch to origin (never --force, never a tag, never the default branch).
    #1722: the project's own config is agent-writable (remote.<x>.receivepack/pushurl, url.*.insteadOf, ext::,
    core.sshCommand, credential helpers, gpg...). So git push runs in a fresh JARVES-made bare repository that borrows
    the project's objects (alternates): only the owner's system/global config applies, and nothing the project config
    adds later reaches the push. The one value read from it, origin's URL, must be a github.com https/ssh URL.
    Afterwards refs/remotes/origin/<branch> is set locally, best effort (the branch is already pushed); no
    `reference-transaction` hook runs there, as gitops.git pins core.hooksPath to NO_HOOKS on the command line."""
    if not re.match(r"^jarves/p\d+/[\w.\-/]+$", branch) or ".." in branch or branch.startswith("/") or branch.endswith("/"):
        raise ForgeError("JARVES pushes only its own jarves/p<N>/... branches")
    url = gitops.git(path, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    if not _PUSH_URL.match(url):
        raise ForgeError(f"JARVES pushes only to a github.com origin: {PUSH_URL_FORMS}")
    if gitops.git(path, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":   # alternates lack it
        raise ForgeError("JARVES cannot push from a shallow clone: run git fetch --unshallow in the project first")
    sha = gitops.git(path, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").stdout.strip()
    objects = gitops.git(path, "rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.strip()
    tmp = tempfile.mkdtemp(prefix="jarves-push-")
    try:
        gitops.git(tmp, "init", "-q", "--bare")
        # bytes: a text-mode "\r\n" on Windows would make git read the path as "objects\r" (CI, round 1)
        (Path(tmp) / "objects" / "info" / "alternates").write_bytes((objects + "\n").encode("utf-8"))
        # #1748: a remote of an unguessable name (a global remote."<url>".pushurl would redirect a push to the bare
        # URL), resolved as git will push it, then checked again. Not covered (the global config is agent-writable;
        # a JARVES-owned GIT_CONFIG_GLOBAL is a separate design decision): a global rule added between the two,
        # core.sshCommand, credential helpers, http.proxy / http.<url>.proxy and http.sslVerify=false.
        name = f"jarves-{secrets.token_hex(8)}"
        gitops.git(tmp, "config", f"remote.{name}.url", url)
        _check_push_target(url, gitops.git(tmp, "remote", "get-url", "--push", "--all", name).stdout.splitlines())
        gitops.git(tmp, "push", "--no-verify", "--recurse-submodules=no", name, f"{sha}:refs/heads/{branch}",
                   timeout=300, ident=False)
    finally:
        try:   # a file git still holds open on Windows must not turn a finished push into a failure (round 3 R2)
            shutil.rmtree(tmp)
        except OSError as e:
            _log.warning("could not remove the temporary push repository %s: %s", tmp, e)
    try:
        gitops.git(path, "update-ref", f"refs/remotes/origin/{branch}", sha)
    except (gitops.GitError, OSError, subprocess.SubprocessError) as e:
        _log.warning("pushed %s, but the local tracking ref origin/%s was not updated: %s", branch, branch, e)


def create_pr(path: str, head: str, base: str, title: str, body: str) -> str:
    out = gh(path, "pr", "create", "--head", head, "--base", base, "--title", title[:200], "--body", body[:6000])
    m = re.search(r"https://github\.com/\S+/pull/\d+", out)
    return m.group(0) if m else out.strip()[:200]


def pr_checks(path: str, ref: str) -> list[dict]:
    """CI of a PR (number, URL or branch): [{name, state, bucket}] (bucket: pass|fail|pending|skipping|cancel)."""
    # #1622: a URL of another repository would override --repo, an option-like ref would add options
    m = re.match(r"^https://github\.com/([^/]+/[^/]+)/pull/(\d+)/?\Z", ref, re.A)
    if m and f"--repo={m.group(1)}".lower() == _repo_arg(path).lower():
        ref = m.group(2)
    if not (re.match(r"^\d+\Z", ref, re.A) or re.match(r"^jarves/p\d+/[\w.\-/]+\Z", ref, re.A)):
        raise ForgeError("JARVES checks only a PR number, its URL or a jarves/p<N>/... branch of this project")
    try:
        out = gh(path, "pr", "checks", ref, "--json", "name,state,bucket")
    except ForgeError as e:
        if "no checks" in str(e).lower():
            return []
        raise
    return [{"name": r.get("name"), "state": r.get("state"), "bucket": r.get("bucket")} for r in json.loads(out or "[]")]
