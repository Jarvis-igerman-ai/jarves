"""#524 revert --dry-run verifies the bridge and dependency backups; #525 a failed revert is shown, not hidden.
Fake install tree, fake hooks; never touches a real install."""
from __future__ import annotations

import json

import pytest

from jarves import hotfix as hf
from jarves import hotfix_slice2 as s2
from test_hotfix_slice2 import BRIDGE_NEW, PIN, Fake, dep_world, opts, source, world  # noqa: F401  (world is a fixture)


def _bridge_hotfix(w):
    exe = w.tmp / "built.exe"
    exe.write_bytes(BRIDGE_NEW)
    hf.apply(w.inst, source(w, ["packaging/bridge_pin.json"], pin=PIN()), w.home, Fake().hooks(), s2opts=opts(w, bridge_exe=exe))
    return next((w.home / "hotfix").iterdir())


def _dep_hotfix(w):
    base, target = dep_world(w)

    def runner(cmd):
        if "--version" in cmd:
            return 1, "no pip"
        return 0, "ok"

    hf.apply(w.inst, source(w, ["core/uv.lock"], base_lock=base, target_lock=target), w.home, Fake().hooks(),
             s2opts=opts(w, runner=runner))
    return next((w.home / "hotfix").iterdir())


def test_dry_run_checks_the_saved_bridge_exe(world):
    d = _bridge_hotfix(world)
    r = hf.revert(world.inst, world.home, Fake().hooks(), dry_run=True)
    assert r["status"] == "dry-run" and "bridge" in r["files"]
    (d / "bridge_original" / "exe").write_bytes(b"tampered")
    with pytest.raises(hf.HotfixError) as e:
        hf.revert(world.inst, world.home, Fake().hooks(), dry_run=True)
    assert e.value.code == "HOTFIX_ORIGINAL_BAD" and "bridge" in e.value.message


def test_dry_run_checks_the_saved_dependency_backups(world):
    d = _dep_hotfix(world)
    assert hf.revert(world.inst, world.home, Fake().hooks(), dry_run=True)["status"] == "dry-run"
    saved = next(p for p in (d / "deps_originals").rglob("*") if p.is_file())
    saved.write_bytes(b"tampered")
    with pytest.raises(hf.HotfixError) as e:
        hf.revert(world.inst, world.home, Fake().hooks(), dry_run=True)
    assert e.value.code == "HOTFIX_ORIGINAL_BAD"


def test_failed_revert_marks_revert_failed_and_restarts_the_core(world, monkeypatch):
    d = _dep_hotfix(world)

    def boom(*a, **k):
        raise OSError("locked file")

    monkeypatch.setattr(s2, "revert_deps", boom)
    f = Fake(running=True)
    with pytest.raises(OSError):
        hf.revert(world.inst, world.home, f.hooks())
    assert f.calls == ["stop", "start"]                                  # the Core is back
    man = json.loads((d / "manifest.json").read_text())
    assert man["status"] == "revert-failed" and "locked file" in man["error"]
    assert hf.status(world.inst, world.home)["latest"]["status"] == "revert-failed"


def test_revert_failure_before_any_write_keeps_the_status_and_restarts_the_bridge(world, monkeypatch):
    """#568 items 3 and 4."""
    d = _bridge_hotfix(world)
    def boom(*a, **k):
        raise OSError("exe locked")                                          # fails after the bridge was stopped
    monkeypatch.setattr(s2, "bridge_revert", boom)
    f = Fake(running=False, bridge_running=True)
    with pytest.raises(OSError):
        hf.revert(world.inst, world.home, f.hooks())
    assert "bstop" in f.calls and f.calls.count("bstart") >= 1 and f.bridge is True       # the bridge was brought back
    assert json.loads((d / "manifest.json").read_text())["status"] == "applied"            # the first write failed: nothing changed
    # the failing step had started, so it IS marked; now a failure before any step:
    d2 = d
    m = json.loads((d2 / "manifest.json").read_text())
    m["status"] = "applied"
    m.pop("error", None)
    (d2 / "manifest.json").write_text(json.dumps(m))
    m["bridge"] = None
    monkeypatch.undo()
    m["replaced"] = [{"path": "a.py", "sha256_original": "0" * 64}]          # verification fails before any write
    (d2 / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(hf.HotfixError):
        hf.revert(world.inst, world.home, Fake().hooks())
    assert json.loads((d2 / "manifest.json").read_text())["status"] == "applied"


def test_revert_failure_after_the_bridge_exe_was_put_back_is_marked(world, monkeypatch):
    """Review of #588: bridge_revert is two writes (exe, then sidecar); a failure after the exe write is half done."""
    d = _bridge_hotfix(world)
    real = s2._atomic_replace_file
    n = []

    def second_fails(src, dst):
        n.append(1)
        if len(n) > 1:
            raise OSError("sidecar locked")
        real(src, dst)

    monkeypatch.setattr(s2, "_atomic_replace_file", second_fails)
    man = json.loads((d / "manifest.json").read_text())
    if not (man["bridge"].get("sha256_original") and man["bridge"].get("had_sidecar")):
        pytest.skip("the fixture bridge has no original exe and sidecar")
    with pytest.raises(OSError):
        hf.revert(world.inst, world.home, Fake(running=False, bridge_running=True).hooks())
    assert json.loads((d / "manifest.json").read_text())["status"] == "revert-failed"
