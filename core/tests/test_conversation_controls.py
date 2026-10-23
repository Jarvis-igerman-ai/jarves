"""Owner conversation regressions: real local control link, fake audio, no cloud or sensors."""
import asyncio
from types import SimpleNamespace

import pytest

from conftest import open_face, until
from jarves import cli
from jarves.appearance import Appearance
from jarves.brain import match_intent
from jarves.runtime import read_state
from jarves.voice.audio import StreamingAudio
from test_runtime import make_core


@pytest.mark.parametrize("said", ["mute yourself", "mute your microphone", "stop hearing me",
                                  "אל תקשיב לי", "תעשה מיוט לעצמך"])
def test_explicit_microphone_privacy_is_not_music_mute(said):
    assert match_intent(said).tool == "mute_microphone"
    other = match_intent("mute")
    assert other is None or other.tool != "mute_microphone"


async def test_voice_mute_then_cli_unmute_uses_live_runtime(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    stopped = []

    class Live:
        _tools_running = 1
        audio_io = StreamingAudio()

        async def stop(self):
            stopped.append(True)

    try:
        core.live = Live()
        result = await core.registry.call("mute_microphone", {}, origin="model")
        assert result.ok and result.data["muted"] and core.dev.mic_muted
        assert not core.live.audio_io._open  # the separate Live stream closes before tool completion
        assert read_state(home)["prefs"]["mic_muted"] is True
        await asyncio.sleep(0.02)
        assert core.live is not None and not stopped  # completed result, no cancellation of its caller
        core.live._tools_running = 0
        await until(lambda: core.live is None)
        assert stopped == [True]
        assert await asyncio.to_thread(cli.cmd_mic, SimpleNamespace(action="status"), cfg, home) == 0
        assert await asyncio.to_thread(cli.cmd_mic, SimpleNamespace(action="unmute"), cfg, home) == 0
        assert not core.dev.mic_muted and read_state(home)["prefs"]["mic_muted"] is False
    finally:
        await core.stop()


async def test_cli_appearance_persists_and_reaches_connected_face(home, cfg):
    core = make_core(home, cfg)
    await core.start()
    face = None
    try:
        face = await open_face(core)
        for mode in ("avatar2d", "avatar3d", "hidden", "face"):
            assert cli.main(["appearance", "set", mode]) == 0
            assert Appearance(home).mode == mode
            await face.wait("appearance.set", lambda p: p["mode"] == mode)
        assert cli.main(["appearance", "status"]) == 0
        assert cli.main(["appearance", "list"]) == 0
        with pytest.raises(SystemExit) as e:
            cli.main(["appearance", "set", "invalid"])
        assert e.value.code == 2 and Appearance(home).mode == "face"
    finally:
        if face:
            await face.ws.close()
        await core.stop()


def test_cli_mic_disconnected_is_not_success(home, cfg, monkeypatch):
    monkeypatch.setattr(cli, "_ws_call", lambda *a, **k: None)
    assert cli.cmd_mic(SimpleNamespace(action="unmute"), cfg, home) == 1
    monkeypatch.setattr(cli, "_ws_call", lambda *a, **k: {"runtime": {"mic": {"muted": False}}})
    assert cli.cmd_mic(SimpleNamespace(action="mute"), cfg, home) == 1
