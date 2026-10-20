"""Startup must not load local recognition for cloud voice, but keep fallback load before WinRT."""
import pytest

from conftest import FakeSTT, FakeTTS
from jarves import app
from jarves.skills.music import FakePlayer
from jarves.voice.audio import SimAudio


@pytest.mark.parametrize("mode,connection,has_key,preload", [
    ("live", "combined", True, False),
    ("realtime", "api_key", True, False),
    ("live", "chatgpt", True, True),
    ("live", "api_key", False, True),
    ("push_to_talk", "api_key", True, True),
    ("off", "api_key", False, False),
])
async def test_startup_recognition_routing(home, cfg, monkeypatch, mode, connection, has_key, preload):
    events = []

    class LocalSTT(FakeSTT):
        async def ensure_loaded(self):
            events.append("recognition")

    class Speech(FakeTTS):
        def check(self):
            events.append("speech")
            return super().check()

    monkeypatch.setattr(app, "WhisperSTT", lambda *args: LocalSTT())
    monkeypatch.setattr(app, "make_agent", lambda *args: None)
    if has_key:
        monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    cfg.ai.connection = connection
    cfg.voice.mode = mode
    cfg.voice.wake_enabled = False
    core = app.Core(cfg, home, audio=SimAudio(), player=FakePlayer(), tts=Speech(), console_logs=False)
    await core.start()
    try:
        assert events == (["recognition", "speech"] if preload else ["speech"])
        assert core.voice_mode == ("push_to_talk" if preload else mode)
    finally:
        await core.stop()
