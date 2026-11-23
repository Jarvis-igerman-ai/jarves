"""#1390: the shared repository config (`.git/config`, writable by every agent through the shared `.git` folder) can name
commands git runs: filter and merge drivers, diff commands, editors, ssh, gpg. JARVES's own git calls refuse to run while
the repository's own config (or a file it includes) names one; the owner's global config still applies.
No network; throw-away local repositories with an empty global config."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import gitops


@pytest.fixture(autouse=True)
def _empty_global(monkeypatch, tmp_path):
    """The host's own ~/.gitconfig must not shape the result."""
    (tmp_path / "userhome").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "userhome" / ".config"))


def _cmd(marker: Path) -> str:
    """A shell command that leaves a trace and passes its input through (a working filter or driver)."""
    return f"touch '{marker.as_posix()}'; cat"


def _setcfg(repo: Path, key: str, value: str) -> None:
    subprocess.run(["git", "config", key, value], cwd=repo, check=True)          # what an agent writes, outside JARVES


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    gitops.git(repo, "config", "user.name", "Owner")                          # ident=False: the owner's identity
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    (repo / ".gitattributes").write_text("*.txt filter=evil merge=evil diff=evil\n", encoding="utf-8")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    return repo


def test_a_filter_named_in_the_shared_config_never_runs(tmp_path):
    """#1390 filters: smudge runs on checkout (worktree add), clean on add (commit_all)."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "ran.txt"
    _setcfg(repo, "filter.evil.smudge", _cmd(marker))
    _setcfg(repo, "filter.evil.clean", _cmd(marker))
    with pytest.raises(gitops.GitError, match="filter.evil"):
        gitops.ensure_integration(home, str(repo), 1)
    (repo / "f.txt").write_text("y\n", encoding="utf-8")
    with pytest.raises(gitops.GitError):
        gitops.commit_all(repo, "change")
    assert not marker.exists()


def test_a_merge_driver_named_in_the_shared_config_never_runs(tmp_path):
    """#1390 merge drivers: a real three-way merge of f.txt (the ship path uses merge_in with the owner's identity)."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "f.txt").write_text("side\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "main", "--no-verify")
    _setcfg(repo, "merge.evil.driver", f"touch '{marker.as_posix()}'; true")
    subprocess.run(["git", "merge", "-q", "--no-edit", "side"], cwd=repo, capture_output=True, check=True)
    assert marker.exists()                                                       # the setup itself runs the driver
    marker.unlink()
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD~1"], cwd=repo, check=True)
    with pytest.raises(gitops.GitError, match="merge.evil.driver"):
        gitops.merge_in(repo, "side", ident=False)
    assert not marker.exists()


@pytest.mark.parametrize("key", ["diff.external", "diff.evil.command", "diff.evil.textconv", "core.sshCommand",
                                 "core.editor", "sequence.editor", "core.pager", "core.askpass", "gpg.program",
                                 "gpg.ssh.program", "credential.helper", "hook.evil.command",
                                 "trailer.evil.cmd", "mergetool.evil.cmd", "remote.origin.uploadpack",
                                 "gpg.ssh.defaultKeyCommand", "submodule.evil.update", "core.alternateRefsCommand",
                                 "filter.evil.process", "core.gitProxy", "remote.origin.receivepack",
                                 "remote.origin.vcs", "difftool.evil.path", "credential.https://x.invalid.helper",
                                 "gpg.x509.program"])
def test_other_command_keys_in_the_shared_config_are_refused(tmp_path, key):
    """#1390 other command types: every git call refuses while the shared config names one."""
    repo = _repo(tmp_path)
    _setcfg(repo, key, "evil")
    with pytest.raises(gitops.GitError, match="repository config names a command"):
        gitops.git(repo, "status")


def test_an_included_file_and_conditional_includes_are_checked(tmp_path):
    """#1390 include.path / includeIf: an included file's keys count as the repository's own; a conditional include
    may match only inside a new worktree, so it is refused outright."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (repo / ".git" / "more").write_text(f'[filter "evil"]\n\tsmudge = {_cmd(marker)}\n', encoding="utf-8")
    _setcfg(repo, "include.path", "more")
    with pytest.raises(gitops.GitError, match="filter.evil.smudge"):
        gitops.git(repo, "status")
    subprocess.run(["git", "config", "--unset", "include.path"], cwd=repo, check=True)
    _setcfg(repo, "includeIf.onbranch:jarves/**.path", "more")
    with pytest.raises(gitops.GitError, match="includeif"):
        gitops.git(repo, "status")
    assert not marker.exists()


def test_the_owners_global_config_and_neutralised_keys_still_work(tmp_path):
    """The owner's own global filter (git-lfs style) is not refused, nor are keys every call already overrides
    (core.hooksPath, core.fsmonitor)."""
    repo = _repo(tmp_path)
    (tmp_path / "userhome" / ".gitconfig").write_text('[filter "evil"]\n\tclean = cat\n\tsmudge = cat\n',
                                                      encoding="utf-8")
    _setcfg(repo, "core.hooksPath", str(tmp_path / "hooks"))
    _setcfg(repo, "core.fsmonitor", "false")
    scope = subprocess.run(["git", "config", "--show-scope", "--get", "filter.evil.clean"], cwd=repo, text=True,
                           capture_output=True, env=gitops.child_env(), check=True).stdout.split()
    assert scope == ["global", "cat"]                        # JARVES's own env really loads the planted global file
    assert gitops.git(repo, "status").returncode == 0
    assert gitops.is_repo(str(repo)) and gitops.repo_state(str(repo))["branch"] == "main"


def test_a_repository_that_names_a_command_is_not_a_usable_repo(tmp_path):
    """is_repo answers False (instead of crashing) when the shared config names a command."""
    repo = _repo(tmp_path)
    _setcfg(repo, "core.editor", "evil")
    assert gitops.is_repo(str(repo)) is False


def test_the_ext_transport_never_runs_a_command(tmp_path):
    """#1390 review R2: `protocol.ext.allow=always` plus an `ext::` remote runs a command on any transport call."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    _setcfg(repo, "protocol.ext.allow", "always")
    _setcfg(repo, "remote.evil.url", f"ext::sh -c touch% {marker.as_posix()}")
    subprocess.run(["git", "ls-remote", "evil"], cwd=repo, capture_output=True)
    assert marker.exists()                                                       # the setup itself runs the command
    marker.unlink()
    assert gitops.git(repo, "ls-remote", "evil", check=False).returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize("args", [("-C", "elsewhere", "status"), ("--git-dir=x", "status"), ("--work-tree", "x", "status")])
def test_a_call_that_points_git_at_another_folder_is_refused(tmp_path, args):
    """#1390 review R6: the config check reads `cwd`, so git must not be pointed at another repository."""
    with pytest.raises(gitops.GitError, match="another folder"):
        gitops.git(_repo(tmp_path), *args)
