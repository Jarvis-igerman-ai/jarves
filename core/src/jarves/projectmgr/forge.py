"""GitHub, optional and per project: off (default, local git only) | read | write. Through the `gh` CLI with the
owner's own login (JARVES stores no token). Never force-pushes, never merges on GitHub, never deletes."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from . import gitops

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


def gh(path: str, *args: str, input_: str | None = None, timeout: float = 120) -> str:
    cmd = gh_cmd()
    if not cmd:
        raise ForgeError("the GitHub CLI (gh) is not installed: install it and run gh auth login")
    env = gitops.child_env({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST"):        # the owner's gh login, or a token they set for gh
        if os.environ.get(k):
            env[k] = os.environ[k]
    p = subprocess.run([*cmd, *args], cwd=path, capture_output=True, text=True, timeout=timeout, env=env,
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
            gh(path, "auth", "status")
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


# (#1722) the push URL: github.com over https or ssh only. No `<helper>::` (ext::, fd::), no local path, no option.
PUSH_URL_FORMS = "https://github.com/OWNER/REPO, ssh://git@github.com[:22]/OWNER/REPO or git@github.com:OWNER/REPO"
_PUSH_URL = re.compile(r"^(?:https://(?:[^@/\s:]+(?::[^@/\s]*)?@)?(?i:github\.com)/|ssh://git@(?i:github\.com)(?::22)?/"
                       r"|git@(?i:github\.com):)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?/?\Z")
# (#1722) keys of the shared (agent-writable) config that would make a push run a command, reroute its URL, or send
# the owner's credential elsewhere (proxy, TLS trust, resolve, extra headers)
# remote.<sub>.*: `git push <url>` first resolves the URL as a remote NAME, so any subsection other than a plain
# nick (a URL-shaped `remote.https://github.com/o/r.git.pushurl`) is refused; protocol.*: what a pushurl could reach
_PUSH_KEY = re.compile(r"^(?:url\..*\.(?:insteadof|pushinsteadof)|remote\..*\.(?:receivepack|uploadpack|vcs|proxy)"
                       r"|remote\.(?![\w-]+\.[^.]+\Z).*|protocol\.(?:.*\.)?allow"
                       r"|http\.(?:.*\.)?(?:proxy|sslverify|sslcainfo|sslcapath|curloptresolve|sslcert|sslkey"
                       r"|extraheader|cookiefile)"
                       r"|core\.(?:sshcommand|askpass|gitproxy|alternaterefscommand)|credential\.(?:.*\.)?helper"
                       r"|gpg\.(?:.*\.)?program|hook\..*\.command)$", re.S | re.I)


def _push_url(path: str) -> str:
    """The vetted origin URL to push to (#1722); ForgeError when the shared config could run a command on the push."""
    try:
        keys = gitops.shared_config_keys(path, r"^(url|remote|core|credential|gpg|hook|http|protocol)\.")
    except gitops.GitError as e:
        raise ForgeError(str(e)) from e
    bad = sorted({k for k in keys if _PUSH_KEY.match(k)})
    if bad:
        raise ForgeError(f"JARVES will not push: the repository's own git config sets {bad[0]!r}, which could run a "
                         "command or send your credential elsewhere (move it to your global config, or remove it with git config --local --unset)"[:300])
    url = gitops.git(path, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    if not _PUSH_URL.match(url):
        raise ForgeError(f"JARVES pushes only to a github.com origin: {PUSH_URL_FORMS}")
    return url


def push_branch(path: str, branch: str) -> None:
    """A normal push of one branch to origin (never --force, never a tag, never the default branch). Pushes to the
    vetted URL itself, so `remote.origin.*` (pushurl, receivepack, vcs) is never read; unsigned; never into a
    submodule, whose own config JARVES does not read (#1722). Afterwards refs/remotes/origin/<branch> is set to the
    pushed commit, as a push to `origin` would."""
    if not re.match(r"^jarves/p\d+/[\w.\-/]+$", branch) or ".." in branch or branch.startswith("/") or branch.endswith("/"):
        raise ForgeError("JARVES pushes only its own jarves/p<N>/... branches")
    url = _push_url(path)
    sha = gitops.git(path, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").stdout.strip()
    gitops.git(path, "push", "--no-verify", "--no-signed", "--recurse-submodules=no", url,
               f"{sha}:refs/heads/{branch}", timeout=300, ident=False)
    gitops.git(path, "update-ref", f"refs/remotes/origin/{branch}", sha)


def create_pr(path: str, head: str, base: str, title: str, body: str) -> str:
    out = gh(path, "pr", "create", "--head", head, "--base", base, "--title", title[:200], "--body", body[:6000])
    m = re.search(r"https://github\.com/\S+/pull/\d+", out)
    return m.group(0) if m else out.strip()[:200]


def pr_checks(path: str, ref: str) -> list[dict]:
    """CI of a PR (number, URL or branch): [{name, state, bucket}] (bucket: pass|fail|pending|skipping|cancel)."""
    try:
        out = gh(path, "pr", "checks", ref, "--json", "name,state,bucket")
    except ForgeError as e:
        if "no checks" in str(e).lower():
            return []
        raise
    return [{"name": r.get("name"), "state": r.get("state"), "bucket": r.get("bucket")} for r in json.loads(out or "[]")]
