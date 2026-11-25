"""#1592: code a check command (or the agent) runs from the worktree can write the shared git dir (`.git/config`,
`.git/hooks`). JARVES restores it after the run and fails that check or agent run, so a planted entry never stays."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jarves.projectmgr import gitops, runners

PLANT = ("import pathlib,subprocess;"
         "c=pathlib.Path(subprocess.check_output(['git','rev-parse','--path-format=absolute','--git-common-dir'],"
         "text=True).strip());"
         "open(c/'config','a').write('[filter \"x\"]\\n\\tclean = touch pwned\\n');"
         "(c/'hooks').mkdir(exist_ok=True);(c/'hooks'/'pre-commit').write_text('#!/bin/sh\\ntouch pwned\\n')")


@pytest.fixture()
def wt(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(gitops, "ENV_KEEP", (*gitops.ENV_KEEP, "GIT_CONFIG_NOSYSTEM"))
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    return gitops.ensure_integration(tmp_path, str(repo), 1)     # pins the shared git dir from the repository


def _shared(w: Path) -> tuple[bytes, list[str]]:
    common = Path(gitops.git(w, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    hooks = sorted(p.name for p in (common / "hooks").iterdir()) if (common / "hooks").is_dir() else []
    return (common / "config").read_bytes(), hooks


def test_a_check_that_writes_the_shared_git_dir_fails_and_is_restored(wt):
    before = _shared(wt)
    ok, out = gitops.run_check(wt, [sys.executable, "-c", PLANT])
    assert not ok and "shared git" in out and "config" in out
    assert _shared(wt) == before                       # no planted filter, no planted hook


def test_a_clean_check_still_passes(wt):
    assert gitops.run_check(wt, [sys.executable, "-c", "print('ok')"]) == (True, "ok")


class _Fake(runners.Runner):
    name = "fake"

    def build(self, prompt, cwd, **kw):
        return runners.Spec([sys.executable, "-c", PLANT], None, gitops.child_env())

    def parse(self, stdout, rc):
        return runners.Result(ok=rc == 0, text="done")


def test_an_agent_run_that_writes_the_shared_git_dir_fails_and_is_restored(wt):
    before = _shared(wt)
    r = _Fake().run("p", str(wt))
    assert not r.ok and "shared git" in r.error
    assert _shared(wt) == before


def test_overlapping_guards_keep_the_first_baseline(wt):
    """Parallel tasks share one repository: a run that starts after a plant must never take it as its baseline."""
    before = _shared(wt)
    a, b = [], []
    ga, gb = gitops.shared_git_guard(wt, a), gitops.shared_git_guard(wt, b)
    ga.__enter__()
    subprocess.run([sys.executable, "-c", PLANT], cwd=wt, check=True)
    gb.__enter__()
    ga.__exit__(None, None, None)                      # restores and reports to the first run
    gb.__exit__(None, None, None)                      # must not put the planted files back
    assert a and _shared(wt) == before


def test_a_file_that_cannot_be_restored_still_fails_the_check(wt, monkeypatch):
    def deny(*_a):
        raise PermissionError("denied")
    monkeypatch.setattr(gitops, "_restore", deny)
    ok, out = gitops.run_check(wt, [sys.executable, "-c", PLANT])
    assert not ok and "config (NOT restored: denied)" in out


def _common(w: Path) -> Path:
    return Path(gitops.git(w, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())


def _fake_gitdir(w: Path, tmp: Path) -> bytes:
    """Point `<wt>/.git` at a gitdir whose common dir is a throw-away copy (review R1); returns the real pointer."""
    real = (w / ".git").read_bytes()
    fake = tmp / "fake"
    shutil.copytree(_common(w), fake / "common", symlinks=True)
    (fake / "gd").mkdir()
    (fake / "gd" / "commondir").write_text(str(fake / "common"), encoding="utf-8")
    (fake / "gd" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (w / ".git").write_text(f"gitdir: {fake / 'gd'}\n", encoding="utf-8")
    return real


def test_a_rewritten_worktree_pointer_never_redirects_the_guard(wt, tmp_path):
    """R1: the common dir is pinned from the repository, not read from the agent-writable `<wt>/.git`."""
    real_common, before = _common(wt), _shared(wt)
    real = _fake_gitdir(wt, tmp_path)
    plant = PLANT.replace("c=pathlib.Path(subprocess.check_output(['git','rev-parse','--path-format=absolute',"
                          "'--git-common-dir'],text=True).strip())", f"c=pathlib.Path({str(real_common)!r})")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", plant])
    (wt / ".git").write_bytes(real)
    assert not ok and "config" in out and _shared(wt) == before


def test_an_agent_that_rewrites_the_worktree_pointer_fails_and_it_is_restored(wt, tmp_path):
    real = (wt / ".git").read_bytes()
    code = f"import pathlib;pathlib.Path({str(wt / '.git')!r}).write_text('gitdir: /elsewhere\\n')"
    ok, out = gitops.run_check(wt, [sys.executable, "-c", code])
    assert not ok and ".git (worktree pointer)" in out and (wt / ".git").read_bytes() == real


def test_a_hooks_folder_swapped_for_a_symlink_is_put_back(wt):
    """R2: `hooks` must stay a real folder inside the git dir, never a link into the worktree."""
    hooks = _common(wt) / "hooks"
    names = sorted(p.name for p in hooks.iterdir())
    code = (f"import os,shutil;h={str(hooks)!r};shutil.rmtree(h);os.makedirs('evil',exist_ok=True);"
            "os.symlink(os.path.abspath('evil'),h)")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", code])
    assert not ok and "hooks" in out
    assert not hooks.is_symlink() and hooks.is_dir() and sorted(p.name for p in hooks.iterdir()) == names
    assert not any((wt / "evil").iterdir())          # the samples were not "restored" into the worktree


def test_an_unrestorable_entry_never_stops_the_other_restores(wt):
    """R3: a folder planted as `config.worktree` before the run is listed, and the planted hook still goes."""
    (_common(wt) / "config.worktree").mkdir()
    (_common(wt) / "config.worktree" / "x").write_text("x", encoding="utf-8")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", PLANT])
    assert not ok and "config.worktree (NOT restored" in out
    assert not (_common(wt) / "hooks" / "pre-commit").exists() and b"pwned" not in (_common(wt) / "config").read_bytes()


def test_an_owner_hook_symlink_is_re_created(wt):
    """R4: the owner's own hook link comes back as the same link."""
    link = _common(wt) / "hooks" / "pre-commit"
    os.symlink("../../scripts/pre-commit", link)
    code = f"import os;p={str(link)!r};os.unlink(p);open(p,'w').write('#!/bin/sh\\ntouch pwned\\n')"
    ok, out = gitops.run_check(wt, [sys.executable, "-c", code])
    assert not ok and link.is_symlink() and os.readlink(link) == "../../scripts/pre-commit"
