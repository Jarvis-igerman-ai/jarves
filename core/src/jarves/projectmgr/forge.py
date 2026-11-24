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
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ForgeError(RuntimeError):
    pass


def gh_cmd() -> list[str]:
    p = shutil.which("gh") or shutil.which("gh.exe")
    return [p] if p else []


def repo_slug(path: str) -> str:
    """owner/repo of the project's `origin` remote on github.com, or ''. Read through gitops.git() only when the owner
    sets the GitHub mode on the PC; the result is stored as the project's `github_repo`, the only target gh() uses."""
    url = gitops.git(path, "remote", "get-url", "origin", check=False).stdout.strip()
    m = _SLUG.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def valid_repo(repo: str) -> bool:
    """owner/repo; neither part may be '.', '..' or start with '-' (never read as a gh option)."""
    return bool(_REPO.match(repo or "")) and not any(x in (".", "..") or x.startswith("-") for x in repo.split("/"))


def gh(repo: str, *args: str, input_: str | None = None, timeout: float = 120) -> str:
    """Run gh for `repo` (owner/repo, JARVES's stored record; '' only for commands that name no repository, such as
    `auth status`). gh never runs inside the project (#1622): its own git reads there (remotes, `url.*.insteadOf`,
    `core.fsmonitor`...) would bypass gitops.git(), and the shared config is agent-writable. It runs in a fresh empty
    folder with GIT_DIR pointing at a missing path, so any git command gh starts finds no repository and reads no
    repository config, nor the user-global or system git config; the target is always the explicit `--repo=`
    (`pr create` runs `git remote -v`, which fails here and gh carries on). Not covered: gh's own config folder
    (GH_CONFIG_DIR, hosts.yml/config.yml) is still read as the owner's sign-in: #1742."""
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    if repo and not valid_repo(repo):
        raise ForgeError("not an owner/repo name")
    box = tempfile.mkdtemp(prefix="jarves-gh-")
    try:
        env = gitops.child_env({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
                                "GIT_DIR": os.path.join(box, "no-repo"), "GIT_CEILING_DIRECTORIES": box,
                                "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                                "GIT_CONFIG_GLOBAL": os.devnull})     # agents share the user's HOME (~/.gitconfig)
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):        # the owner's gh login, or a token they set for gh
            if os.environ.get(k):
                env[k] = os.environ[k]
        p = subprocess.run([*cmd, *args, *([f"--repo={repo}"] if repo else [])], cwd=box, capture_output=True,
                           text=True, timeout=timeout, env=env, creationflags=_NOWIN, input=input_,
                           encoding="utf-8", errors="replace")
    finally:   # best effort: on Windows gh (or a git it started) may still hold the folder; a leftover empty temp
        shutil.rmtree(box, ignore_errors=True)   # folder must not turn a done `pr create` into an error (no lost URL)
    if p.returncode != 0:
        raise ForgeError(" ".join((p.stderr or p.stdout or "gh failed").split())[:300])
    return p.stdout


def _need(repo: str) -> str:
    if not valid_repo(repo):
        raise ForgeError("no GitHub repository is recorded for this project: run `jarvis project configure --github "
                         "read|write` on the PC again")
    return repo


def status(repo: str) -> dict:
    """Read-only facts for `jarvis project github`: gh installed, signed in, the recorded repo."""
    out = {"gh": bool(gh_cmd()), "signed_in": False, "repo": repo if valid_repo(repo) else ""}
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


def push_branch(path: str, branch: str) -> None:
    """A normal push of one branch to origin (never --force, never a tag, never the default branch)."""
    if not re.match(r"^jarves/p\d+/[\w.\-/]+$", branch) or ".." in branch or branch.startswith("/") or branch.endswith("/"):
        raise ForgeError("JARVES pushes only its own jarves/p<N>/... branches")
    gitops.git(path, "push", "--no-verify", "origin", f"{branch}:{branch}", timeout=300, ident=False)


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
