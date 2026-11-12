"""#293: `jarvis update install` records what ran BEFORE it stops anything and hands it to setup."""
import json

from jarves import runmarks, updates


def test_installer_args_carry_was_running():
    a = updates.installer_args("x.exe", silent=True, restart=True,
                               was_running={"core": True, "face": True, "dashboard": ["--lan", "--mobile"]})
    assert "/RESTARTJARVES=1" in a
    assert "/WASRUNNING=core,face,dashboard:lan:mobile" in a


def test_nothing_ran_is_explicit():
    a = updates.installer_args("x.exe", silent=True, restart=True, was_running={"core": False})
    assert "/WASRUNNING=none" in a


def test_unknown_adds_no_param():
    assert not [x for x in updates.installer_args("x.exe", silent=True, restart=True) if x.startswith("/WASRUNNING")]


def test_dashboard_flags_whitelisted():
    s = runmarks.was_running_arg({"core": False, "dashboard": ["--lan", "--evil;calc"]})
    assert s == "dashboard:lan"


def test_detect_running(tmp_path):
    import os
    (tmp_path / runmarks.DASHBOARD_FILE).write_text(json.dumps({"pid": os.getpid(), "flags": ["--lan", "--x"]}))
    r = runmarks.detect_running(tmp_path, core_up=True, face_up=lambda: False)
    assert r == {"core": True, "face": False, "dashboard": ["--lan"]}
    (tmp_path / runmarks.DASHBOARD_FILE).write_text(json.dumps({"pid": 2 ** 22 + 12345, "flags": ["--lan"]}))
    assert runmarks.detect_running(tmp_path, core_up=False, face_up=lambda: False) == \
        {"core": False, "face": False, "dashboard": None}


def test_pending_record_gets_was_running(tmp_path):
    updates._write_json(tmp_path / "updates" / updates.PENDING_FILE, {"from": "1", "to": "2", "state": "installing"})
    updates.record_was_running(tmp_path, {"core": True, "face": False, "dashboard": None})
    d = json.loads((tmp_path / "updates" / updates.PENDING_FILE).read_text())
    assert d["was_running"] == {"core": True, "face": False, "dashboard": None} and d["state"] == "installing"


def test_dashboard_install_hands_setup_what_ran(tmp_path, monkeypatch):
    """#166: the in-app (dashboard) install passes /WASRUNNING= so only what ran is restarted."""
    seen = {}
    monkeypatch.setattr(updates, "_checker", lambda ctx: type("C", (), {
        "prepare_install": lambda self, approve: {"installer": "x.exe", "version": "9", "signed": True}})())
    monkeypatch.setattr(runmarks, "detect_running",
                        lambda home, core_up, face_up=None: {"core": core_up, "face": False, "dashboard": None})
    monkeypatch.setattr(updates, "launch_installer", lambda *a, **k: seen.update(k))
    ctx = type("X", (), {"home": tmp_path})()
    code, _ = updates.api_updates_install(ctx, None, {"approve": True, "silent": True})
    assert code == 200 and seen["was_running"] == {"core": True, "face": False, "dashboard": None}
    seen.clear()
    updates.api_updates_install(ctx, None, {"approve": True})
    assert seen["was_running"] is None and seen["restart"] is False
