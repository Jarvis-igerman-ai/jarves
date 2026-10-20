"""`jarvis say`: engine choice by language/OS, one-line output, no engine noise that cannot apply (#17)."""
import numpy as np
import pytest

from jarves import cli
from jarves.config import VoiceConfig
from jarves.voice import audio, realtime, tts


def names(chain):
    return [e.name for e in chain]


def test_text_lang():
    assert tts.text_lang("שלום לנועה, לאביטל ולצחי") == "he"
    assert tts.text_lang("hello Noa") == "en"
    assert tts.text_lang("hello שלום") == "he"


def test_chain_by_os():
    win = names(tts.tts_chain(VoiceConfig(), platform="win32"))
    lin = names(tts.tts_chain(VoiceConfig(), platform="linux"))
    assert win == ["openai-tts", "openai-realtime", "winrt"]
    assert lin == ["openai-tts", "openai-realtime", "espeak"]
    assert "espeak" not in win and "winrt" not in lin


def test_dedicated_tts_before_conversation_model():
    assert names(tts.tts_chain(VoiceConfig(), platform="win32", allow_cloud=True))[0] == "openai-tts"
    assert names(tts.tts_chain(VoiceConfig(), platform="win32", allow_cloud=False)) == ["winrt"]


def test_hebrew_text_never_reaches_an_engine_without_hebrew():
    class EnOnly:
        name, quality, langs = "en-only", "local-fallback", ("en",)
    engines = [EnOnly(), tts.WinRTTTS(), tts.EspeakTTS()]
    assert "en-only" not in names(tts.select_engines(engines, "שלום"))
    assert "en-only" in names(tts.select_engines(engines, "hello"))


@pytest.fixture
def say_env(monkeypatch, tmp_path):
    async def synth(self, text):
        return np.zeros(100, np.float32), 24000

    async def no_play(self, samples, rate):
        return None
    for eng in (realtime.RealtimeSpeaker, tts.OpenAISpeechTTS, tts.WinRTTTS):
        monkeypatch.setattr(eng, "check", lambda self: (True, "voice X"))
        monkeypatch.setattr(eng, "synth", synth)
    monkeypatch.setattr(audio.SoundDeviceAudio, "play", no_play)
    monkeypatch.setenv("JARVES_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    (tmp_path / "config.toml").write_text('[ai]\nconnection = "api_key"\n', encoding="utf-8")


def test_default_output_is_one_line_without_espeak_on_windows(say_env, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert cli.main(["say", "שלום"]) == 0
    out = capsys.readouterr()
    lines = [x for x in (out.out + out.err).splitlines() if x.strip()]
    assert len(lines) == 1 and lines[0].startswith("Speaking with openai-tts")
    assert "espeak" not in (out.out + out.err).lower()


def test_verbose_shows_engine_table(say_env, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    assert cli.main(["say", "--verbose", "שלום"]) == 0
    out = capsys.readouterr().out
    assert "openai-tts" in out and "winrt" in out and "espeak" not in out


def test_failure_is_one_clear_error(say_env, capsys, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    for eng in (realtime.RealtimeSpeaker, tts.OpenAISpeechTTS, tts.WinRTTTS):
        monkeypatch.setattr(eng, "check", lambda self: (False, "not available"))
    assert cli.main(["say", "שלום"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR:") and "jarvis key" in err and "espeak" not in err
