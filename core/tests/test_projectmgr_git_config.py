"""#1390: commands the shared repository config names (filters, merge drivers, diff drivers) never run from JARVES's
own git calls. An agent can write `.git/config`, an included file and `.git/info/attributes` (no commit needed)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import gitops


@pytest.fixture(autouse=True)
def _owner_home(tmp_path, monkeypatch):
    """An empty global config of our own, so the host's ~/.gitconfig cannot shape the result."""
    home = tmp_path / "owner-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Owner\n\temail = owner@example.invalid\n", encoding="utf-8")
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _sh(marker: Path, tail: str = "cat") -> str:
    return f"echo ran >> '{marker.as_posix()}'; {tail}"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    return repo


def _cfg(repo: Path, *kv: str, file: str = "") -> None:
    subprocess.run(["git", "config", *(["--file", file] if file else []), *kv], cwd=repo, check=True)


def _attrs(repo: Path, text: str) -> None:
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text(text, encoding="utf-8")


def test_filters_named_in_the_shared_config_never_run(tmp_path):
    """#1390: `filter.<x>.smudge/clean/process` run on worktree add, add -A and merges; required=true would also make
    a blanked filter fail, so JARVES sets it to false."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "ran.txt"
    for k in ("smudge", "clean"):
        _cfg(repo, f"filter.evil.{k}", _sh(marker))
    _cfg(repo, "filter.evil.required", "true")
    _attrs(repo, "* filter=evil\n")
    (repo / "f.txt").unlink()
    subprocess.run(["git", "checkout", "--", "f.txt"], cwd=repo, check=True)     # the setup itself works
    assert marker.exists()
    marker.unlink()
    gitops.ensure_integration(home, str(repo), 1)
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)                     # checkout: smudge
    assert (wt / "f.txt").read_text(encoding="utf-8") == "base\n"
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a")                                              # add: clean
    assert gitops.merge_in(repo, gitops.task_branch(1, 1, 1)) == [] and (repo / "a.py").exists()
    assert not marker.exists()


def test_merge_drivers_named_in_an_included_shared_config_never_run(tmp_path):
    """#1390: `merge.<x>.driver` runs on a merge; here it hides in an `include.path` file. A blanked driver makes the
    merge conflict, so merge_in reports the file and aborts (fails closed)."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "f.txt").write_text("side\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "main", "--no-verify")
    _cfg(repo, "merge.evil.driver", _sh(marker, "exit 0"), file=str(repo / ".git" / "evil.inc"))
    _cfg(repo, "include.path", "evil.inc")
    _attrs(repo, "* merge=evil\n")
    assert gitops.merge_in(repo, "side", ident=False) == ["f.txt"]
    assert not marker.exists()
    assert gitops.git(repo, "status", "--porcelain").stdout == ""                 # aborted: nothing changed


def test_diff_drivers_and_external_diff_named_in_the_shared_config_never_run(tmp_path):
    """#1390: `diff.<x>.textconv`, `diff.<x>.command` and `diff.external` run on a plain `git diff` (the reviewer's
    full diff in the engine); JARVES's diff shows the plain text."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    (repo / "f.txt").write_text("new\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "new", "--no-verify")
    _cfg(repo, "diff.evil.textconv", _sh(marker))
    _attrs(repo, "* diff=evil\n")
    assert "+new" in gitops.git(repo, "diff", "HEAD~1", "HEAD").stdout
    _cfg(repo, "diff.evil.command", _sh(marker, "true"))
    _cfg(repo, "diff.external", _sh(marker, "true"))
    assert "+new" in gitops.git(repo, "diff", "HEAD~1", "HEAD").stdout
    assert not marker.exists()


def test_the_owners_global_filter_stays_and_odd_names_are_safe(tmp_path, _owner_home):
    """Only the shared (local/worktree) config is blanked: a global filter (Git LFS) still works. A filter name with
    `=` or a space is passed through the environment, not `-c`, so it cannot split into another key."""
    repo = _repo(tmp_path)
    with open(_owner_home / ".gitconfig", "a", encoding="utf-8") as f:
        f.write('[filter "lfs"]\n\tsmudge = git-lfs smudge %f\n')
    _cfg(repo, "filter.a=b c.smudge", "x")
    env = gitops.shared_config_overrides(repo)
    pairs = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pairs == {"filter.a=b c.smudge": "", "filter.a=b c.clean": "", "filter.a=b c.process": "",
                     "filter.a=b c.required": "false"}
    assert gitops.shared_config_overrides(tmp_path) == {}                          # not a repository: nothing to undo


def test_an_unreadable_shared_config_fails_closed(tmp_path):
    """A config git cannot parse must not silently skip the overrides: the call raises instead of running."""
    repo = _repo(tmp_path)
    with open(repo / ".git" / "config", "a", encoding="utf-8") as f:
        f.write("[broken\n")
    with pytest.raises(gitops.GitError, match="shared git config"):
        gitops.git(repo, "status")
