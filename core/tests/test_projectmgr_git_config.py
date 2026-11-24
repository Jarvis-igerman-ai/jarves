"""#1390: commands the shared repository config names (filters, merge drivers, diff drivers) never run from JARVES's
own git calls. An agent can write `.git/config`, an included file and `.git/info/attributes` (no commit needed)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarves.projectmgr import gitops


@pytest.fixture(autouse=True)
def _owner_home(tmp_path, monkeypatch):
    """An empty global config of our own and no system config, so the host's git config cannot shape the result
    (Git for Windows ships a system `filter.lfs`)."""
    home = tmp_path / "owner-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Owner\n\temail = owner@example.invalid\n", encoding="utf-8")
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))
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
    assert "+new" in gitops.git(repo, "show", "HEAD").stdout                     # review R4: show and log -p too
    assert "+new" in gitops.git(repo, "log", "-p", "-1").stdout
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


def test_editors_named_in_the_shared_config_never_run(tmp_path):
    """#1390: `core.editor` / `sequence.editor` run on any JARVES git call that opens a message; the editor is `:`."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    _cfg(repo, "core.editor", _sh(marker, "true"))
    subprocess.run(["git", "commit", "--amend", "--no-verify"], cwd=repo, check=True, capture_output=True,
                   env=gitops.child_env())                                         # no GIT_EDITOR from the host
    assert marker.exists()                                                         # the setup itself works
    marker.unlink()
    gitops.git(repo, "commit", "--amend", "--no-verify")
    assert not marker.exists()


def test_submodules_are_never_recursed_into(tmp_path):
    """Review R1: `submodule.recurse=true` in the shared config makes checkout recurse into a submodule, whose own
    config (`.git/modules/<s>/config`, agent-writable) is never read, so its filter would run. JARVES never recurses."""
    (tmp_path / "s").mkdir()
    sub, marker = _repo(tmp_path / "s"), tmp_path / "ran.txt"
    repo = _repo(tmp_path)
    file_ok = ["-c", "protocol.file.allow=always"]
    subprocess.run(["git", *file_ok, "submodule", "add", "-q", str(sub), "sub"], cwd=repo, check=True)
    gitops.git(repo, "commit", "-qm", "sub", "--no-verify")
    old = gitops.git(repo, "rev-parse", "HEAD").stdout.strip()
    (sub / "f.txt").write_text("v2\n", encoding="utf-8")
    gitops.git(sub, "commit", "-qam", "v2", "--no-verify")
    subprocess.run(["git", "-C", "sub", "pull", "-q", "--no-rebase", "origin", "main"], cwd=repo, check=True,
                   capture_output=True)
    gitops.git(repo, "commit", "-qam", "sub v2", "--no-verify")
    mod = repo / ".git" / "modules" / "sub"
    _cfg(repo, "filter.evil.smudge", _sh(marker), file=str(mod / "config"))
    (mod / "info").mkdir(exist_ok=True)
    (mod / "info" / "attributes").write_text("* filter=evil\n", encoding="utf-8")
    _cfg(repo, "submodule.recurse", "true")
    subprocess.run(["git", "checkout", "-q", old], cwd=repo, check=True, env=gitops.child_env())
    assert marker.exists()                                                         # the setup itself works
    marker.unlink()
    gitops.git(repo, "checkout", "-q", "main")
    assert not marker.exists()


def _touch(path: Path, n: int) -> None:
    import os
    os.utime(path, (1_000_000_000 + n, 1_000_000_000 + n))                        # stat-dirty, same content


def test_a_populated_submodule_is_never_checked_with_its_own_filters(tmp_path):
    """Round-3 review R2: in the superproject, status, diff, add and merge look inside each populated submodule to
    see whether it is dirty; that child git reads `.git/modules/<s>/config` and runs its clean filter on a
    stat-dirty file, even with `submodule.<s>.ignore=none` set locally. JARVES runs none of it, and a gitlink
    change still shows."""
    (tmp_path / "s").mkdir()
    sub, marker = _repo(tmp_path / "s"), tmp_path / "ran.txt"
    repo = _repo(tmp_path)
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "sub"], cwd=repo,
                   check=True, capture_output=True)
    gitops.git(repo, "commit", "-qm", "sub", "--no-verify")
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "side.txt").write_text("s", encoding="utf-8")
    gitops.git(repo, "add", "side.txt")
    gitops.git(repo, "commit", "-qm", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    mod = repo / ".git" / "modules" / "sub"
    _cfg(repo, "filter.evil.clean", _sh(marker), file=str(mod / "config"))
    (mod / "info").mkdir(exist_ok=True)
    (mod / "info" / "attributes").write_text("* filter=evil\n", encoding="utf-8")
    _cfg(repo, "submodule.sub.ignore", "none")
    _touch(repo / "sub" / "f.txt", 1)
    subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True,
                   env=gitops.child_env())
    assert marker.exists()                                                         # the setup itself works
    marker.unlink()
    for n, call in enumerate((lambda: gitops.git(repo, "status", "--porcelain"), lambda: gitops.git(repo, "diff"),
                              lambda: gitops.commit_all(repo, "x"), lambda: gitops.merge_in(repo, "side")), 2):
        _touch(repo / "sub" / "f.txt", n)
        call()
    assert not marker.exists() and (repo / "side.txt").exists()
    gitops.git(sub, "commit", "-qm", "e", "--allow-empty", "--no-verify")
    subprocess.run(["git", "-C", "sub", "pull", "-q", "--no-rebase", "origin", "main"], cwd=repo, check=True,
                   capture_output=True)
    gitops.git(repo, "add", "sub")
    gitops.git(repo, "commit", "-qm", "bump", "--no-verify")
    assert gitops.changed_files(repo, "HEAD~1") == ["sub"]                         # gitlink changes still show


def test_blanked_diff_drivers_do_not_break_a_merge(tmp_path):
    """Round-3 review R7: a merge (git prints its diffstat by default) with a `diff=evil` attribute and a shared
    textconv still merges, and the textconv never runs."""
    repo, marker = _repo(tmp_path), tmp_path / "ran.txt"
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "f.txt").write_text("side\n", encoding="utf-8")
    gitops.git(repo, "commit", "-qam", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "g.txt").write_text("g\n", encoding="utf-8")
    gitops.git(repo, "add", "g.txt")
    gitops.git(repo, "commit", "-qm", "g", "--no-verify")
    _cfg(repo, "diff.evil.textconv", _sh(marker))
    _attrs(repo, "* diff=evil\n")
    assert gitops.merge_in(repo, "side") == [] and (repo / "f.txt").read_text(encoding="utf-8") == "side\n"
    assert not marker.exists()


def test_a_shared_filter_with_the_owners_filter_name_fails_closed(tmp_path, _owner_home):
    """Review R3: blanking a filter the owner's config also defines (Git LFS) would silently commit binaries
    instead of pointers; JARVES refuses with a message instead."""
    repo = _repo(tmp_path)
    with open(_owner_home / ".gitconfig", "a", encoding="utf-8") as f:
        f.write('[filter "lfs"]\n\tsmudge = git-lfs smudge %f\n')
    _cfg(repo, "filter.lfs.required", "true")
    with pytest.raises(gitops.GitError, match="redefines your filter 'lfs'"):
        gitops.git(repo, "status")


def test_global_options_before_the_subcommand_are_refused(tmp_path):
    """Review R4: the config is read in `cwd`; `-C` or `--git-dir` would run git in another repository."""
    repo = _repo(tmp_path)
    for args in (("-C", str(tmp_path), "status"), ("--git-dir=x", "status"), ("--work-tree", "x", "status"),
                 ("--exec-path=x", "status"), ("--bare", "status"), ("-p", "log"), ("--literal-pathspecs", "add")):
        with pytest.raises(gitops.GitError, match="subcommand first"):
            gitops.git(repo, *args)


def test_a_git_older_than_2_31_is_refused(monkeypatch, tmp_path):
    """Round-3 review R1: git 2.26-2.30 accepts --show-scope but ignores GIT_CONFIG_COUNT, so every override would be
    silently dropped (fails open). Below 2.31 JARVES runs no git command at all."""
    calls: list = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "git version 2.30.2\n", "")
    monkeypatch.setattr(gitops, "_GIT_OK", [])
    monkeypatch.setattr(gitops.subprocess, "run", run)
    with pytest.raises(gitops.GitError, match=r"git 2\.31 or later \(found: 2\.30\)"):
        gitops.git(tmp_path, "status")
    assert calls == [["git", "version"]]                                           # nothing else ran
