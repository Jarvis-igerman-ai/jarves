"""voice/stt.py WhisperSTT: model fallback, error reporting, single load, short-audio skip (hermetic: a fake
faster_whisper module, no download, no real model, no clock)."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

from jarves.voice.stt import STTUnavailable, WhisperSTT


@pytest.fixture
def fake_whisper(monkeypatch):
    """Install a fake faster_whisper; `broken` names fail to load, `built` records every construction."""
    state = SimpleNamespace(broken=set(), built=[], calls=[])

    class FakeModel:
        def __init__(self, name, device, compute_type, cpu_threads):
            state.built.append((name, device, compute_type, cpu_threads))
            if name in state.broken:
                raise OSError(f"cannot download {name}")
            self.name = name

        def transcribe(self, audio, **kw):
            state.calls.append((audio.dtype, len(audio), kw))
            return iter([SimpleNamespace(text="  שלום "), SimpleNamespace(text="עולם  ")]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    return state


def audio(seconds: float) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float64)


@pytest.mark.asyncio
async def test_loads_primary_model_and_transcribes(fake_whisper):
    s = WhisperSTT("ivrit", compute="int8", language="he", cpu_threads=2)
    assert await s.transcribe(audio(1.0)) == "שלום עולם"
    assert s.loaded_name == "ivrit" and fake_whisper.built == [("ivrit", "cpu", "int8", 2)]
    dtype, n, kw = fake_whisper.calls[0]
    assert dtype == np.float32 and n == 16000                 # cast to float32 before the model sees it
    assert kw["language"] == "he" and kw["vad_filter"] is True and kw["condition_on_previous_text"] is False


@pytest.mark.asyncio
async def test_falls_back_when_primary_fails(fake_whisper):
    fake_whisper.broken = {"ivrit"}
    s = WhisperSTT("ivrit", fallback="small")
    await s.ensure_loaded()
    assert s.loaded_name == "small" and [b[0] for b in fake_whisper.built] == ["ivrit", "small"]
    assert s.error is None


@pytest.mark.asyncio
async def test_all_models_fail_reports_actionable_error_and_retries_later(fake_whisper):
    fake_whisper.broken = {"ivrit", "small"}
    s = WhisperSTT("ivrit", fallback="small")
    with pytest.raises(STTUnavailable) as e:
        await s.ensure_loaded()
    assert e.value.code == "STT_MODEL_UNAVAILABLE" and "cannot download small" in e.value.message
    assert "voice.stt_model" in e.value.action and s.error is e.value
    fake_whisper.broken = set()                               # network back: the next call loads again
    await s.ensure_loaded()
    assert s.loaded_name == "ivrit" and s.error is None


@pytest.mark.asyncio
async def test_no_fallback_tries_only_the_named_model(fake_whisper):
    fake_whisper.broken = {"ivrit"}
    s = WhisperSTT("ivrit", fallback=None)
    with pytest.raises(STTUnavailable):
        await s.ensure_loaded()
    assert [b[0] for b in fake_whisper.built] == ["ivrit"]


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_load(fake_whisper):
    s = WhisperSTT("ivrit")
    await asyncio.gather(s.ensure_loaded(), s.ensure_loaded(), s.ensure_loaded())
    assert len(fake_whisper.built) == 1
    await s.ensure_loaded()                                   # already loaded: no second build
    assert len(fake_whisper.built) == 1


@pytest.mark.asyncio
async def test_short_audio_is_skipped_without_loading(fake_whisper):
    s = WhisperSTT("ivrit")
    assert await s.transcribe(audio(0.2)) == ""
    assert fake_whisper.built == [] and fake_whisper.calls == []


def test_unavailable_carries_code_message_action():
    e = STTUnavailable("C", "msg", "do this")
    assert (e.code, e.message, e.action, str(e)) == ("C", "msg", "do this", "msg")


def test_module_imports_without_the_voice_extra(monkeypatch):
    """CI has no faster_whisper: importing the module must still work and only using STT reports it unavailable.
    A fresh private copy is loaded so the shared jarves.voice.stt module (and its classes) stay untouched."""
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # None in sys.modules: `import` raises ImportError
    path = sys.modules["jarves.voice.stt"].__file__
    spec = importlib.util.spec_from_file_location("jarves.voice._stt_no_extra", path)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert fresh._fw is None and fresh.WhisperSTT("ivrit").loaded_name is None
