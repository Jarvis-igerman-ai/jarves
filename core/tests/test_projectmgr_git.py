"""Project manager git helper: tasks share one repository, so worktree work takes turns and a held lock file is retried.
No network; a throw-away local repository."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

from jarves.projectmgr import gitops


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    return repo


def _fake_run(outcomes, calls):
    def run(argv, **kw):
        if "--show-scope" in argv:                          # the config read of #1407, not the call under test
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        calls.append(argv)
        rc, err = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        return subprocess.CompletedProcess(argv, rc, "", err)
    return run


LOCK = "fatal: Unable to create 'C:/r/.git/index.lock': File exists."


def test_a_held_lock_file_is_retried_then_succeeds(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, LOCK), (128, LOCK), (0, "")], calls))
    monkeypatch.setattr(gitops.time, "sleep", lambda s: None)
    assert gitops.git(tmp_path, "status").returncode == 0 and len(calls) == 3


def test_a_lock_that_never_clears_fails_after_the_retries(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, LOCK)], calls))
    monkeypatch.setattr(gitops.time, "sleep", lambda s: None)
    with pytest.raises(gitops.GitError):
        gitops.git(tmp_path, "status")
    assert len(calls) == gitops.LOCK_RETRIES + 1


def test_other_failures_are_never_retried(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(128, "fatal: not a git repository")], calls))
    with pytest.raises(gitops.GitError):
        gitops.git(tmp_path, "status")
    assert len(calls) == 1


def test_parallel_tasks_can_all_create_and_replace_their_worktrees(tmp_path):
    repo, home = _repo(tmp_path), tmp_path / "home"
    gitops.ensure_integration(home, str(repo), 1)
    errors: list = []

    def work(seq: int) -> None:
        for attempt in (1, 2, 3, 1, 2):                      # the repeats replace an earlier worktree, like a retry does
            try:
                wt, _ = gitops.new_task_worktree(home, str(repo), 1, seq, attempt)
                assert (wt / ".git").exists()
            except Exception as e:                           # noqa: BLE001 - collected and asserted below
                errors.append((seq, attempt, str(e)[:200]))

    threads = [threading.Thread(target=work, args=(s,)) for s in range(1, 5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", ".GitHub/workflows/ci.yml", ".GITHUB/dependabot.yml",
    ".Claude/settings.json", ".CLAUDE/agents/x.md", ".Codex/config.toml", ".OpenCode/x", ".Git/config",
    ".GitHooks/pre-commit", "OpenCode.json", "sub/.MCP.json", ".GitModules", r".GitHub\workflows\ci.yml"])
def test_protected_paths_are_refused_in_any_letter_case(path):
    # JAR-317: Windows/macOS ignore case, so `.GitHub/...` is the same CI file as `.github/...`
    prot, outside = gitops.violations([path], ["src/**"])
    assert prot == [path] and outside == []


def test_changed_files_shows_non_ascii_and_renamed_protected_paths(tmp_path):
    # JAR-574: plain `git diff --name-only` quotes a non-ASCII name (`".github/workflows/\303\251.yml"`), which no
    # PROTECTED prefix matches, and names only the target of a rename, hiding a file moved out of `.github/`
    repo = _repo(tmp_path)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "ci", "--no-verify")
    base = gitops.git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / ".GitHub" / "workflows").mkdir(parents=True, exist_ok=True)  # a case-sensitive FS needs its own dir
    (repo / ".GitHub" / "workflows" / "é.yml").write_text("on: push\n", encoding="utf-8")
    (repo / "src").mkdir()
    gitops.git(repo, "mv", ".github/workflows/ci.yml", "src/ci.yml")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "sneak", "--no-verify")
    files = gitops.changed_files(repo, base)
    prot, _ = gitops.violations(files, ["src/**"])
    assert ".github/workflows/ci.yml" in prot
    # exact name: gitops.git() decodes stdout as UTF-8 (encoding="utf-8"), never the Windows locale codec
    assert any(f.casefold() == ".github/workflows/é.yml" for f in prot), files


@pytest.mark.parametrize("path", ["CLAUDE~1/settings.local.json", "GITHUB~1/workflows/ci.yml", "github~2/x.yml",
                                  "src/OPENCO~1.JSO", r"GITHUB~1\workflows\ci.yml"])
def test_windows_short_names_are_refused(path):
    # JAR-574: a Windows checkout writes `CLAUDE~1/x` into the existing `.claude/` folder
    assert gitops.is_protected(path)


@pytest.mark.parametrize("path", ["src/github/x.py", "docs/.github.md", "src/claude/x.py", "my.mcp.json.bak",
                                  "src/backup~/x.py", "notes~old.md", "src/version~12345678/x.py"])
def test_ordinary_paths_with_similar_names_are_not_protected(path):
    assert not gitops.is_protected(path)


def test_agent_tool_rules_deny_edits_to_github_workflows():
    from jarves.projectmgr import runners
    assert "Edit(.github/**)" in runners.DENY and "Write(.github/**)" in runners.DENY


def _hook(folder: Path, name: str, marker: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    h = folder / name
    h.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\n", encoding="utf-8")
    h.chmod(0o755)


# every hook JARVES's own commands could trigger (#1381): index writes run post-index-change, ref writes run
# reference-transaction, merges, commits and checkouts run the rest
HOOKS = ("post-merge", "pre-merge-commit", "commit-msg", "prepare-commit-msg", "pre-commit", "post-commit",
         "post-checkout", "post-index-change", "reference-transaction", "post-rewrite")


def test_merges_never_run_hooks_from_the_shared_git_folder_or_config(tmp_path):
    """#1381 item 1: an agent that writes `.git/hooks` or sets `core.hooksPath` in the shared config must not get code
    run by JARVES's merges (merge_in, its abort, fast_forward; ship uses merge_in with the owner's identity)."""
    repo, marker = _repo(tmp_path), tmp_path / "hook-ran.txt"
    gitops.git(repo, "config", "user.name", "Owner")                              # ident=False: the owner's identity
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    for br in ("side", "side2", "main"):                                          # three divergent tips
        gitops.git(repo, "checkout", "-q", *(["main"] if br == "main" else ["-b", br, "main"]))
        (repo / f"{br}.txt").write_text(br, encoding="utf-8")
        gitops.git(repo, "add", "-A")
        gitops.git(repo, "commit", "-qm", br, "--no-verify")
    for name in HOOKS:
        _hook(repo / ".git" / "hooks", name, marker)
    assert gitops.merge_in(repo, "side") == [] and (repo / "side.txt").exists()   # a real merge commit
    assert gitops.merge_in(repo, "side2", ident=False) == [] and (repo / "side2.txt").exists()   # the ship path
    assert gitops.git(repo, "log", "-1", "--format=%an").stdout.strip() == "Owner"
    assert gitops.merge_in(repo, "main~1") == []                                  # already merged: nothing to do
    gitops.git(repo, "checkout", "-qb", "ff", "HEAD~1")
    gitops.fast_forward(repo, "main")
    assert not marker.exists()


def _evil_config(repo: Path, tmp_path: Path, marker: Path) -> None:
    """What an agent that can write the shared `.git/config` sets: a hooks folder with every hook, and an fsmonitor
    command, which git runs on every index refresh (add, status, diff, merge) whatever `core.hooksPath` says."""
    for name in HOOKS:
        _hook(tmp_path / "evil-hooks", name, marker)
    _hook(tmp_path / "evil-bin", "fsmonitor", marker)
    subprocess.run(["git", "config", "core.hooksPath", str(tmp_path / "evil-hooks")], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.fsmonitor", (tmp_path / "evil-bin" / "fsmonitor").as_posix()], cwd=repo,
                   check=True)


def test_hooks_and_fsmonitor_set_in_the_shared_config_never_run(tmp_path):
    """#1381 review R1 and R5: a merge commit, a fast-forward, worktree add and a commit with `core.hooksPath` and
    `core.fsmonitor` set in the shared config run neither."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "hook-ran.txt"
    gitops.git(repo, "config", "user.name", "Owner")                    # ident=False needs the owner's identity
    gitops.git(repo, "config", "user.email", "owner@example.invalid")
    gitops.git(repo, "checkout", "-qb", "side")
    (repo / "side.txt").write_text("s", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "side", "--no-verify")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "main.txt").write_text("m", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "main", "--no-verify")
    _evil_config(repo, tmp_path, marker)
    subprocess.run(["git", "status"], cwd=repo, capture_output=True, check=True)     # the setup itself works
    assert marker.exists()
    marker.unlink()
    assert gitops.merge_in(repo, "side", ident=False) == [] and (repo / "side.txt").exists()     # merge commit
    gitops.git(repo, "checkout", "-qb", "ff", "side")
    gitops.fast_forward(repo, "main")
    gitops.ensure_integration(home, str(repo), 1)
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a") and gitops.changed_files(wt, "main") == ["a.py"]
    assert not marker.exists()


def test_symlinks_and_gitlinks_in_scope_count_as_protected(tmp_path):
    """#1381 item 2: `is_protected` only reads path text, so a symlink in scope pointing into `.github/` or a gitlink
    (submodule entry) in scope passed the scope check. link_files reports both; the engine treats them as protected."""
    repo = _repo(tmp_path)
    base = gitops.git(repo, "rev-parse", "HEAD").stdout.strip()
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input="../.github/workflows",
                          capture_output=True, text=True, check=True).stdout.strip()
    gitops.git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},src/link")
    gitops.git(repo, "update-index", "--add", "--cacheinfo", f"160000,{base},src/sub")
    (repo / "src").mkdir()
    (repo / "src" / "ok.py").write_text("1", encoding="utf-8")
    gitops.git(repo, "add", "src/ok.py")
    gitops.git(repo, "commit", "-qm", "links", "--no-verify")
    files = gitops.changed_files(repo, base)
    assert gitops.violations(files, ["src/"]) == ([], [])                       # the path text alone looks harmless
    assert sorted(gitops.link_files(repo, base)) == ["src/link", "src/sub"]


def test_worktree_creation_and_commits_never_run_shared_hooks(tmp_path):
    """#1381 review: worktree add runs `post-checkout`, `commit --no-verify` still runs `prepare-commit-msg` and
    `post-commit`, `add -A` runs `post-index-change`, branch create/delete runs `reference-transaction`. JARVES's own
    git commands run no shared hook at all."""
    repo, home, marker = _repo(tmp_path), tmp_path / "home", tmp_path / "hook-ran.txt"
    for name in HOOKS:
        _hook(repo / ".git" / "hooks", name, marker)
    gitops.ensure_integration(home, str(repo), 1)
    wt, _ = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.commit_all(wt, "a")
    gitops.detached_worktree(home, str(repo), 1, gitops.git(wt, "rev-parse", "HEAD").stdout.strip())
    gitops.remove_worktree(str(repo), wt, gitops.task_branch(1, 1, 1))           # branch -D: a ref write
    assert gitops.git(repo, "branch", "--list", gitops.task_branch(1, 1, 1)).stdout.strip() == ""
    assert not marker.exists()


def test_the_hooks_path_can_never_become_a_folder_an_agent_fills():
    """#1381 review R1: a shared temp folder could be filled by a racing agent between a check and git's hook lookup.
    The hooks path sits below an existing file, so nobody can create it as a folder, and every git call uses it."""
    assert not os.path.isdir(gitops.NO_HOOKS)
    with pytest.raises(OSError):
        os.makedirs(gitops.NO_HOOKS)
    with pytest.raises(OSError):
        (Path(gitops.NO_HOOKS) / "post-merge").write_text("#!/bin/sh\n", encoding="utf-8")


def test_every_git_call_disables_hooks_and_fsmonitor(monkeypatch, tmp_path):
    """#1381 review R3: the one `git()` helper puts both overrides in front of every command, with either identity."""
    calls: list = []
    monkeypatch.setattr(gitops.subprocess, "run", _fake_run([(0, "")], calls))
    gitops.git(tmp_path, "merge", "x")
    gitops.git(tmp_path, "merge", "x", ident=False)
    for argv in calls:
        pairs = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-c"]
        assert f"core.hooksPath={gitops.NO_HOOKS}" in pairs and "core.fsmonitor=false" in pairs
        assert argv.index("merge") > max(i for i, a in enumerate(argv) if a == "-c")


@pytest.fixture
def no_user_config(monkeypatch, tmp_path):
    """Hermetic: the machine's global git config (a signing program, say) must not decide these tests."""
    for k in ("HOME", "XDG_CONFIG_HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")                  # nor the system config (child_env drops it)
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))


def _spy(tmp_path: Path, name: str) -> tuple[str, Path]:
    """A config-named command that leaves a marker file if git ever runs it (#1407)."""
    marker = tmp_path / f"ran-{name}"
    return f'echo ran > "{marker.as_posix()}"; cat', marker


@pytest.mark.parametrize("name", ["evil", "Evil", "x=y"])   # case-sensitive; `=` inside (reviews R1, R1 round 2)
def test_a_filter_driver_from_the_repo_config_never_runs(tmp_path, no_user_config, name):
    repo = _repo(tmp_path)
    cmd, marker = _spy(tmp_path, "filter")
    gitops.git(repo, "config", f"filter.{name}.clean", cmd)
    gitops.git(repo, "config", f"filter.{name}.required", "true")
    (repo / ".gitattributes").write_text(f"*.txt filter={name}\n", encoding="utf-8")
    (repo / "g.txt").write_text("y", encoding="utf-8")
    assert gitops.commit_all(repo, "with attributes") and not marker.exists()


@pytest.mark.parametrize("name", ["evil", "Evil", "x=y"])
def test_a_merge_driver_from_the_repo_config_never_runs(tmp_path, no_user_config, name):
    repo = _repo(tmp_path)
    cmd, marker = _spy(tmp_path, "merge")
    gitops.git(repo, "config", f"merge.{name}.driver", cmd)
    (repo / ".gitattributes").write_text(f"f.txt merge={name}\n", encoding="utf-8")
    gitops.commit_all(repo, "attributes")
    gitops.git(repo, "checkout", "-q", "-b", "side")
    (repo / "f.txt").write_text("side", encoding="utf-8")
    gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text("main", encoding="utf-8")
    gitops.commit_all(repo, "main")
    assert gitops.merge_in(repo, "side") == ["f.txt"] and not marker.exists()      # a conflict, nothing ran


def test_a_signing_program_from_the_repo_config_never_runs_on_the_owner_merge(tmp_path, no_user_config):
    repo = _repo(tmp_path)
    marker = tmp_path / "ran-gpg"
    spy = tmp_path / "gpg-spy.sh"                       # gpg.program is run directly, not through a shell
    spy.write_text(f'#!/bin/sh\necho ran > "{marker.as_posix()}"\nexit 1\n', encoding="utf-8")
    spy.chmod(0o755)
    cmd = spy.as_posix()
    gitops.git(repo, "checkout", "-q", "-b", "side")
    (repo / "s.txt").write_text("s", encoding="utf-8")
    gitops.commit_all(repo, "side")
    gitops.git(repo, "checkout", "-q", "main")
    (repo / "m.txt").write_text("m", encoding="utf-8")
    gitops.commit_all(repo, "main")
    for k, v in (("gpg.program", cmd), ("commit.gpgsign", "true"), ("user.name", "Owner"), ("user.email", "o@x")):
        gitops.git(repo, "config", k, v)
    assert gitops.merge_in(repo, "side", ident=False) == [] and not marker.exists()
    assert (repo / "s.txt").exists()


def test_config_hooks_and_gpg_programs_from_the_repo_config_are_replaced(tmp_path, no_user_config):
    repo = _repo(tmp_path)
    gitops.git(repo, "config", "hook.evil.command", "rm -rf /")      # newer git runs these; 2.43 does not
    gitops.git(repo, "config", "gpg.ssh.program", "evil")
    pre = gitops.config_overrides(repo)
    assert ("hook.evil.command", "exit 0") in pre and ("gpg.ssh.program", "ssh-keygen") in pre
    assert not any(v == "evil" for _, v in pre)
    assert ("commit.gpgsign", "false") in gitops.config_overrides(repo, ident=False)


def test_your_own_same_named_driver_is_kept_over_the_repo_config(tmp_path, no_user_config):
    """Review R4: a local value shadowing your global filter/merge driver or config hook keeps your value, not a no-op."""
    repo = _repo(tmp_path)
    glob = {"filter.lfs.clean": "git-lfs clean -- %f", "filter.lfs.required": "true",
            "merge.mine.driver": "my-merge %O %A %B", "hook.mine.command": "my-hook"}
    (tmp_path / "home").mkdir(exist_ok=True)
    for k, v in glob.items():
        subprocess.run(["git", "config", "--global", k, v], check=True, env={**os.environ})
        gitops.git(repo, "config", k, "evil")
    pre = gitops.config_overrides(repo)
    assert all((k, v) in pre for k, v in glob.items()) and not any(v == "evil" for _, v in pre), pre


@pytest.mark.parametrize("key,attr", [("diff.external", ""), ("diff.Evil.command", "diff=Evil"),
                                      ("diff.Evil.textconv", "diff=Evil")])
def test_a_diff_program_from_the_repo_config_never_runs(tmp_path, no_user_config, key, attr):
    """Review R2: porcelain `git diff` honours diff.external, diff.<drv>.command and textconv by default."""
    repo = _repo(tmp_path)
    cmd, marker = _spy(tmp_path, "diff")
    if attr:
        (repo / ".gitattributes").write_text(f"*.txt {attr}\n", encoding="utf-8")
    gitops.commit_all(repo, "attributes")
    (repo / "f.txt").write_text("changed", encoding="utf-8")
    gitops.commit_all(repo, "change")
    gitops.git(repo, "config", key, cmd)
    gitops.git(repo, "diff", "HEAD~1...HEAD", check=False)
    gitops.diffstat(repo, "HEAD~1")
    assert not marker.exists()
    pairs = dict(gitops.config_overrides(repo))
    assert pairs[key] == "exit 1" and cmd not in pairs.values()


def test_an_ssh_default_key_command_from_the_repo_config_never_runs(tmp_path, no_user_config):
    """Review R2: `gpg.ssh.defaultKeyCommand` runs when gpg.format=ssh signs without a key."""
    repo = _repo(tmp_path)
    marker = tmp_path / "ran-key"
    spy = tmp_path / "key-spy.sh"
    spy.write_text(f'#!/bin/sh\necho ran > "{marker.as_posix()}"\nexit 1\n', encoding="utf-8")
    spy.chmod(0o755)
    for k, v in (("gpg.format", "ssh"), ("gpg.ssh.defaultKeyCommand", spy.as_posix()), ("commit.gpgsign", "true"),
                 ("user.name", "Owner"), ("user.email", "o@x")):
        gitops.git(repo, "config", k, v)
    (repo / "k.txt").write_text("k", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "owner", ident=False)
    assert not marker.exists()
    assert ("gpg.ssh.defaultkeycommand", "exit 1") in gitops.config_overrides(repo)


def test_an_unreadable_repo_config_fails_closed_and_check_false_never_raises(tmp_path, no_user_config):
    """Review R5: a malformed .git/config written by an agent: the command never runs, check=False returns 128."""
    repo = _repo(tmp_path)
    (repo / ".git" / "config").write_text("[broken\n", encoding="utf-8")
    assert gitops.git(repo, "status", check=False).returncode == 128
    with pytest.raises(gitops.GitError):
        gitops.git(repo, "status")
