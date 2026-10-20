"""F2: `jarvis say` uses the Core's mode-aware, budgeted speech (no billing bypass)."""
import numpy as np
import pytest

from jarves import cli
from jarves.budget import Budget
from jarves.config import Config
from jarves.memory import MemoryStore
from jarves.voice import realtime, tts


@pytest.fixture
def speech(monkeypatch, tmp_path):
    """Record which engine speaks; never touch real audio or the network."""
    calls = {"cloud": 0, "local": 0}

    async def cloud_synth(self, text):
        calls["cloud"] += 1
        return np.zeros(100, np.float32), 24000

    async def local_synth(self, text):
        calls["local"] += 1
        return np.zeros(100, np.float32), 16000

    for eng in (realtime.RealtimeSpeaker, tts.OpenAISpeechTTS):
        monkeypatch.setattr(eng, "check", lambda self: (True, "fake cloud"))
        monkeypatch.setattr(eng, "synth", cloud_synth)
    for eng in (tts.WinRTTTS, tts.EspeakTTS):
        monkeypatch.setattr(eng, "check", lambda self: (True, "fake local"))
        monkeypatch.setattr(eng, "synth", local_synth)

    async def no_play(self, samples, rate):
        return None
    from jarves.voice import audio
    monkeypatch.setattr(audio.SoundDeviceAudio, "play", no_play)
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")          # a key IS present in every case
    return calls


def say(tmp_path, connection, limit=200):
    (tmp_path / "config.toml").write_text(f'[ai]\nconnection = "{connection}"\ndaily_request_limit = {limit}\n',
                                          encoding="utf-8")
    return cli.main(["say", "שלום"])


def test_plan_only_mode_never_uses_paid_speech_even_with_key(tmp_path, speech):
    assert say(tmp_path, "chatgpt") == 0
    assert speech == {"cloud": 0, "local": 1}


def test_exhausted_budget_falls_back_to_local(tmp_path, speech):
    assert say(tmp_path, "api_key", limit=0) == 0
    assert speech == {"cloud": 0, "local": 1}


def test_within_budget_cloud_speech_is_metered(tmp_path, speech):
    assert say(tmp_path, "api_key", limit=5) == 0
    assert speech == {"cloud": 1, "local": 0}
    assert MemoryStore(tmp_path / "memory.db").usage_today("ai_requests") == 1      # counted in the shared budget


def test_make_tts_engines_by_mode(tmp_path, speech):
    b = Budget(MemoryStore(tmp_path / "m.db"), Config().ai)
    for conn, cloud in (("chatgpt", False), ("api_key", True), ("combined", True)):
        c = Config()
        c.ai.connection = conn
        engines = tts.make_tts(c, b).engines
        assert any(getattr(e, "quality", "") == "cloud" for e in engines) is cloud, conn
    off = Config()
    off.ai.provider = "offline"
    assert not any(getattr(e, "quality", "") == "cloud" for e in tts.make_tts(off, b).engines)
