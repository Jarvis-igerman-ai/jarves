"""#261 installer recovery: the new program is dead after a schema bump -> previous program + verified data come back,
only after approval. Fake process, fake data restore, frozen clock; nothing real is started or touched."""
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[2] / "packaging"
spec = importlib.util.spec_from_file_location("installer_state", PKG / "installer_state.py")
inst = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inst)

DEAD = lambda *a, **k: SimpleNamespace(returncode=1)          # noqa: E731  fake process: the new python crashes
ALIVE = lambda *a, **k: SimpleNamespace(returncode=0)         # noqa: E731


def tree(tmp_path, health=None, previous=True):
    app, home = tmp_path / "app", tmp_path / "data"
    for part in ("python", "face", "bridge"):
        (app / part).mkdir(parents=True)
        (app / part / "v.txt").write_text("new")
    (app / "jarvis.cmd").write_text("new")
    if previous:
        for part in ("python", "face", "bridge"):
            (app / "rollback" / part).mkdir(parents=True)
            (app / "rollback" / part / "v.txt").write_text("old")
        (app / "rollback" / "jarvis.cmd").write_text("old")
    (home / "updates").mkdir(parents=True)
    if health:
        (home / "updates" / "health.json").write_text(json.dumps({"status": health}))
    state = app / "install-state.json"
    state.write_text(json.dumps({"home": str(home.resolve())}))
    return app, home, state


def test_dead_program_is_detected_by_a_failing_or_missing_process(tmp_path):
    app, _, _ = tree(tmp_path)
    assert inst.program_dead(app, run=DEAD) and not inst.program_dead(app, run=ALIVE)

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)
    assert inst.program_dead(app, run=boom)
    assert inst.program_dead(app, run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))


def test_nothing_is_changed_without_approval(tmp_path):
    app, home, state = tree(tmp_path, health="health_failed")
    calls = []
    r = inst.recover_program(app, home, state, run=DEAD, restore=lambda *a: calls.append(a))
    assert r["status"] == "needs_approval" and "approve" in r["message"].lower() and not calls
    assert (app / "python" / "v.txt").read_text() == "new" and (app / "rollback" / "python").is_dir()


def test_approved_recovery_restores_data_first_then_swaps_the_program(tmp_path):
    app, home, state = tree(tmp_path, health="health_failed")
    order = []
    r = inst.recover_program(app, home, state, approved=True, run=DEAD, stamp=lambda: 111,
                             restore=lambda h, s: order.append(("data", (app / "python" / "v.txt").read_text())))
    assert order == [("data", "new")]                              # data restored while the program was still the new one
    assert r["status"] == "recovered" and set(r["parts"]) == {"python", "face", "bridge", "jarvis.cmd"}
    for part in ("python", "face", "bridge"):
        assert (app / part / "v.txt").read_text() == "old"
        assert (app / "failed-upgrade-recover-111" / part / "v.txt").read_text() == "new"   # kept, never deleted
    assert (app / "jarvis.cmd").read_text() == "old"
    assert json.loads((home / "updates" / "health.json").read_text())["status"] == "rolled_back"
    assert "back" in r["message"]


def test_a_backup_that_does_not_verify_changes_nothing(tmp_path):
    app, home, state = tree(tmp_path, health="health_failed")

    def bad(*a):
        raise ValueError("rollback backup no longer verifies")
    with pytest.raises(ValueError):
        inst.recover_program(app, home, state, approved=True, run=DEAD, restore=bad)
    assert (app / "python" / "v.txt").read_text() == "new" and not list(app.glob("failed-upgrade-*"))


def test_a_healthy_program_is_left_alone(tmp_path):
    app, home, state = tree(tmp_path)
    assert inst.recover_program(app, home, state, approved=True, run=ALIVE)["status"] == "not_needed"
    assert (app / "python" / "v.txt").read_text() == "new"


def test_health_failed_marker_triggers_even_if_the_probe_passes_and_no_previous_program_is_refused(tmp_path):
    app, home, state = tree(tmp_path, health="health_failed")
    assert inst.recover_program(app, home, state, run=ALIVE)["status"] == "needs_approval"
    app2, home2, state2 = tree(tmp_path / "b", previous=False)
    assert inst.recover_program(app2, home2, state2, approved=True, run=DEAD)["status"] == "no_previous_program"


def test_cli_exit_codes_and_no_approval_by_default(tmp_path, monkeypatch):
    app, home, state = tree(tmp_path, health="health_failed")
    monkeypatch.setattr(inst, "program_dead", lambda *a, **k: True)
    assert inst.main(["recover-program", "--home", str(home), "--state", str(state), "--app", str(app)]) == 6
    assert (app / "python" / "v.txt").read_text() == "new"
