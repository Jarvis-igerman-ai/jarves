"""Project manager git helper: tasks share one repository, so worktree work takes turns and a held lock file is retried.
No network; a throw-away local repository."""
from __future__ import annotations

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
    _hook(tmp_path / "evil-hooks", "post-merge", marker)
    gitops.git(repo, "config", "core.hooksPath", str(tmp_path / "evil-hooks"))
    gitops.git(repo, "checkout", "-qb", "ff", "HEAD~1")
    gitops.fast_forward(repo, "main")
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


def test_the_empty_hooks_folder_is_reused_and_replaced_once_written_to():
    """#1381 review R3: one empty folder per process instead of a new one per command; a hook written into it never
    runs, because a folder that is no longer empty is replaced."""
    d = gitops._no_hooks_dir()
    assert gitops._no_hooks_dir() == d and not list(Path(d).iterdir())
    (Path(d) / "post-index-change").write_text("#!/bin/sh\n", encoding="utf-8")
    d2 = gitops._no_hooks_dir()
    assert d2 != d and not list(Path(d2).iterdir())
