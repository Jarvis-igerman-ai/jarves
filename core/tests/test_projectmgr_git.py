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


# ---------------------------------------------------------------- #1599: task folders are separate clones
def _poison(folder_git: Path, tmp_path: Path, marker: Path) -> None:
    """Everything an agent can put in its own `.git` that would run a command: every hook, a hooks path, fsmonitor,
    a clean/smudge/process filter and a merge driver named by `.git/info/attributes` for every file, and a config hook."""
    for name in HOOKS:
        _hook(folder_git / "hooks", name, marker)
        _hook(tmp_path / "evil-hooks", name, marker)
    _hook(tmp_path / "evil-bin", "run", marker)
    cmd = (tmp_path / "evil-bin" / "run").as_posix()
    for k, v in (("core.hooksPath", str(tmp_path / "evil-hooks")), ("core.fsmonitor", cmd),
                 ("filter.x.clean", cmd), ("filter.x.smudge", cmd), ("filter.x.process", cmd),
                 ("merge.x.driver", cmd), ("diff.external", cmd), ("hook.h.event", "post-commit"),
                 ("hook.h.command", cmd), ("core.attributesFile", (folder_git / "info" / "attributes").as_posix())):
        subprocess.run(["git", "config", "--file", str(folder_git / "config"), k, v], check=True)
    (folder_git / "info").mkdir(exist_ok=True)
    (folder_git / "info" / "attributes").write_text("* filter=x merge=x diff=x\n", encoding="utf-8")


def _task(tmp_path: Path):
    repo, home = _repo(tmp_path), tmp_path / "home"
    integ = gitops.ensure_integration(home, str(repo), 1)
    wt, br = gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    return repo, home, integ, wt, br


def test_a_task_folder_is_a_separate_clone_with_no_way_back(tmp_path):
    """#1599: the agent's folder has its own `.git` folder (not a worktree pointing into the owner's), no remote, and
    the owner's config is not the one git reads there."""
    repo, _home, _integ, wt, br = _task(tmp_path)
    assert (wt / ".git").is_dir()
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=wt,
                            capture_output=True, text=True, check=True).stdout.strip()
    assert Path(common).resolve() == (wt / ".git").resolve()
    assert subprocess.run(["git", "remote"], cwd=wt, capture_output=True, text=True, check=True).stdout.strip() == ""
    assert gitops.folder_branch(wt) == br and (wt / "f.txt").read_text(encoding="utf-8") == "x"
    assert gitops.where(wt) == (gitops.project_root(_home, 1) / "integration", br)
    assert gitops.where(repo) == (repo, "HEAD")


def test_commands_in_the_agents_own_git_folder_never_run_for_jarves(tmp_path):
    """#1599 / #1390: JARVES's commit, diff, state, merge and cleanup of a task folder never read the folder's `.git`,
    so hooks, filters, merge drivers, fsmonitor and config hooks set there never run, whatever is written when."""
    repo, _home, integ, wt, br = _task(tmp_path)
    marker = tmp_path / "ran.txt"
    _poison(wt / ".git", tmp_path, marker)
    (wt / "probe.txt").write_text("p\n", encoding="utf-8")
    subprocess.run(["git", "add", "probe.txt"], cwd=wt, capture_output=True)          # the setup is live
    assert marker.exists()
    marker.unlink()
    # earlier work on the integration branch touching the same file: a real three-way merge with a merge driver
    # (first a five-line file, fast-forwarded into the folder; then each side changes a different line of it)
    (integ / "g.txt").write_text("1\n2\n3\n4\n5\n", encoding="utf-8")
    gitops.git(integ, "add", "-A")
    gitops.git(integ, "commit", "-qm", "earlier task", "--no-verify")
    assert gitops.merge_in(wt, gitops.integration_branch(1)) == [] and (wt / "g.txt").exists()
    (integ / "g.txt").write_text("1\n2\n3\n4\nintegration\n", encoding="utf-8")
    gitops.git(integ, "commit", "-qam", "later task", "--no-verify")
    (wt / "g.txt").write_text("agent\n2\n3\n4\n5\n", encoding="utf-8")
    (wt / "a.py").write_text("1\n", encoding="utf-8")
    assert gitops.folder_state(wt)[1] is True
    assert gitops.commit_all(wt, "task 1")
    assert sorted(gitops.changed_files(wt, gitops.integration_branch(1))) == ["a.py", "g.txt", "probe.txt"]
    assert gitops.link_files(wt, gitops.integration_branch(1)) == []
    assert gitops.merge_in(wt, gitops.integration_branch(1)) == []
    assert (wt / "g.txt").read_text(encoding="utf-8").replace("\r\n", "\n") == "agent\n2\n3\n4\nintegration\n"
    head, dirty = gitops.folder_state(wt)
    assert not dirty and head == gitops.git(repo, "rev-parse", br).stdout.strip()
    gitops.fast_forward(integ, br)
    gitops.remove_worktree(str(repo), wt, br)
    assert not marker.exists() and not wt.exists()
    owner_cfg = (repo / ".git" / "config").read_text(encoding="utf-8")
    assert "filter" not in owner_cfg and "fsmonitor" not in owner_cfg and "hooksPath" not in owner_cfg


def test_the_agents_git_folder_cannot_hide_a_protected_file(tmp_path):
    """#1599: the task branch is built from the folder's files with the owner's git folder, so an agent that commits,
    excludes or rewrites history in its own `.git` cannot make JARVES miss a `.github/` file."""
    _repo_, _home, _integ, wt, _br = _task(tmp_path)
    (wt / ".github" / "workflows").mkdir(parents=True)
    (wt / ".github" / "workflows" / "x.yml").write_text("on: push\n", encoding="utf-8")
    (wt / ".git" / "info").mkdir(exist_ok=True)
    (wt / ".git" / "info" / "exclude").write_text(".github/\n", encoding="utf-8")       # the clone's git ignores it
    subprocess.run(["git", "-c", "user.name=a", "-c", "user.email=a@b", "commit", "-qm", "own", "--allow-empty",
                    "--no-verify"], cwd=wt, check=True)
    assert gitops.commit_all(wt, "task 1")
    files = gitops.changed_files(wt, gitops.integration_branch(1))
    assert gitops.violations(files, ["src/**"])[0] == [".github/workflows/x.yml"]


def test_a_task_merge_reports_conflicts_and_changes_nothing(tmp_path):
    repo, _home, integ, wt, br = _task(tmp_path)
    (integ / "f.txt").write_text("integration\n", encoding="utf-8")
    gitops.git(integ, "commit", "-qam", "earlier", "--no-verify")
    (wt / "f.txt").write_text("agent\n", encoding="utf-8")
    gitops.commit_all(wt, "task")
    before = gitops.git(repo, "rev-parse", br).stdout.strip()
    assert gitops.merge_in(wt, gitops.integration_branch(1)) == ["f.txt"]
    assert gitops.git(repo, "rev-parse", br).stdout.strip() == before
    assert (wt / "f.txt").read_text(encoding="utf-8") == "agent\n"
    assert gitops.folder_state(wt) == (before, False)


def test_a_task_merge_fast_forwards_a_task_with_no_work(tmp_path):
    repo, _home, integ, wt, br = _task(tmp_path)
    (integ / "n.txt").write_text("new\n", encoding="utf-8")
    gitops.git(integ, "add", "-A")
    gitops.git(integ, "commit", "-qm", "earlier", "--no-verify")
    assert not gitops.commit_all(wt, "nothing")
    assert gitops.merge_in(wt, gitops.integration_branch(1)) == []
    assert (wt / "n.txt").exists()
    assert gitops.git(repo, "rev-parse", br).stdout.strip() == gitops.git(integ, "rev-parse", "HEAD").stdout.strip()


def _symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:          # Windows without symlink rights: skipped, named
        pytest.skip(f"cannot create a symlink here: {e}")


@pytest.mark.parametrize("win", [False, True])
def test_cleanup_never_changes_a_file_outside_the_task_folder(monkeypatch, tmp_path, caplog, win):
    """#1599 review R1: a link in the task folder that cannot be removed (its folder is read-only) must not make
    cleanup chmod the link's target, an owner file outside the folder. The leftover is logged, not hidden."""
    repo, _home, _integ, wt, br = _task(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.chmod(outside, 0o644)
    mode = os.stat(outside).st_mode               # Windows reports 0o666 for any writable file: compare, not 0o644
    (wt / "d").mkdir()
    _symlink(outside, wt / "d" / "l")
    real_unlink = os.unlink

    def unlink(p, *a, **kw):                       # the read-only parent: as root a real chmod 555 would not stop it
        if os.fspath(p) in ("l", str(wt / "d" / "l")):
            raise PermissionError(13, "Permission denied", os.fspath(p))
        return real_unlink(p, *a, **kw)
    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(gitops, "_WIN", win)              # True: the Windows branch (lstat, never through a link)
    gitops.remove_worktree(str(repo), wt, br)
    assert os.stat(outside).st_mode == mode and outside.read_text(encoding="utf-8") == "secret"
    assert "could not remove" in caplog.text and (wt / "d" / "l").is_symlink()


def test_an_old_git_is_named_instead_of_an_unknown_option(monkeypatch, tmp_path):
    """#1599 review R6: task folders need git 2.38 (merge-tree --write-tree); an older git gets a clear message."""
    _repo_, _home, _integ, wt, _br = _task(tmp_path)
    monkeypatch.setattr(gitops, "_GIT_VERSION", [(2, 30)])
    with pytest.raises(gitops.GitError, match="git 2.38 or later"):
        gitops.commit_all(wt, "x")
    assert gitops.merge_in(wt, gitops.integration_branch(1)) == [
        "(not merged: git 2.38 or later is needed for task folders; this git is 2.30)"]


def _agent_status(wt: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True,
                          check=True).stdout


def test_the_agents_own_git_view_follows_jarves_commits_and_merges(tmp_path):
    """#1599 review R1: after commit_all and merge_in the folder's own `git status` is clean, so an agent that
    "discards unrelated changes" cannot revert earlier tasks' work into the task branch."""
    repo, _home, integ, wt, br = _task(tmp_path)
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    assert gitops.commit_all(wt, "task 1")
    assert _agent_status(wt) == ""
    (integ / "n.txt").write_text("n\n", encoding="utf-8")
    gitops.git(integ, "add", "-A")
    gitops.git(integ, "commit", "-qm", "earlier", "--no-verify")
    assert gitops.merge_in(wt, gitops.integration_branch(1)) == []
    assert _agent_status(wt) == ""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True).stdout.strip()
    assert head == gitops.git(repo, "rev-parse", br).stdout.strip()


def test_a_linked_ref_in_the_agents_git_folder_is_never_written_through(tmp_path, caplog):
    """#1599 review R1: the clone's branch ref is written as text only when nothing on its path is a link."""
    _repo_, _home, _integ, wt, br = _task(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    ref = wt / ".git" / "refs" / "heads" / br
    ref.unlink()
    _symlink(outside, ref)
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    assert gitops.commit_all(wt, "task 1")
    assert outside.read_text(encoding="utf-8") == "keep" and "not updated" in caplog.text


def test_an_old_git_makes_no_task_folder(monkeypatch, tmp_path):
    """#1599 review R4: the git 2.38 prerequisite is checked before a task folder is made, with the clear message."""
    repo, home = _repo(tmp_path), tmp_path / "home"
    gitops.ensure_integration(home, str(repo), 1)
    monkeypatch.setattr(gitops, "_GIT_VERSION", [(2, 30)])
    with pytest.raises(gitops.GitError, match="git 2.38 or later"):
        gitops.new_task_worktree(home, str(repo), 1, 1, 1)
    assert not (gitops.project_root(home, 1) / "wt" / "t1-a1").exists()


def test_a_lost_branch_race_puts_the_folder_back(monkeypatch, tmp_path):
    """#1599 review R5: when the task branch moved during a merge, the folder's files go back to the branch's tree
    and the merge says why, instead of a GitError that leaves unexplained edits."""
    _repo_, _home, integ, wt, _br = _task(tmp_path)
    (integ / "n.txt").write_text("n\n", encoding="utf-8")
    gitops.git(integ, "add", "-A")
    gitops.git(integ, "commit", "-qm", "earlier", "--no-verify")
    real = gitops.git

    def git(cwd, *args, **kw):
        if "update-ref" in args:
            raise gitops.GitError("cannot lock ref: is at another value")
        return real(cwd, *args, **kw)
    monkeypatch.setattr(gitops, "git", git)
    out = gitops.merge_in(wt, gitops.integration_branch(1))
    assert out and "another value" in out[0] and not (wt / "n.txt").exists()
    monkeypatch.setattr(gitops, "git", real)
    assert gitops.folder_state(wt)[1] is False


def test_a_shared_worktree_from_an_older_install_is_refused(tmp_path):
    """#1599 review R1: a task folder made by `git worktree add` (an older install) shares the owner's config and
    info/attributes; while one exists JARVES runs no task-folder or ship git in that repository, so a filter it
    planted never runs. Removing it (a stopped task) lets the separate clones work again."""
    repo, home, integ, wt, br = _task(tmp_path)
    old = gitops.project_root(home, 1) / "wt" / "t2-a1"
    gitops.git(repo, "worktree", "add", "-q", "-b", gitops.task_branch(1, 2, 1), str(old), gitops.integration_branch(1))
    assert not gitops.is_clone(old) and gitops.is_clone(wt)
    marker = tmp_path / "ran.txt"
    _hook(tmp_path / "evil-bin", "run", marker)
    cmd = (tmp_path / "evil-bin" / "run").as_posix()
    subprocess.run(["git", "config", "filter.x.clean", cmd], cwd=old, check=True)        # the agent writes it
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* filter=x\n", encoding="utf-8")
    (old / "a.txt").write_text("a\n", encoding="utf-8")
    (wt / "b.txt").write_text("b\n", encoding="utf-8")
    for folder in (old, wt):
        with pytest.raises(gitops.GitError, match="older install"):
            gitops.commit_all(folder, "x")
        assert gitops.merge_in(folder, gitops.integration_branch(1))[0].startswith("(not merged: a task folder")
        with pytest.raises(gitops.GitError, match="older install"):
            gitops.folder_state(folder)
    with pytest.raises(gitops.GitError, match="older install"):
        gitops.fast_forward(integ, br)
    assert not marker.exists()
    gitops.remove_worktree(str(repo), old, gitops.task_branch(1, 2, 1))
    (repo / ".git" / "info" / "attributes").unlink()
    subprocess.run(["git", "config", "--unset", "filter.x.clean"], cwd=repo, check=True)
    assert gitops.commit_all(wt, "task 1") and not marker.exists()


def test_a_failed_view_update_keeps_the_commit(monkeypatch, tmp_path, caplog):
    """#1599 review R2: the task branch moved, so an OSError writing the clone's own ref (Windows: the agent's git
    holds the file) is logged, not raised as a non-GitError that would crash the stage."""
    repo, _home, _integ, wt, br = _task(tmp_path)
    (wt / "a.txt").write_text("a\n", encoding="utf-8")

    def replace(*_a, **_kw):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(gitops.os, "replace", replace)
    assert gitops.commit_all(wt, "task 1")
    assert "not updated" in caplog.text and "Permission denied" in caplog.text
    assert gitops.git(repo, "show", f"{br}:a.txt").stdout == "a\n"
    assert [x.name for x in (wt / ".git" / "refs" / "heads").rglob(".jarves-*")] == []


def test_the_private_index_is_kept_outside_the_folder_and_removed_with_it(tmp_path):
    """#1599 review R4: one private index per task (its stat cache spares a rehash of every file each call), outside
    the task folder, removed with the folder."""
    repo, home, _integ, wt, br = _task(tmp_path)
    idx = gitops.project_root(home, 1) / "idx" / "t1-a1"
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    assert gitops.commit_all(wt, "task 1") and idx.is_file()
    assert gitops.folder_state(wt)[1] is False
    (wt / "a.txt").write_text("changed\n", encoding="utf-8")
    assert gitops.folder_state(wt)[1] is True
    gitops.remove_worktree(str(repo), wt, br)
    assert not idx.exists()
