"""GitHub, optional and per project: off (default, local git only) | read | write. Through the `gh` CLI with the
owner's own login (JARVES stores no token). Never force-pushes, never merges on GitHub, never deletes."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import gitops

_log = logging.getLogger(__name__)
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
MODES = ("off", "read", "write")
_SLUG = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_REPO = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class ForgeError(RuntimeError):
    pass


def gh_cmd() -> list[str]:
    p = shutil.which("gh") or shutil.which("gh.exe")
    return [p] if p else []


def repo_slug(path: str) -> str:
    """owner/repo of the project's `origin` remote on github.com, or ''. Read only when the owner sets the GitHub mode
    on the PC: stored as the project's `github_repo`, the only target of gh() and push_branch() (#1622)."""
    url = gitops.git(path, "remote", "get-url", "origin", check=False).stdout.strip()
    m = _SLUG.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def valid_repo(repo: str) -> bool:
    """owner/repo; no part is '..' or starts with '-' or '.' (never a gh option or a path)."""
    return bool(_REPO.match(repo or "")) and ".." not in repo


def _need(repo: str) -> str:
    if not valid_repo(repo):
        raise ForgeError("no GitHub repository is recorded for this project: run `jarvis project configure --github "
                         "read|write` on the PC again")
    return repo


def gh_env(box: str) -> dict[str, str]:
    """#1622: gh's own git reads (remotes for `pr create`...) find no repository (GIT_DIR is a missing path below the
    empty `box`) and read neither the user-global (~/.gitconfig, agent-writable through the shared HOME) nor the system
    git config. Not covered: gh's own config folder (GH_CONFIG_DIR, the owner's sign-in): #1742."""
    env = gitops.child_env({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "GIT_TERMINAL_PROMPT": "0",
                            "GIT_DIR": os.path.join(box, "no-repo"), "GIT_CEILING_DIRECTORIES": box,
                            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):        # the owner's gh login, or a token they set for gh
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def gh(repo: str, *args: str, input_: str | None = None, timeout: float = 120) -> str:
    """Run gh for `repo` (the recorded owner/repo; '' only for `auth status`, which names no repository). gh never runs
    inside the project (#1622): it runs in a fresh empty folder (gh_env) and its target is the explicit `--repo=`."""
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    box = tempfile.mkdtemp(prefix="jarves-gh-")
    try:
        p = subprocess.run([*cmd, *args, *([f"--repo={_need(repo)}"] if repo else [])], cwd=box, capture_output=True,
                           text=True, timeout=timeout, env=gh_env(box), creationflags=_NOWIN, input=input_,
                           encoding="utf-8", errors="replace")
    finally:   # best effort: on Windows gh may still hold the folder; that must not lose a created PR's URL
        shutil.rmtree(box, ignore_errors=True)
    if p.returncode != 0:
        raise ForgeError(" ".join((p.stderr or p.stdout or "gh failed").split())[:300])
    return p.stdout


def status(path: str, repo: str = "") -> dict:
    """Read-only facts for `jarvis project github`: git usable (2.31+, #1722), gh installed, signed in, the recorded
    repo (`repo`, #1622) and, shown only, today's origin (gh never follows it)."""
    try:
        gitops.require_git(path)
    except (gitops.GitError, OSError, subprocess.SubprocessError) as e:
        return {"git": False, "git_error": str(e)[:200], "gh": bool(gh_cmd()), "signed_in": False,
                "repo": repo if valid_repo(repo) else ""}
    out = {"git": True, "gh": bool(gh_cmd()), "signed_in": False, "repo": repo if valid_repo(repo) else "",
           "origin": repo_slug(path)}
    if out["gh"]:
        try:
            gh("", "auth", "status")
            out["signed_in"] = True
        except (ForgeError, OSError, subprocess.SubprocessError):
            pass
    return out


def issues(repo: str, label: str = "", limit: int = 20) -> list[dict]:
    args = ["issue", "list", "--state", "open", "--limit", str(max(1, min(50, limit))), "--json",
            "number,title,body,labels,url"]
    if label:
        args += ["--label", label]
    rows = json.loads(gh(_need(repo), *args) or "[]")
    return [{"number": r["number"], "title": r["title"], "body": (r.get("body") or "")[:1200], "url": r.get("url", ""),
             "labels": [x.get("name") for x in r.get("labels") or []]} for r in rows]


# (#1722) the push URL: github.com over https or ssh only. No `<helper>::` (ext::, fd::), no local path, no option,
# no user/token in an https URL (git's error text could carry it into logs; the owner's credential helper signs in)
PUSH_URL_FORMS = "https://github.com/OWNER/REPO, ssh://git@github.com[:22]/OWNER/REPO or git@github.com:OWNER/REPO"
_PUSH_URL = re.compile(r"^(?:https://(?i:github\.com)/|ssh://git@(?i:github\.com)(?::22)?/"
                       r"|git@(?i:github\.com):)[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*?(?:\.git)?/?\Z")


def push_branch(path: str, branch: str, repo: str) -> None:
    """A normal push of one branch to origin (never --force, never a tag, never the default branch).
    #1722: the project's own config is agent-writable (remote.<x>.receivepack/pushurl, url.*.insteadOf, ext::,
    core.sshCommand, credential helpers, gpg...). So git push runs in a fresh JARVES-made bare repository that borrows
    the project's objects (alternates): only the owner's system/global config applies, and nothing the project config
    adds later reaches the push. The one value read from it, origin's URL, must be a github.com https/ssh URL of the
    recorded `repo` (#1622: an agent could point origin at another github.com repository). Not covered: a rewrite
    (insteadOf/pushInsteadOf) in the user-global config, which agents can also write, still applies to the push: #1748.
    Afterwards refs/remotes/origin/<branch> is set locally, best effort (the branch is already pushed); no
    `reference-transaction` hook runs there, as gitops.git pins core.hooksPath to NO_HOOKS on the command line."""
    if not re.match(r"^jarves/p\d+/[\w.\-/]+$", branch) or ".." in branch or branch.startswith("/") or branch.endswith("/"):
        raise ForgeError("JARVES pushes only its own jarves/p<N>/... branches")
    url = gitops.git(path, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    if not _PUSH_URL.match(url):
        raise ForgeError(f"JARVES pushes only to a github.com origin: {PUSH_URL_FORMS}")
    m = _SLUG.search(url.lower())
    if not m or f"{m.group(1)}/{m.group(2)}" != _need(repo).lower():
        raise ForgeError(f"origin no longer points at the recorded repository {repo}: JARVES does not push there")
    if gitops.git(path, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":   # alternates lack it
        raise ForgeError("JARVES cannot push from a shallow clone: run git fetch --unshallow in the project first")
    sha = gitops.git(path, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").stdout.strip()
    objects = gitops.git(path, "rev-parse", "--path-format=absolute", "--git-path", "objects").stdout.strip()
    tmp = tempfile.mkdtemp(prefix="jarves-push-")
    try:
        gitops.git(tmp, "init", "-q", "--bare")
        # bytes: a text-mode "\r\n" on Windows would make git read the path as "objects\r" (CI, round 1)
        (Path(tmp) / "objects" / "info" / "alternates").write_bytes((objects + "\n").encode("utf-8"))
        gitops.git(tmp, "push", "--no-verify", "--recurse-submodules=no", url, f"{sha}:refs/heads/{branch}",
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


def create_pr(repo: str, head: str, base: str, title: str, body: str) -> str:
    out = gh(_need(repo), "pr", "create", "--head", head, "--base", base, "--title", title[:200], "--body", body[:6000])
    m = re.search(r"https://github\.com/\S+/pull/\d+", out)
    return m.group(0) if m else out.strip()[:200]


def pr_checks(repo: str, ref: str) -> list[dict]:
    """CI of a PR (number, URL or branch): [{name, state, bucket}] (bucket: pass|fail|pending|skipping|cancel)."""
    try:
        out = gh(_need(repo), "pr", "checks", ref, "--json", "name,state,bucket")
    except ForgeError as e:
        if "no checks" in str(e).lower():
            return []
        raise
    return [{"name": r.get("name"), "state": r.get("state"), "bucket": r.get("bucket")} for r in json.loads(out or "[]")]
