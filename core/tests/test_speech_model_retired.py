"""#1224: gpt-4o-mini-tts shuts down 14 Jan 2027; nothing may call it by default."""
import asyncio

import pytest

from jarves.config import VoiceConfig
from jarves.voice import tts, wake_eval
from jarves.voice.realtime import RealtimeSpeaker

RETIRED = "gpt-4o-mini-tts"


def test_default_chain_never_uses_retired_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vc = VoiceConfig()
    assert vc.speech_http_model != RETIRED
    chain = tts.tts_chain(vc, allow_cloud=True)
    usable = [getattr(e, "engine", e) for e in chain if e.check()[0]]
    assert usable and isinstance(usable[0], RealtimeSpeaker)   # gpt-realtime-2.1-mini speaks first
    assert all(getattr(e, "model", "") != RETIRED for e in usable)


@pytest.mark.parametrize("model", ["", RETIRED, "gpt-4o-mini-tts-2025-12-15", "tts-1"])
def test_http_engine_refuses_off_or_retired_model(monkeypatch, model):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    e = tts.OpenAISpeechTTS(model)
    ok, detail = e.check()
    assert not ok and "speech_http_model" in detail
    with pytest.raises(tts.TTSUnavailable) as ex:      # no network call is attempted
        asyncio.run(e.synth("hello"))
    assert ex.value.code == "TTS_MODEL_RETIRED"


def test_default_http_engine_is_off():
    assert tts.OpenAISpeechTTS().model == ""


def test_wake_eval_does_not_call_retired_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert wake_eval.OPENAI_TTS_MODEL != RETIRED
    with pytest.raises(RuntimeError, match="retired"):
        wake_eval.openai_clips([("hey jarvis", "")], "pos", tmp_path, ["echo"])
