"""Custom wake phrase training (WP-M4, `jarvis wake train`): numpy head, window labelling, the whole pipeline with a
fake synthesizer + fake front end (fast, silent, no TTS), and the runtime detector on the real openWakeWord front end."""
import json

import numpy as np
import pytest

from jarves import cli
from jarves.identity import Identity
from jarves.voice import wake_train as wt
from jarves.voice.wakeword import HeadWakeDetector, OpenWakeWordDetector, make_detector


def tone(freq, secs=0.6, sr=16000):
    t = np.arange(int(sr * secs)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr


def fake_synth(text, voice, rate, pitch):
    """The 'phrase' is a rising two-tone chirp; everything else a flat tone (deterministic, no audio device)."""
    if text.lower() in ("hey max", "היי מקס"):
        a, sr = tone(500 * pitch, 0.3)
        b, _ = tone(1500 * pitch, 0.3)
        return np.concatenate([a, b]), sr
    return tone(300 + 10 * len(text), 0.5)


def fake_embedder(audio):
    """One 96-d vector per 80 ms chunk: coarse spectrum (stands in for the openWakeWord embedding)."""
    n = len(audio) // wt.CHUNK
    frames = audio[: n * wt.CHUNK].reshape(n, wt.CHUNK)
    spec = np.abs(np.fft.rfft(frames, axis=1))[:, :576].reshape(n, 96, 6).mean(2)
    return np.log1p(spec).astype(np.float32), np.arange(n)


VOICES = [wt.Voice("en1", "en", "fake"), wt.Voice("en2", "en", "fake"), wt.Voice("he1", "he", "fake")]


def test_head_roundtrip_and_scores(tmp_path):
    rng = np.random.default_rng(1)
    x = rng.standard_normal((200, wt.WINDOWS, wt.EMB)).astype(np.float32)
    y = (x[:, -1, 0] > 0.5).astype(int)
    head = wt.train_head(x, y, epochs=60)
    p = head_pred = wt.head_score(head, x)
    assert ((head_pred > 0.5) == y).mean() > 0.9 and p.shape == (200,)
    wt.save_head(tmp_path / "h.npz", head, {"phrase": "hey max", "threshold": 0.55})
    back = wt.load_head(tmp_path / "h.npz")
    assert back["phrase"] == "hey max" and back["meta"]["threshold"] == 0.55
    assert np.allclose(wt.head_score(back, x), p, atol=1e-5)
    np.savez(tmp_path / "other.npz", a=np.zeros(1), meta=np.array(json.dumps({"format": "x"})))
    with pytest.raises(ValueError):
        wt.load_head(tmp_path / "other.npz")


def test_window_labels_positive_only_at_the_phrase_end():
    sr = wt.SR
    events = [(sr * 3, sr * 4, True), (sr * 8, sr * 9, False)]
    n = 12 * sr // wt.CHUNK
    x, y, t = wt.windows(np.zeros((n, wt.EMB), np.float32), np.arange(n), events)
    pos_t = t[y == 1] / sr
    assert len(pos_t) and pos_t.min() >= 4.0 and pos_t.max() <= 4.0 + wt.POS_AFTER_S
    assert not any((t / sr > 3.75) & (t / sr < 4.0))              # nearly whole phrase: left out (ambiguous)
    assert (y[(t / sr > 8) & (t / sr < 10)] == 0).all()           # a negative clip is negative


def test_pipeline_trains_and_separates_the_phrase(tmp_path):
    out = tmp_path / "wake" / "hey_max.npz"
    rep = wt.train_phrase("hey max", out, say=["היי מקס"], voices=VOICES, synth=fake_synth, embedder=fake_embedder,
                          rates=(0.9, 1.1), pitches=(0.9, 1.0, 1.1), progress=lambda s: None)
    assert out.is_file() and out.with_suffix(".json").is_file()
    assert rep["held_out_voice"] == "en1" and rep["eval"]["positives"] > 0
    assert rep["eval"]["recall"] >= 0.8 and rep["eval"]["false_per_hour"] <= 0.5, rep["eval"]
    assert "NOT a real-room" in rep["evidence"] and 0.3 <= rep["threshold"] <= 0.95
    models = wt.list_models(tmp_path)
    assert models[0]["phrase"] == "hey max" and models[0]["eval"]["recall"] == rep["eval"]["recall"]


def test_pipeline_needs_voices_or_samples(tmp_path):
    with pytest.raises(RuntimeError):
        wt.train_phrase("hey max", tmp_path / "x.npz", voices=[], embedder=fake_embedder)


def test_make_detector_dispatch(tmp_path):
    assert isinstance(make_detector("hey_jarvis"), OpenWakeWordDetector)
    assert isinstance(make_detector(str(tmp_path / "x.tflite")), OpenWakeWordDetector)
    assert isinstance(make_detector(str(tmp_path / "x.npz"), 0.6), HeadWakeDetector)
    ok, msg = make_detector(str(tmp_path / "missing.npz")).check()
    assert not ok and "missing.npz" in msg


def test_head_detector_on_the_real_front_end(tmp_path):
    import pyopen_wakeword  # noqa: F401  (the `voice` extra is installed everywhere: uv sync --all-extras)
    rng = np.random.default_rng(0)
    x = rng.standard_normal((40, wt.WINDOWS, wt.EMB)).astype(np.float32)
    wt.save_head(tmp_path / "m.npz", wt.train_head(x, np.array([0, 1] * 20), epochs=2), {"phrase": "hey max"})
    det = make_detector(str(tmp_path / "m.npz"), 0.999)
    ok, msg = det.check()
    assert ok and "hey max" in msg
    silence = np.zeros(wt.CHUNK, np.int16).tobytes()
    for _ in range(30):
        det.feed(silence)
    assert 0.0 <= det.last_score <= 1.0
    det.reset()


def test_cli_train_and_apply(home, monkeypatch, capsys):
    monkeypatch.setattr(wt, "local_voices", lambda: VOICES)
    real = wt.train_phrase

    def fast(*a, **kw):
        kw.update(synth=fake_synth, embedder=fake_embedder, rates=(1.0,), pitches=(0.9, 1.1))
        return real(*a, **kw)
    monkeypatch.setattr(wt, "train_phrase", fast)
    assert cli.main(["wake", "train", "hey", "max", "--say", "היי מקס", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "recall" in out and "Applied" in out
    w = Identity(home).wake()
    assert w["phrase"] == "hey max" and w["model"].endswith("hey_max.npz")
    assert cli.main(["wake", "train"]) == 2


def test_cli_train_revert(home, monkeypatch, capsys):
    monkeypatch.setattr(wt, "local_voices", lambda: VOICES)
    real = wt.train_phrase

    def fast(*a, **kw):
        kw.update(synth=fake_synth, embedder=fake_embedder, rates=(1.0,), pitches=(0.9, 1.1))
        return real(*a, **kw)
    monkeypatch.setattr(wt, "train_phrase", fast)
    assert cli.main(["wake", "train", "hey", "max", "--apply"]) == 0
    model = Identity(home).wake()["model"]
    assert model.endswith("hey_max.npz")
    assert cli.main(["wake", "train", "--revert"]) == 0
    assert "Reverted" in capsys.readouterr().out
    w = Identity(home).wake()
    assert w["model"] == "hey_jarvis" and w["phrase"] == "Hey Jarvis" and w["threshold"] is None
    assert (home / "wake" / "hey_max.npz").is_file()          # the trained file is kept
