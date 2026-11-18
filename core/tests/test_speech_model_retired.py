"""#1224: gpt-4o-mini-tts shuts down 14 Jan 2027; nothing may call it by default."""
import asyncio

import pytest

from jarves.config import VoiceConfig
from jarves.voice import tts, wake_eval
from jarves.voice.realtime import RealtimeSpeaker

RETIRED = "gpt-4o-mini-tts"


def _usable(vc):
    """Engines FallbackTTS.check() keeps, unwrapped; RealtimeSpeaker.check() only needs OPENAI_API_KEY."""
    f = tts.FallbackTTS(tts.tts_chain(vc, allow_cloud=True))
    f.check()
    return [getattr(e, "engine", e) for e in f.usable]


@pytest.mark.parametrize("setting", [None, RETIRED, " GPT-4o-mini-TTS "])
def test_chain_never_uses_retired_model(monkeypatch, setting):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vc = VoiceConfig() if setting is None else VoiceConfig(speech_http_model=setting)
    if setting is None:
        assert vc.speech_http_model == ""      # default is off
    usable = _usable(vc)
    assert usable and isinstance(usable[0], RealtimeSpeaker)   # gpt-realtime-2.1-mini speaks first
    assert not any(isinstance(e, tts.OpenAISpeechTTS) for e in usable)


def test_chain_keeps_a_user_set_supported_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    usable = _usable(VoiceConfig(speech_http_model="some-future-tts"))
    assert isinstance(usable[0], tts.OpenAISpeechTTS) and usable[0].model == "some-future-tts"


@pytest.mark.parametrize("model, code", [("", "TTS_MODEL_OFF"), ("  ", "TTS_MODEL_OFF"),
                                         (RETIRED, "TTS_MODEL_RETIRED"),
                                         ("gpt-4o-mini-tts-2025-12-15", "TTS_MODEL_RETIRED"),
                                         (" Gpt-4o-Mini-Tts ", "TTS_MODEL_RETIRED")])
def test_http_engine_refuses_off_or_retired_model(monkeypatch, model, code):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    e = tts.OpenAISpeechTTS(model)
    ok, detail = e.check()
    assert not ok and "speech_http_model" in detail
    with pytest.raises(tts.TTSUnavailable) as ex:      # no network call is attempted
        asyncio.run(e.synth("hello"))
    assert ex.value.code == code


def test_registry_still_watches_speech_http_model():
    import tomllib
    from pathlib import Path
    reg = tomllib.loads((Path(__file__).resolve().parents[2] / "docs/decisions/best_in_class.toml").read_text("utf-8"))
    e = next(x for x in reg["entry"] if x["id"] == "speech_http_model")
    assert e["kind"] == "manual" and e["code_match"] == 'speech_http_model: str = ""'


def test_default_http_engine_is_off():
    assert tts.OpenAISpeechTTS().model == ""


def test_wake_eval_does_not_call_retired_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert wake_eval.OPENAI_TTS_MODEL != RETIRED
    with pytest.raises(RuntimeError, match="retired"):
        wake_eval.openai_clips([("hey jarvis", "")], "pos", tmp_path, ["echo"])
