"""#1722: a JARVES push never runs a command, or goes to a URL, that the project's shared (agent-writable) git config
names; and the project's GitHub status names the git 2.31 prerequisite. Each command test has a negative control: a
plain `git push origin` with the same config runs the marker command on this platform."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarves.projectmgr import forge, gitops

BRANCH = "jarves/p1/t1-a1"
URL = "https://github.com/o/r.git"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The owner's global config: allows ext:: (so only the guard under test stops it) and rewrites github.com to a
    local bare repository (hermetic, no network)."""
    h = tmp_path / "owner-home"
    h.mkdir()
    bare = tmp_path / "remote" / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    (h / ".gitconfig").write_text(f"[user]\n\tname = O\n\temail = o@example.invalid\n[protocol \"ext\"]\n\tallow = "
                                  f"always\n[url \"{bare.as_posix()}\"]\n\tinsteadOf = {URL}\n", encoding="utf-8")
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))
    return bare


def _setup(tmp_path: Path, origin: str = URL) -> tuple[Path, str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    gitops.git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    gitops.git(repo, "branch", BRANCH)
    gitops.git(repo, "config", "remote.origin.url", origin)
    marker, py = tmp_path / "ran.txt", tmp_path / "evil.py"
    py.write_text(f"open({str(marker)!r}, 'a').write('ran')\nraise SystemExit(1)\n", encoding="utf-8")
    return repo, f"{Path(sys.executable).as_posix()} {py.as_posix()}", marker


def _plain(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Plain git (no JARVES pins) for the negative controls, in the same allow-listed environment as JARVES's git."""
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=gitops.child_env())


def _on(bare: Path) -> bool:
    return BRANCH in subprocess.run(["git", "--git-dir", str(bare), "branch", "--list", BRANCH], capture_output=True,
                                    text=True).stdout


@pytest.mark.parametrize("key,value", [
    ("remote.origin.receivepack", "{cmd}"),                       # transport command
    ("remote.origin.pushurl", "ext::{cmd}"),                      # ext:: through the remote's push URL
    ("url.ext::{cmd} #.pushInsteadOf", URL)])                     # push URL rewrite to ext::
def test_shared_config_commands_never_run_on_a_push(tmp_path, home, key, value):
    repo, cmd, marker = _setup(tmp_path)
    gitops.git(repo, "config", key.format(cmd=cmd), value.format(cmd=cmd))
    neg = _plain(repo, "push", "origin", BRANCH)
    assert neg.returncode != 0 and marker.exists(), neg.stderr               # negative control
    marker.unlink()
    forge.push_branch(str(repo), BRANCH, "o/r")
    assert not marker.exists() and _on(home)
    sha = gitops.git(repo, "rev-parse", BRANCH).stdout.strip()
    assert gitops.git(repo, "rev-parse", f"refs/remotes/origin/{BRANCH}").stdout.strip() == sha


def test_shared_ssh_command_never_runs(tmp_path, home):
    """The owner's own core.sshCommand applies; the shared one never runs (shared config wins over global in git)."""
    repo, cmd, marker = _setup(tmp_path, "ssh://git@github.com/o/r.git")
    own = tmp_path / "own.txt"
    (tmp_path / "own.py").write_text(f"open({str(own)!r}, 'a').write('x')\nraise SystemExit(1)\n", encoding="utf-8")
    with open(Path.home() / ".gitconfig", "a", encoding="utf-8") as f:
        f.write(f"[core]\n\tsshCommand = {Path(sys.executable).as_posix()} {(tmp_path / 'own.py').as_posix()}\n")
    gitops.git(repo, "config", "core.sshCommand", cmd)
    neg = _plain(repo, "push", "origin", BRANCH)
    assert neg.returncode != 0 and marker.exists() and not own.exists(), neg.stderr  # negative control: shared wins
    marker.unlink()
    with pytest.raises(gitops.GitError):
        forge.push_branch(str(repo), BRANCH, "o/r")                               # the owner's (stub) ssh refused it
    assert own.exists() and not marker.exists()


@pytest.mark.parametrize("origin", ["ext::{cmd}", "file:///tmp/r.git", "https://github.com.evil.invalid/o/r.git",
                                    "https://github.com@evil.invalid/o/r", "-uhttps://github.com/o/r",
                                    "https://u:tok@github.com/o/r.git", "https://github.com/../x",
                                    "https://github.com/o/..", "https://github.com/-o/r", "git@github.com:o/.r"])
def test_a_non_github_origin_is_refused_before_git_runs(tmp_path, home, origin):
    repo, cmd, marker = _setup(tmp_path)
    gitops.git(repo, "config", "remote.origin.url", origin.format(cmd=cmd))
    with pytest.raises(forge.ForgeError, match="github.com"):
        forge.push_branch(str(repo), BRANCH, "o/r")
    assert not marker.exists() and not _on(home)


@pytest.mark.parametrize("where", ["dot-git-hooks", "shared-hookspath"])
def test_no_reference_transaction_hook_runs_on_the_tracking_ref_update(tmp_path, home, where):
    """Review R1 (round 1): update-ref in the project runs `reference-transaction` (git 2.28+) from .git/hooks or a
    shared core.hooksPath; gitops.git pins core.hooksPath to NO_HOOKS, so neither runs. The hook is `#!/bin/sh`
    (on Windows: Git for Windows' sh); the negative control fails loudly if it never runs there."""
    repo, cmd, marker = _setup(tmp_path)
    hooks = repo / ".git" / "hooks" if where == "dot-git-hooks" else tmp_path / "shared-hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "reference-transaction").write_bytes(f"#!/bin/sh\n{cmd}\n".encode())
    (hooks / "reference-transaction").chmod(0o755)
    if where == "shared-hookspath":
        gitops.git(repo, "config", "core.hooksPath", hooks.as_posix())
    _plain(repo, "update-ref", "refs/x", "HEAD")
    assert marker.exists()                                                   # negative control: plain git runs it
    marker.unlink()
    forge.push_branch(str(repo), BRANCH, "o/r")
    assert not marker.exists() and _on(home)
    sha = gitops.git(repo, "rev-parse", BRANCH).stdout.strip()
    assert gitops.git(repo, "rev-parse", f"refs/remotes/origin/{BRANCH}").stdout.strip() == sha


@pytest.mark.parametrize("url", [URL, "https://GitHub.com/o/r", "ssh://git@github.com/o/r.git",
                                 "ssh://git@github.com:22/o/r.git", "git@github.com:o/r.git", "git@GITHUB.COM:o/r"])
def test_accepted_github_origin_forms(url):
    """Review round 2 R5: the valid forms stay accepted (the regex alone)."""
    assert forge._PUSH_URL.match(url)


def test_a_shallow_clone_is_refused_with_a_clear_error(tmp_path, home):
    """Review round 2 R3: the temp repository has no `shallow` file, so git's own error would be unclear."""
    repo, _, _ = _setup(tmp_path)
    (repo / ".git" / "shallow").write_bytes((gitops.git(repo, "rev-parse", "HEAD").stdout.strip() + "\n").encode())
    with pytest.raises(forge.ForgeError, match="shallow clone"):
        forge.push_branch(str(repo), BRANCH, "o/r")
    assert not _on(home)


def test_a_failed_tracking_ref_update_does_not_fail_the_push(tmp_path, home, monkeypatch, caplog):
    """Review R2: the branch is already pushed, so any error of the local update-ref (a timeout too) is logged."""
    repo, _, _ = _setup(tmp_path)
    real = gitops.git

    def git(cwd, *a, **kw):
        if a[:1] == ("update-ref",):
            raise subprocess.TimeoutExpired("git", 1)
        return real(cwd, *a, **kw)
    monkeypatch.setattr(gitops, "git", git)
    forge.push_branch(str(repo), BRANCH, "o/r")
    assert _on(home) and "tracking ref" in caplog.text


def test_status_reports_a_git_older_than_2_31(monkeypatch, tmp_path):
    """#1722 R6: `jarvis project github` names the git 2.31 prerequisite instead of raising GitError later."""
    def old_git(cwd):   # round 3 R5: stub the public check, not gitops internals
        raise gitops.GitError("JARVES needs git 2.31 or later (found: 2.30)")
    monkeypatch.setattr(gitops, "require_git", old_git)
    st = forge.status(str(tmp_path), "o/r")
    assert st["git"] is False and "2.31 or later" in st["git_error"] and st["repo"] == "o/r"   # #1746 R4: kept


def test_a_failed_temp_cleanup_does_not_fail_the_push(tmp_path, home, monkeypatch, caplog):
    """Round 3 R2: on Windows git can still hold a file in the temporary repository; the push already succeeded."""
    repo, _, _ = _setup(tmp_path)
    seen, real = [], forge.shutil.rmtree

    def rmtree(p, *a, **kw):
        seen.append(p)
        raise PermissionError(13, "in use", p)
    monkeypatch.setattr(forge.shutil, "rmtree", rmtree)
    forge.push_branch(str(repo), BRANCH, "o/r")
    assert _on(home) and seen and "temporary push repository" in caplog.text
    sha = gitops.git(repo, "rev-parse", BRANCH).stdout.strip()
    assert gitops.git(repo, "rev-parse", f"refs/remotes/origin/{BRANCH}").stdout.strip() == sha
    for p in seen:
        real(p, ignore_errors=True)
