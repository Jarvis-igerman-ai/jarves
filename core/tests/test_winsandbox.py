"""V4 package J (#17): restricted-token / Low-integrity launch of helper and Skill children, on top of the Job Object
limits of isolation.py. Real but harmless child processes only (print / try to write one temp file). Windows-specific
cases carry `windows_only` (deselected, never skipped, elsewhere); the portable cases (default off, fail-closed
contract, status text) run everywhere."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarves import isolation, winsandbox
from jarves.config import Config
from jarves.skills import generated as gen
from jarves.skills import helper as hp


@pytest.fixture(autouse=True)
def flag_off():
    isolation.set_restricted_token(False)
    isolation.reset_status()
    yield
    isolation.set_restricted_token(False)
    isolation.reset_status()


def run_child(code: str, cwd, timeout=60) -> tuple[int, str]:
    p = winsandbox.spawn([sys.executable, "-c", code], cwd=str(cwd), env=hp.child_env(), stdin="null",
                         stderr="stdout")
    out = p.stdout.read().decode("utf-8", "replace")
    return winsandbox.wait_exit(p, timeout), out


def test_default_is_the_current_behaviour():
    assert Config().helper.restricted_token is False
    assert isolation.restricted_token_enabled() is False
    assert isolation.mechanism() in ("Windows Job Object", "POSIX rlimits + own session")
    assert winsandbox.status(False)["integrity"] == "inherited"
    isolation.set_restricted_token(True)
    assert isolation.restricted_token_enabled() is winsandbox.available()          # only ever on where it can work
    assert winsandbox.status(True)["enabled"] is winsandbox.available()


def test_status_row_names_the_token_only_when_it_is_on():
    isolation.set_restricted_token(True)
    assert ("restricted token" in isolation.mechanism()) is (sys.platform == "win32")


@pytest.mark.linux_only
def test_restricted_launch_is_refused_off_windows_not_run_unrestricted():
    with pytest.raises(winsandbox.RestrictedLaunchError):
        winsandbox.spawn([sys.executable, "-c", "print(1)"], cwd=None, env=None)


@pytest.mark.windows_only
def test_child_runs_at_low_integrity_and_this_process_does_not(tmp_path):
    assert winsandbox.integrity_of_current_process() in ("medium", "high")
    src = str(Path(winsandbox.__file__).parents[1])
    code = f"import sys; sys.path.insert(0, {src!r}); from jarves import winsandbox; " \
           "print(winsandbox.integrity_of_current_process())"
    rc, out = run_child(code, tmp_path)
    assert rc == 0 and out.strip() == "low"


@pytest.mark.windows_only
def test_low_child_cannot_write_where_a_normal_child_can(tmp_path):
    target = tmp_path / "owner_file.txt"
    code = f"open({str(target)!r}, 'w').write('x'); print('WROTE')"
    normal = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert "WROTE" in normal.stdout and target.exists()
    target.unlink()
    rc, out = run_child(code, tmp_path)
    assert "WROTE" not in out and "PermissionError" in out and rc != 0 and not target.exists()


@pytest.mark.windows_only
def test_low_child_still_reads_pipes_and_exit_code(tmp_path):
    (tmp_path / "in.txt").write_text("hello", encoding="utf-8")
    rc, out = run_child("print(open('in.txt').read()); raise SystemExit(7)", tmp_path)
    assert out.strip() == "hello" and rc == 7


@pytest.mark.windows_only
def test_stdin_pipe_roundtrip(tmp_path):
    p = winsandbox.spawn([sys.executable, "-c", "print(input()[::-1])"], cwd=str(tmp_path), env=hp.child_env(),
                         stdin="pipe")
    p.stdin.write(b"abc\n")
    p.stdin.flush()
    assert p.stdout.readline().strip() == b"cba"
    assert winsandbox.wait_exit(p, 60) == 0


@pytest.mark.windows_only
def test_kill_and_poll(tmp_path):
    p = winsandbox.spawn([sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(tmp_path), env=hp.child_env())
    assert p.poll() is None
    p.kill()
    assert p.wait(30) == 1 and p.poll() == 1


@pytest.mark.windows_only
def test_helper_runs_through_job_object_and_restricted_token(tmp_path):
    isolation.set_restricted_token(True)
    argv = [sys.executable, "-c", "print(42)"]
    prop = hp.Proposal(command=subprocess.list2cmdline(argv), cwd=tmp_path, argv=argv, level="normal")
    res = hp.run_blocking(prop, 60, tmp_path / "out.log")
    assert res.exit_code == 0 and "42" in res.tail
    assert res.isolation == "active" and "restricted token" in res.isolation_reason


@pytest.mark.windows_only
def test_helper_shell_command_cannot_write_to_a_normal_folder_when_restricted(tmp_path):
    target = tmp_path / "made_by_helper.txt"
    prop = hp.Proposal(command=f'echo hi> "{target}"', cwd=tmp_path, argv=None, level="normal")
    isolation.set_restricted_token(False)
    assert hp.run_blocking(prop, 60, tmp_path / "a.log").exit_code == 0 and target.exists()
    target.unlink()
    isolation.set_restricted_token(True)
    res = hp.run_blocking(prop, 60, tmp_path / "b.log")
    assert res.exit_code != 0 and not target.exists()


@pytest.mark.windows_only
async def test_generated_skill_runs_restricted_and_fails_closed(monkeypatch):
    isolation.set_restricted_token(True)
    code = "def ok_run(args, ctx):\n    return 7\n"

    async def broker(op, a):
        raise PermissionError("no")
    r = await gen.run_sandboxed(code, "ok_run", {}, broker)
    assert r["ok"] is True, r

    def boom(*a, **k):
        raise winsandbox.RestrictedLaunchError(5, "denied")
    monkeypatch.setattr(winsandbox, "spawn", boom)
    r = await gen.run_sandboxed(code, "ok_run", {}, broker)
    assert r["ok"] is False and "not run" in r["error"]
