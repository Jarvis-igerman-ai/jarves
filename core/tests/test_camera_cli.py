"""Camera status reads authoritative saved switches without ever opening a sensor."""
import json

import pytest

from jarves.camera import __main__ as cli
from jarves.camera import backends
from jarves.config import Config
from jarves.memory import MemoryStore


@pytest.mark.parametrize("available", [True, False])
def test_status_uses_saved_feature_switches_without_capture(home, monkeypatch, capsys, available):
    # The old camera-local setting deliberately disagrees with the maintained feature backend.
    memory = MemoryStore(home / "memory.db")
    memory.set_setting("feature:camera", "off")
    memory.set_setting("feature:face_recognition", "on")
    memory.close()
    saved = {"camera": True, "face_recognition": False, "camera_when_away": True}
    (home / "features.json").write_text(json.dumps({"enabled": saved}), encoding="utf-8")
    monkeypatch.setattr(cli, "load_config", lambda: (Config(), home / "config.toml"))
    monkeypatch.setattr(cli, "probe", lambda _: backends.Probe(available, "fake", "sensorless test probe"))

    def forbidden_open(_):
        pytest.fail("status must not open the camera")
    monkeypatch.setattr(backends.OpenCVCamera, "open", forbidden_open)
    monkeypatch.setattr(backends.Picamera2Camera, "open", forbidden_open)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert ("camera: found" if available else "camera: NOT available") in out
    assert "switch backend: features" in out
    rows = {row.split()[0]: row.split()[1] for row in out.splitlines()[1:4]}
    assert rows == {"camera": "ON", "face_recognition": "off", "camera_when_away": "ON"}


def test_status_preserves_local_fallback_switches(home, monkeypatch, capsys):
    from jarves.camera import switches
    memory = MemoryStore(home / "memory.db")
    memory.set_setting("feature:camera", "on")
    memory.close()
    monkeypatch.setattr(switches, "_features_module", lambda: None)
    monkeypatch.setattr(cli, "load_config", lambda: (Config(), home / "config.toml"))
    monkeypatch.setattr(cli, "probe", lambda _: backends.Probe(False, "none", "no sensor"))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "switch backend: local" in out
    assert out.splitlines()[1].split()[:2] == ["camera", "ON"]
