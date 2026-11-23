"""#1592: a check command (run by JARVES or by the agent) executes worktree code that could write the shared git config."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from jarves.projectmgr import gitops, runners

PLANT = '[filter "x"]\n\tclean = evil\n'


def _wt(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    gitops.git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    gitops.git(repo, "add", "-A")
    gitops.git(repo, "commit", "-qm", "init", "--no-verify")
    gitops.ensure_integration(tmp_path / "home", str(repo), 1)
    wt, _ = gitops.new_task_worktree(tmp_path / "home", str(repo), 1, 1, 1)
    return wt, repo / ".git" / "config"


def _append(path: Path, text: str) -> list[str]:
    return [sys.executable, "-c", f"open({str(path)!r}, 'a').write({text!r})"]


def _rejected(cfg: Path) -> list[Path]:
    return sorted((cfg.parent / "jarves-rejected").glob("*"))


def test_a_check_that_plants_a_filter_in_the_shared_config_fails_and_is_undone(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    ok, out = gitops.run_check(wt, _append(cfg, PLANT))
    assert not ok and gitops.TAMPERED in out
    assert cfg.read_bytes() == before
    assert ["evil" in f.read_text(encoding="utf-8") for f in _rejected(cfg)] == [True]   # kept for the owner to see


def test_a_check_that_rewrites_the_worktree_pointer_is_undone(tmp_path):
    wt, _cfg = _wt(tmp_path)
    before = (wt / ".git").read_bytes()
    ok, out = gitops.run_check(wt, _append(wt / ".git", "\n"))
    assert not ok and gitops.TAMPERED in out and (wt / ".git").read_bytes() == before


def test_a_clean_check_passes_and_leaves_the_config_alone(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    assert gitops.run_check(wt, [sys.executable, "-c", "print('ok')"])[0] is True
    assert cfg.read_bytes() == before and _rejected(cfg) == []
    assert gitops._GUARDS == {}


def test_a_check_outside_a_git_worktree_is_refused(tmp_path):
    marker = tmp_path / "ran"
    ok, out = gitops.run_check(tmp_path, _append(marker, "x"))
    assert not ok and out.startswith("check refused") and not marker.exists()


def test_overlapping_runs_never_adopt_an_entry_an_earlier_run_planted(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    files = gitops.guarded_files(wt)
    with gitops.config_guard(files) as first:
        cfg.write_text(cfg.read_text(encoding="utf-8") + PLANT, encoding="utf-8")
        with gitops.config_guard(files) as second:   # starts after the plant: must not take it as the baseline
            pass
        assert cfg.read_bytes() == before and second["tampered"] and first["tampered"]
    assert cfg.read_bytes() == before and gitops._GUARDS == {}


def test_every_incident_keeps_its_own_rejected_copy(tmp_path):
    wt, cfg = _wt(tmp_path)
    assert not gitops.run_check(wt, _append(cfg, PLANT))[0]
    assert not gitops.run_check(wt, _append(cfg, PLANT.replace("evil", "worse")))[0]
    texts = "".join(f.read_text(encoding="utf-8") for f in _rejected(cfg))
    assert len(_rejected(cfg)) == 2 and "evil" in texts and "worse" in texts


def test_a_planted_hook_attributes_and_worktree_config_are_removed(tmp_path):
    wt, cfg = _wt(tmp_path)
    common = cfg.parent
    gitdir = Path((wt / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip())
    plants = [common / "hooks" / "pre-commit", gitdir / "config.worktree", common / "info" / "attributes"]
    script = ("import os, sys\n"
              "for p in sys.argv[1:]:\n"
              "    os.makedirs(os.path.dirname(p), exist_ok=True)\n"
              "    open(p, 'w').write('evil')\n")
    ok, out = gitops.run_check(wt, [sys.executable, "-c", script, *map(str, plants)])
    assert not ok and gitops.TAMPERED in out
    assert [p.exists() for p in plants] == [False, False, False] and len(_rejected(cfg)) == 3


def test_a_changed_existing_hook_gets_its_text_and_mode_back(tmp_path):
    wt, cfg = _wt(tmp_path)
    hook = cfg.parent / "hooks" / "post-merge"
    hook.write_text("#!/bin/sh\necho owner\n", encoding="utf-8")
    hook.chmod(0o755)
    mode = hook.stat().st_mode
    ok, out = gitops.run_check(wt, _append(hook, "echo evil\n"))
    assert not ok and gitops.TAMPERED in out and hook.read_text(encoding="utf-8") == "#!/bin/sh\necho owner\n" and hook.stat().st_mode == mode


def test_a_timed_out_check_that_planted_an_entry_says_both(tmp_path):
    wt, cfg = _wt(tmp_path)
    script = f"open({str(cfg)!r}, 'a').write({PLANT!r}); import time; time.sleep(60)"
    ok, out = gitops.run_check(wt, [sys.executable, "-c", script], timeout=2)
    assert not ok and out.startswith("check timed out") and gitops.TAMPERED in out
    assert "evil" not in cfg.read_text(encoding="utf-8")


def test_a_config_that_cannot_be_put_back_still_fails_the_check(tmp_path):
    wt, cfg = _wt(tmp_path)
    script = f"import os; os.remove({str(cfg)!r}); os.mkdir({str(cfg)!r})"     # a folder where the config was
    ok, out = gitops.run_check(wt, [sys.executable, "-c", script])
    assert not ok and gitops.TAMPERED in out and "could not be restored" in out and gitops._GUARDS == {}


def test_jarves_worktree_calls_during_a_run_are_not_taken_for_tampering(tmp_path):
    wt, cfg = _wt(tmp_path)
    repo = cfg.parent.parent
    gitops.git(repo, "remote", "add", "origin", str(repo))
    gitops.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")     # a remote base would set up tracking
    with gitops.config_guard(gitops.guarded_files(wt)) as mark:
        gitops.ensure_integration(tmp_path / "home", str(repo), 2, base="origin/main")
        wt2, br2 = gitops.new_task_worktree(tmp_path / "home", str(repo), 1, 2, 1)
        gitops.remove_worktree(str(repo), wt2, br2)
    assert mark["tampered"] is False


class _Script(runners.Runner):
    name = "fake"

    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self.argv = argv

    def build(self, prompt, cwd, *, model="", resume="", read_only=False, check_argv=None, team=False):
        return runners.Spec(self.argv, None, gitops.child_env())

    def parse(self, stdout, rc):
        return runners.Result(ok=rc == 0, text=stdout)


def test_an_agent_run_with_the_check_rule_cannot_keep_a_planted_entry(tmp_path):
    wt, cfg = _wt(tmp_path)
    before = cfg.read_bytes()
    r = _Script(_append(cfg, PLANT)).run("p", str(wt), check_argv=["python", "-m", "pytest"])
    assert not r.ok and gitops.TAMPERED in r.error and cfg.read_bytes() == before


def test_an_agent_run_with_the_check_rule_outside_a_worktree_is_refused(tmp_path):
    marker = tmp_path / "ran"
    r = _Script(_append(marker, "x")).run("p", str(tmp_path), check_argv=["python", "-m", "pytest"])
    assert not r.ok and r.error.startswith("refused") and not marker.exists()


def test_an_agent_run_without_a_check_command_is_not_guarded(tmp_path):
    r = _Script([sys.executable, "-c", "print('hi')"]).run("p", str(tmp_path), read_only=True)
    assert r.ok and "hi" in r.text


# A check that leaves a detached grandchild (its own session) behind: the grandchild writes its pid, waits for `go`,
# then plants the entry. The parent exits as soon as the pid is written (or, with a 5th argument, waits for that file:
# the run is then cancelled), so the run ends while the grandchild lives.
GRANDCHILD = """import os, sys
open(sys.argv[2] + '.tmp', 'w').write(str(os.getpid()))
os.replace(sys.argv[2] + '.tmp', sys.argv[2])
while not os.path.exists(sys.argv[3]):
    pass
open(sys.argv[1], 'a').write('[filter "x"]\\n\\tclean = evil\\n')
"""
PARENT = """import os, subprocess, sys
subprocess.Popen([sys.executable, *sys.argv[1:5]], start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not os.path.exists(sys.argv[5] if len(sys.argv) > 5 else sys.argv[3]):
    pass
"""


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b")", 1)[1].split()[0] != b"Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="containment is Linux only (#1665)")
@pytest.mark.parametrize("via", ["check", "agent", "agent-cancelled"])
def test_a_detached_grandchild_cannot_write_after_the_run_ends(tmp_path, via):
    wt, cfg = _wt(tmp_path)
    before, pidf, go, child = cfg.read_bytes(), tmp_path / "pid", tmp_path / "go", tmp_path / "child.py"
    child.write_text(GRANDCHILD, encoding="utf-8")
    argv = [sys.executable, "-c", PARENT, str(child), str(cfg), str(pidf), str(go)]
    if via == "check":
        gitops.run_check(wt, argv)
    elif via == "agent":
        _Script(argv).run("p", str(wt), check_argv=["python", "-m", "pytest"])
    else:                                            # the parent never ends; cancel once the grandchild is up
        cancel = threading.Event()
        threading.Thread(target=lambda: (_wait_for(pidf), cancel.set()), daemon=True).start()
        r = _Script([*argv, str(tmp_path / "never")]).run("p", str(wt), check_argv=["x"], cancel=cancel)
        assert r.error == "cancelled"
    pid = int(pidf.read_text(encoding="utf-8"))
    go.touch()                                       # a surviving grandchild would now plant the entry
    assert not _alive(pid) and cfg.read_bytes() == before


def _wait_for(path: Path) -> None:
    while not (path.exists() and path.read_text(encoding="utf-8")):
        threading.Event().wait(0.01)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="containment is Linux only (#1665)")
def test_a_sigterm_right_at_start_still_lets_the_reaper_clean_up():
    """R2: the reaper starts with SIGTERM blocked, so a SIGTERM before its handler exists is handled, not fatal."""
    with gitops.term_blocked():
        p = subprocess.Popen(gitops.contain([sys.executable, "-c", "import time; time.sleep(60)"])[0])
    p.terminate()                                    # long before the reaper's interpreter has started
    assert p.wait(30) == 143                         # handled: the default action would give -15


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="containment is Linux only (#1665)")
def test_a_check_that_exits_124_itself_is_not_reported_as_timed_out(tmp_path):
    wt, _cfg = _wt(tmp_path)
    ok, out = gitops.run_check(wt, [sys.executable, "-c", "raise SystemExit(124)"])
    assert not ok and not out.startswith("check timed out")


def test_the_reaper_and_gitops_agree_on_the_timeout_line():
    from jarves.projectmgr import _reaper
    assert _reaper.TIMEOUT == gitops.REAPER_TIMEOUT


def test_a_write_timed_to_land_during_a_jarves_worktree_call_is_still_undone(tmp_path, monkeypatch):
    wt, cfg = _wt(tmp_path)
    before, real = cfg.read_bytes(), gitops.git

    def git(cwd, *args, **kw):
        if args[:2] == ("worktree", "add"):
            cfg.write_text(cfg.read_text(encoding="utf-8") + PLANT, encoding="utf-8")   # lands during the call
        return real(cwd, *args, **kw)
    monkeypatch.setattr(gitops, "git", git)
    with gitops.config_guard(gitops.guarded_files(wt)) as mark:
        gitops.new_task_worktree(tmp_path / "home", str(cfg.parent.parent), 1, 3, 1)
    assert mark["tampered"] and cfg.read_bytes() == before


def test_an_oversized_file_is_never_read_whole_and_counts_as_a_change(tmp_path, monkeypatch):
    wt, cfg = _wt(tmp_path)
    monkeypatch.setattr(gitops, "SNAP_CAP", 8192)
    before = cfg.read_bytes()
    ok, out = gitops.run_check(wt, _append(cfg, "#" * 20_000))
    assert not ok and gitops.TAMPERED in out and cfg.read_bytes() == before
    assert [f.stat().st_size for f in _rejected(cfg)] == [8192]          # the kept copy is capped too


def test_a_guarded_file_already_too_large_refuses_the_run(tmp_path, monkeypatch):
    wt, cfg = _wt(tmp_path)
    monkeypatch.setattr(gitops, "SNAP_CAP", 8192)
    (cfg.parent / "hooks" / "big").write_bytes(b"x" * 9000)
    marker = tmp_path / "ran"
    ok, out = gitops.run_check(wt, _append(marker, "x"))
    assert not ok and "too large to guard" in out and not marker.exists() and gitops._GUARDS == {}
    r = _Script(_append(marker, "x")).run("p", str(wt), check_argv=["x"])
    assert not r.ok and "too large to guard" in r.error and not marker.exists()


def test_only_the_newest_rejected_copies_are_kept(tmp_path, monkeypatch):
    wt, cfg = _wt(tmp_path)
    monkeypatch.setattr(gitops, "KEEP_REJECTED", 2)
    for word in ("one", "two", "three"):
        assert not gitops.run_check(wt, _append(cfg, f"# {word}\n"))[0]
    texts = "".join(f.read_text(encoding="utf-8") for f in _rejected(cfg))
    assert len(_rejected(cfg)) == 2 and "one" not in texts and "three" in texts
