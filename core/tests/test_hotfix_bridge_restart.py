"""#466: the running bridge process vs the pinned exe, and the optional --restart-bridge step. Fake image lookup, fake hooks,
frozen clock: no real process is looked at, stopped or killed."""
from __future__ import annotations

import json

import pytest

from jarves import hotfix as hf


def write(root, rel, data):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(hf, "_now_id", lambda: "20261026-120000")


@pytest.fixture
def world(tmp_path):
    inst = tmp_path / "site-packages" / "jarves"
    src = tmp_path / "src" / "jarves"
    for r in (inst, src):
        write(r, "__init__.py", "__version__='4.5.0'\n")
        write(r, "a.py", "A=1\n")
    write(inst, hf.BUILD_INFO_NAME, json.dumps({"version": "4.5.0", "commit": "abc123"}))
    write(tmp_path, "pinned/whatsapp_bridge.exe", b"NEW")
    write(tmp_path, "stale/whatsapp_bridge.exe", b"OLD")
    return type("W", (), dict(inst=inst, src=src, home=tmp_path / "home", tmp=tmp_path))


class Bridge:
    """A fake bridge process: pid + image; stop/start swap it like Core's own path would."""
    def __init__(self, w, image="stale/whatsapp_bridge.exe", fail_start=False):
        self.w, self.pid, self.image, self.up, self.fail_start, self.calls = w, 100, w.tmp / image, True, fail_start, []

    def state(self):
        return hf.bridge_process_state(self.w.home, pid_of=lambda: self.pid if self.up else 0,
                                       image_of=lambda pid: str(self.image) if self.up and pid == self.pid else None,
                                       expected=self.w.tmp / "pinned" / "whatsapp_bridge.exe")

    def hooks(self, doctor=(0, 0)):
        rcs = list(doctor)

        def stop():
            self.calls.append("bstop")
            self.up = False

        def start():
            self.calls.append("bstart")
            if self.fail_start:
                return
            self.up, self.pid, self.image = True, self.pid + 1, self.w.tmp / "pinned" / "whatsapp_bridge.exe"

        return hf.Hooks(lambda: False, lambda: None, lambda: None, lambda: "b.zip", lambda: rcs.pop(0), log=lambda s: None,
                        bridge_running=lambda: self.up, stop_bridge=stop, start_bridge=start, bridge_state=self.state)


def src_of(w, changes=None):
    return hf.Source(w.src, changes, "def456")


def test_state_detects_stale_ok_and_not_running(world):
    b = Bridge(world)
    st = b.state()
    assert st["state"] == "restart-needed" and st["pid"] == 100 and st["running_sha256"] != st["expected_sha256"]
    assert "bridge restart needed" in hf.bridge_state_lines(st)[0]
    b.image = world.tmp / "pinned" / "whatsapp_bridge.exe"
    assert b.state()["state"] == "ok" and "bridge restart needed" not in hf.bridge_state_lines(b.state())[0]
    b.up = False
    assert b.state()["state"] == "not-running" and hf.bridge_state_lines(b.state()) == []


def test_status_reports_restart_needed(world):
    b = Bridge(world)
    s = hf.status(world.inst, world.home, bridge_state=b.state)
    assert s["bridge_process"]["state"] == "restart-needed"


def test_apply_without_flag_only_reports(world):
    b = Bridge(world)
    write(world.src, "a.py", "A=2\n")
    m = hf.apply(world.inst, src_of(world, ["core/src/jarves/a.py"]), world.home, b.hooks())
    assert m["bridge_restart_needed"] is True and b.calls == [] and b.pid == 100


def test_apply_with_flag_restarts_only_the_owned_bridge_after_files(world):
    b = Bridge(world)
    write(world.src, "a.py", "A=2\n")
    m = hf.apply(world.inst, src_of(world, ["core/src/jarves/a.py"]), world.home, b.hooks(), restart_bridge=True)
    assert b.calls == ["bstop", "bstart"] and m["bridge_restart"]["restarted"] and m["bridge_restart"]["new_pid"] == 101
    assert m["bridge_restart_needed"] is False and m["status"] == "applied"


def test_failed_restart_reverts_files_and_brings_bridge_back(world):
    b = Bridge(world, fail_start=True)
    write(world.src, "a.py", "A=2\n")
    with pytest.raises(hf.HotfixError) as e:
        hf.apply(world.inst, src_of(world, ["core/src/jarves/a.py"]), world.home, b.hooks(), restart_bridge=True)
    assert e.value.code == "HOTFIX_REVERTED"
    assert (world.inst / "a.py").read_text() == "A=1\n"
    assert b.calls == ["bstop", "bstart", "bstart"]            # the revert starts it again through Core's own path


def test_nothing_to_change_still_restarts_when_asked(world):
    b = Bridge(world)
    r = hf.apply(world.inst, src_of(world, []), world.home, b.hooks(), restart_bridge=True)
    assert r["status"] == "nothing" and r["bridge_restart"]["restarted"] and b.calls == ["bstop", "bstart"]


def test_restart_refuses_when_process_changes_between_checks(world):
    b = Bridge(world)
    seen = iter([b.state(), {**b.state(), "pid": 999}])
    h = b.hooks()
    h.bridge_state = lambda: next(seen)
    with pytest.raises(hf.HotfixError):
        hf.restart_owned_bridge(h)
    assert b.calls == []


def test_ok_bridge_is_never_touched(world):
    b = Bridge(world, image="pinned/whatsapp_bridge.exe")
    assert hf.restart_owned_bridge(b.hooks()) == {"restarted": False, "state": "ok"}
    assert b.calls == []


def test_hotfix_has_no_command_line_kill():
    import inspect
    text = inspect.getsource(hf)
    for bad in ("taskkill", "pkill", "killall", "os.kill", ".kill()", ".terminate()"):
        assert bad not in text


def test_dry_run_never_stops_or_starts_the_bridge(world):
    b = Bridge(world)
    r = hf.apply(world.inst, src_of(world, []), world.home, b.hooks(), dry_run=True, restart_bridge=True)
    assert r["status"] == "dry-run" and b.calls == [] and b.pid == 100 and "bridge_restart" not in r


def test_no_restart_flag_is_honoured_when_nothing_changes(world):
    b = Bridge(world)
    r = hf.apply(world.inst, src_of(world, []), world.home, b.hooks(), restart=False, restart_bridge=True)
    assert r["status"] == "nothing" and b.calls == [] and "bridge_restart" not in r


def test_reused_pid_of_another_program_is_not_the_bridge(world):
    b = Bridge(world)
    write(world.tmp, "other/notepad.exe", b"X")
    b.image = world.tmp / "other" / "notepad.exe"
    assert b.state()["state"] == "not-running" and hf.restart_owned_bridge(b.hooks())["restarted"] is False
    assert b.calls == []
