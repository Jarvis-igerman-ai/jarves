"""#1722: a JARVES push never runs a command the shared (agent-writable) repository config names: transport commands,
ext:: URLs, URL rewrites, ssh/askpass/credential/gpg programs, config-defined hooks. Plus the git 2.31 prerequisite in
the project's GitHub status."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import forge, gitops

BRANCH = "jarves/p1/t1-a1"


@pytest.fixture(autouse=True)
def _owner_home(tmp_path, monkeypatch):
    home = tmp_path / "owner-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Owner\n\temail = owner@example.invalid\n", encoding="utf-8")
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))
    return home


def _cfg(repo: Path, *kv: str, file: str = "") -> None:
    subprocess.run(["git", "config", *(["--file", file] if file else []), *kv], cwd=repo, check=True)


def _repo(tmp_path: Path, origin: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    gitops.git(repo, "branch", BRANCH)
    _cfg(repo, "remote.origin.url", origin)
    return repo


def _script(tmp_path: Path) -> tuple[Path, Path]:
    marker, sc = tmp_path / "ran.txt", tmp_path / "evil.sh"
    sc.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\nexit 1\n", encoding="utf-8")
    sc.chmod(0o755)
    return sc, marker


def test_an_ext_url_never_runs(tmp_path):
    sc, marker = _script(tmp_path)
    repo = _repo(tmp_path, f"ext::sh {sc.as_posix()}")
    _cfg(repo, "protocol.ext.allow", "always")
    with pytest.raises(forge.ForgeError, match="github.com origin"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def test_a_receivepack_on_a_local_origin_never_runs(tmp_path):
    sc, marker = _script(tmp_path)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = _repo(tmp_path, str(bare))
    _cfg(repo, "remote.origin.receivepack", sc.as_posix())
    with pytest.raises(forge.ForgeError, match="remote.origin.receivepack"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def test_a_shared_ssh_command_never_runs(tmp_path):
    sc, marker = _script(tmp_path)
    repo = _repo(tmp_path, "git@github.com:o/r.git")
    _cfg(repo, "core.sshCommand", sc.as_posix())
    with pytest.raises(forge.ForgeError, match="core.sshcommand"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


def test_a_shared_url_rewrite_to_ext_never_runs(tmp_path):
    sc, marker = _script(tmp_path)
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    _cfg(repo, "protocol.ext.allow", "always")
    _cfg(repo, f"url.ext::sh {sc.as_posix()} #.insteadOf", "https://github.com/")
    with pytest.raises(forge.ForgeError, match="insteadof"):
        forge.push_branch(str(repo), BRANCH)
    assert not marker.exists()


@pytest.mark.parametrize("key", ["core.askPass", "core.gitProxy", "core.alternateRefsCommand", "credential.helper",
                                 "credential.https://github.com.helper", "gpg.program", "gpg.ssh.program",
                                 "hook.x.command", "remote.origin.vcs", "remote.origin.uploadpack",
                                 "url.https://evil.invalid/.pushInsteadOf", "include"])
def test_other_command_keys_in_the_shared_config_refuse_the_push(tmp_path, key):
    """Refused before git push starts (no network). `include`: the key sits in an included file (scope: local)."""
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    if key == "include":
        inc = tmp_path / "inc.cfg"
        _cfg(repo, "core.askPass", "x", file=str(inc))
        _cfg(repo, "include.path", str(inc))
    else:
        _cfg(repo, key, "x")
    with pytest.raises(forge.ForgeError, match="will not push"):
        forge.push_branch(str(repo), BRANCH)


def test_a_clean_push_reaches_the_remote_unsigned(tmp_path, _owner_home):
    """The owner's global config stays usable (here a URL rewrite to a local bare repository); a shared
    push.gpgSign=true does not sign (a local receiver refuses signed pushes, so it would fail)."""
    bare = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "--bare", str(bare / "o" / "r.git")], check=True)
    _cfg(_owner_home, f"url.{bare.as_posix()}/.insteadOf", "https://github.com/", file=str(_owner_home / ".gitconfig"))
    repo = _repo(tmp_path, "https://github.com/o/r.git")
    _cfg(repo, "push.gpgSign", "true")
    forge.push_branch(str(repo), BRANCH)
    out = subprocess.run(["git", "--git-dir", str(bare / "o" / "r.git"), "branch", "--list", BRANCH],
                         capture_output=True, text=True)
    assert BRANCH in out.stdout


def test_status_reports_a_git_older_than_2_31(monkeypatch, tmp_path):
    """#1722 R6: the project's GitHub status names the git 2.31 prerequisite instead of raising GitError."""
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "git version 2.30.2\n", "")
    monkeypatch.setattr(gitops, "_GIT_OK", [])
    monkeypatch.setattr(gitops.subprocess, "run", run)
    st = forge.status(str(tmp_path))
    assert st["git"] is False and "2.31 or later" in st["git_error"] and st["repo"] == ""
