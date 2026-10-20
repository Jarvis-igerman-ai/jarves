"""Feature-on prerequisites, all network/install/hardware boundaries mocked."""
from argparse import Namespace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from jarves import cli, feature_setup as setup, features
from jarves.config import Config
from jarves.runtime.lock import InstanceLock


@pytest.fixture
def camera(monkeypatch, tmp_path):
    state = {"installed": False, "hardware": True, "installs": []}
    monkeypatch.setattr(setup, "_present", lambda module: state["installed"] if module == "cv2" else True)
    def install(extra):
        state["installs"].append(extra)
        state["installed"] = True
    monkeypatch.setattr(setup, "_install_extra", install)
    monkeypatch.setattr(features.CATALOG["camera"], "probe", lambda c:
                        (state["installed"] and state["hardware"], "camera hardware missing"))
    f = features.Features(Config(), tmp_path)
    monkeypatch.setattr(cli, "_cli_features", lambda cfg, home: f)
    return state, f


def test_install_recheck_then_enable(camera, tmp_path):
    state, f = camera
    assert not f.available("camera")[0]  # cache a failure; successful installation must invalidate it
    assert cli.cmd_features(Namespace(action="on", id="camera"), Config(), tmp_path) == 0
    assert state["installs"] == ["camera"]
    assert f.active("camera")


def test_existing_dependencies_no_install(camera, tmp_path):
    state, f = camera
    state["installed"] = True
    assert cli.cmd_features(Namespace(action="on", id="camera"), Config(), tmp_path) == 0
    assert state["installs"] == []


@pytest.mark.parametrize("failure", [RuntimeError("installer failed"), KeyboardInterrupt(),
                                     subprocess.TimeoutExpired("uv", 300)])
def test_failure_or_cancellation_still_saves_switch_as_unavailable(camera, monkeypatch, tmp_path, failure, capsys):
    _, f = camera
    def fail(extra):
        raise failure
    monkeypatch.setattr(setup, "_install_extra", fail)
    assert cli.cmd_features(Namespace(action="on", id="camera"), Config(), tmp_path) == 0
    assert f.enabled("camera") and not f.active("camera")      # O-02: saved ON, shown unavailable
    assert "unavailable (" in capsys.readouterr().out
    lock = InstanceLock(tmp_path)
    lock.acquire()  # failure/cancel always releases the installation guard
    lock.release()


def test_already_enabled_repairs_missing_software(camera, tmp_path):
    state, f = camera
    f.set("camera", True)
    assert cli.cmd_features(Namespace(action="on", id="camera"), Config(), tmp_path) == 0
    assert state["installs"] == ["camera"] and f.active("camera")


def test_hardware_blocker_saves_enabled_but_unavailable(camera, tmp_path, capsys):
    state, f = camera
    state.update(installed=True, hardware=False)
    assert cli.cmd_features(Namespace(action="on", id="camera"), Config(), tmp_path) == 0
    assert f.enabled("camera") and not f.active("camera") and state["installs"] == []
    assert "camera: unavailable (camera hardware missing)" in capsys.readouterr().out


def test_already_enabled_failure_stays_enabled(camera, tmp_path):
    state, f = camera
    f.set("camera", True)
    state.update(installed=True, hardware=False)
    assert cli.cmd_features(Namespace(action="on", id="camera"), Config(), tmp_path) == 0
    assert f.enabled("camera") and not f.active("camera")


@pytest.mark.parametrize("action", ["off", "reset", "list"])
def test_other_actions_never_install(camera, monkeypatch, tmp_path, action):
    monkeypatch.setattr(setup, "prepare", lambda *args: pytest.fail("unexpected install"))
    assert cli.cmd_features(Namespace(action=action, id="camera"), Config(), tmp_path) == 0


def test_running_core_blocks_environment_mutation(camera, tmp_path):
    state, f = camera
    lock = InstanceLock(tmp_path)
    lock.acquire("test Core")
    try:
        ok, detail = setup.prepare("camera", f, tmp_path)
        assert not ok and "jarvis stop" in detail
        assert state["installs"] == [] and not f.enabled("camera")
    finally:
        lock.release()


def test_locked_install_keeps_extras_and_project(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "_source_core", lambda: tmp_path)
    monkeypatch.setattr(setup.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(setup.shutil, "which", lambda name: "uv")
    calls = []
    monkeypatch.setattr(setup.subprocess, "run", lambda args, **kw: calls.append((args, kw)))
    setup._install_extra("camera")
    args, options = calls[0]
    assert args == ["uv", "sync", "--directory", str(tmp_path), "--frozen", "--inexact",
                    "--no-install-project", "--extra", "camera"]
    assert options == {"check": True, "timeout": 300}


def test_installed_build_reports_repair(monkeypatch):
    monkeypatch.setattr(setup, "_source_core", lambda: None)
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: pytest.fail("installed app ran uv"))
    with pytest.raises(RuntimeError, match="repair/update"):
        setup._install_extra("camera")


def test_wake_missing_audio_installs_voice(monkeypatch):
    monkeypatch.setattr(setup, "_present", lambda name: name != "sounddevice")
    assert setup._missing_extra("wake_word") == "voice"
    assert setup._missing_extra("gmail_send") is None


def test_nonsoftware_blocker_never_installs(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "_install_extra", lambda *args: pytest.fail("account install"))
    f = SimpleNamespace(invalidate=lambda: None, available=lambda fid: (False, "Google account not connected"))
    ok, detail = setup.prepare("gmail_read", f, tmp_path)
    assert not ok and "Google account not connected" in detail


def test_face_model_install_and_recheck(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "_missing_extra", lambda fid: None)
    monkeypatch.setattr(setup, "_model_valid", lambda path: path.exists())
    monkeypatch.setattr(setup, "_download_model", lambda path: (path.parent.mkdir(), path.write_bytes(b"model")))
    f = SimpleNamespace(invalidate=lambda: None, available=lambda fid: (True, ""))
    assert setup.prepare("face_recognition", f, tmp_path) == (True, "")


@pytest.mark.parametrize("valid", [True, False])
def test_model_hash_atomicity_and_cleanup(monkeypatch, tmp_path, valid):
    data = b"official asset"
    monkeypatch.setattr(setup, "SFACE_BYTES", len(data))
    monkeypatch.setattr(setup, "SFACE_SHA256", setup.hashlib.sha256(data if valid else b"wrong").hexdigest())
    seen = []
    monkeypatch.setattr(setup, "urlopen", lambda url, **kw: (seen.append((url, kw)) or BytesIO(data)))
    path = tmp_path / setup.SFACE_FILE
    path.write_bytes(b"old")
    if valid:
        setup._download_model(path)
        assert path.read_bytes() == data
    else:
        with pytest.raises(RuntimeError, match="SHA256"):
            setup._download_model(path)
        assert path.read_bytes() == b"old"
    assert seen == [(setup.SFACE_URL, {"timeout": 20})]
    assert list(tmp_path.glob(".sface-*")) == []


def test_windows_camera_probe_is_sensorless(monkeypatch, tmp_path):
    from jarves.camera import backends
    from jarves.camera import probe as camera_probe
    monkeypatch.setattr(features.sys, "platform", "win32")
    monkeypatch.setattr(camera_probe, "probe", lambda backend: backends.Probe(True, "opencv", "webcam interface found"))
    monkeypatch.setattr(backends.OpenCVCamera, "open", lambda self: pytest.fail("opened sensor"))
    ctx = features.ProbeCtx(Config(), tmp_path, lambda: {}, lambda: None)
    assert features._probe_camera(ctx) == (True, "")
    ctx.components = lambda: {"camera": {"status": "unavailable", "detail": "OS permission denied"}}
    assert features._probe_camera(ctx) == (False, "OS permission denied")


def test_stopped_core_snapshot_is_not_live(monkeypatch, tmp_path):
    from jarves import health
    from jarves.runtime import lock
    monkeypatch.setattr(lock, "held", lambda home: False)
    monkeypatch.setattr(health, "read_snapshot", lambda path: pytest.fail("used stopped snapshot"))
    assert cli._cli_features(Config(), tmp_path).ctx.components() == {}


@pytest.mark.parametrize("fid,field,command", [
    ("google_calendar", "calendar", "jarvis google login"),
    ("calendar_write", "calendar_write", "jarvis google login --calendar-write"),
    ("gmail_read", "gmail_read", "jarvis google login"),
    ("gmail_send", "gmail_send", "jarvis google login"),
])
def test_google_probe_requires_its_own_scope(monkeypatch, tmp_path, fid, field, command):
    from jarves import google_auth
    granted = {"connected": True, "needs_reconnect": False, "calendar": True, "calendar_write": True,
               "gmail_read": True, "gmail_send": True}
    monkeypatch.setattr(google_auth, "GoogleAuth", lambda home: SimpleNamespace(status=lambda: granted))
    # No registered skill list while the Core is stopped: token scopes must still control readiness.
    ctx = features.ProbeCtx(Config(), tmp_path, lambda: {}, lambda: None)
    probe = features.CATALOG[fid].probe
    assert probe(ctx) == (True, "")
    granted[field] = False
    ok, detail = probe(ctx)
    assert not ok and "consent required" in detail and command in detail


def test_readonly_google_token_cannot_activate_writes(monkeypatch, tmp_path):
    from jarves import google_auth
    granted = {"connected": True, "needs_reconnect": False, "calendar": True, "calendar_write": False,
               "gmail_read": True, "gmail_send": False}
    monkeypatch.setattr(google_auth, "GoogleAuth", lambda home: SimpleNamespace(status=lambda: granted))
    f = features.Features(Config(), tmp_path)
    assert f.active("google_calendar") and f.active("gmail_read")
    assert setup.prepare("calendar_write", f, tmp_path)[0] is False
    assert setup.prepare("gmail_send", f, tmp_path)[0] is False
    assert not f.enabled("calendar_write") and not f.enabled("gmail_send")
