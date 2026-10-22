"""V4 package J (#17): custom wake word in production. Sample quality checks, the calibration report, the near-miss
false-wake regression list, reproducible training, never overwriting a working model without --apply and
`jarvis wake train --revert`. Fake synthesizer + fake front end and generated WAV tones: silent, no microphone, no
speakers, no owner audio."""
import json
import wave

import numpy as np
import pytest

from jarves import cli
from jarves.identity import Identity
from jarves.voice import wake_prod as wp
from jarves.voice import wake_train as wt

from test_wake_train import VOICES, fake_embedder, fake_synth


def write_wav(path, audio, sr=16000):
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def phrase_audio(rng, amp=0.5, floor=1e-3, pad=0.3, sr=16000):
    t = np.arange(int(0.3 * sr)) / sr
    voice = amp * np.concatenate([np.sin(2 * np.pi * 500 * t), np.sin(2 * np.pi * 1500 * t)])
    quiet = lambda: floor * rng.standard_normal(int(pad * sr))
    return np.concatenate([quiet(), voice, quiet()]).astype(np.float32)


@pytest.fixture
def rng():
    return np.random.default_rng(3)


# ---------------------------------------------------------------------- sample quality checks
def test_sample_checks_accept_a_clean_recording_and_name_every_problem(tmp_path, rng):
    write_wav(tmp_path / "good.wav", phrase_audio(rng))
    write_wav(tmp_path / "gooddup.wav", phrase_audio(np.random.default_rng(3)))        # same bytes as good.wav
    write_wav(tmp_path / "silent.wav", np.zeros(16000))
    write_wav(tmp_path / "clipped.wav", np.clip(phrase_audio(rng, amp=5.0), -1, 1))
    write_wav(tmp_path / "short.wav", phrase_audio(rng)[:2000])
    write_wav(tmp_path / "quiet.wav", phrase_audio(rng, amp=0.01, floor=1e-5))
    write_wav(tmp_path / "noisy.wav", phrase_audio(rng, floor=0.2))
    (tmp_path / "broken.wav").write_bytes(b"not a wav")
    got = {c.path.stem: c for c in wp.check_samples(tmp_path)}
    assert got["good"].ok and not got["good"].problems
    assert not got["gooddup"].ok and "duplicate" in " ".join(got["gooddup"].problems)
    assert "silent" in " ".join(got["silent"].problems)
    assert "clipped" in " ".join(got["clipped"].problems)
    assert "too short" in " ".join(got["short"].problems)
    assert "quiet" in " ".join(got["quiet"].problems)
    assert "noisy" in " ".join(got["noisy"].problems)
    assert "readable" in " ".join(got["broken"].problems)
    s = wp.sample_summary(list(got.values()))
    assert s["files"] == 8 and s["usable"] == 1 and not s["enough"] and not s["minimum"]


def test_rejected_samples_are_left_out_of_training_not_deleted(tmp_path, rng):
    write_wav(tmp_path / "a.wav", phrase_audio(rng))
    write_wav(tmp_path / "b.wav", np.zeros(16000))
    ok = [c.path for c in wp.check_samples(tmp_path) if c.ok]
    assert [p.name for p in ok] == ["a.wav"] and (tmp_path / "b.wav").exists()
    assert len(wt.read_samples(tmp_path, ok)) == 1 and len(wt.read_samples(tmp_path)) == 2


# ---------------------------------------------------------------------- reproducible training + calibration report
def train(out, samples=None, seed=7, files=None):
    return wt.train_phrase("hey max", out, say=["היי מקס"], samples=samples, voices=VOICES, synth=fake_synth,
                           embedder=fake_embedder, rates=(1.0,), pitches=(0.9, 1.1), seed=seed, sample_files=files,
                           progress=lambda s: None)


def own_folder(tmp_path, n=8):
    d = tmp_path / "own"
    d.mkdir()
    for i in range(n):
        write_wav(d / f"s{i}.wav", phrase_audio(np.random.default_rng(100 + i), amp=0.4 + 0.05 * (i % 3)))
    return d


def test_same_seed_and_samples_give_the_same_model(tmp_path):
    d = own_folder(tmp_path)
    r1 = train(tmp_path / "a.npz", d)
    r2 = train(tmp_path / "b.npz", d)
    h1, h2 = wt.load_head(tmp_path / "a.npz"), wt.load_head(tmp_path / "b.npz")
    assert all(np.array_equal(h1[k], h2[k]) for k in ("mu", "sd", "w1", "b1", "w2", "b2"))
    assert r1["threshold"] == r2["threshold"] and r1["curve"] == r2["curve"]
    assert r1["samples_sha256"] == r2["samples_sha256"] != ""
    assert r1["seed"] == 7


def test_calibration_report_holds_the_curve_and_the_owner_scores(tmp_path):
    d = own_folder(tmp_path)
    rep = train(tmp_path / "m.npz", d)
    thresholds = [r["threshold"] for r in rep["curve"]]
    assert thresholds == sorted(thresholds) and rep["threshold"] in thresholds
    assert all({"recall", "false_per_hour", "owner_recall", "owner_held_out_recall"} <= set(r) for r in rep["curve"])
    ow = rep["owner"]
    assert ow["recordings"] == 8 and len(ow["peak_scores"]) == 8 and ow["held_out_indices"] == [3, 7]
    assert ow["recall_at_threshold"] >= 0.75
    saved = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert saved["curve"] == rep["curve"] and "s0.wav" not in json.dumps(saved)       # no file names in the report
    lines = wp.format_curve(rep["curve"], rep["threshold"])
    assert len(lines) == len(rep["curve"]) + 1 and sum("<- chosen" in x for x in lines) == 1


def test_threshold_prefers_the_owners_voice_among_safe_thresholds(monkeypatch):
    peaks = [0.9, 0.85, 0.5, 0.45]
    embs, last = np.zeros((40, wt.EMB), np.float32), np.arange(40)
    safe = {"recall": 1.0, "false_per_hour": 0.0, "false_wakes": 0, "hours": 1, "positives": 1}
    monkeypatch.setattr(wt, "evaluate", lambda *a, **k: safe)
    th, _, curve = wp.calibrate({}, embs, last, [], 100, peaks, [3])
    assert wp.recall_at(peaks, th) == max(r["owner_recall"] for r in curve)
    assert th == 0.45                                                       # ties go to the higher threshold
    assert [r["owner_recall"] for r in curve][:3] == [1.0, 1.0, 1.0]
    # one unsafe low threshold: the choice moves up to the best SAFE one
    monkeypatch.setattr(wt, "evaluate", lambda a, e, l, ev, n, th: {**safe, "false_per_hour": 9.0 if th < 0.5 else 0.0})
    th, _, _ = wp.calibrate({}, embs, last, [], 100, peaks, [3])
    assert th == 0.5


# ---------------------------------------------------------------------- false-wake regression (near-miss sentences)
def test_near_miss_list_is_the_voice_campaign_list():
    texts = [t for _, t in wp.NEAR_MISS_SENTENCES]
    assert len(texts) == 20 and len(set(texts)) == 20
    for must in ("hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles, come here", "היי טרוויס", "ג'רוויס"):
        assert must in texts
    assert {lang for lang, _ in wp.NEAR_MISS_SENTENCES} == {"en", "he"}


def test_trained_model_does_not_wake_on_any_near_miss_sentence(tmp_path):
    rep = train(tmp_path / "m.npz")
    nm = rep["near_miss"]
    assert nm["rendered"] == 12 * 2 + 8 * 1 and nm["false_wakes"] == [], nm      # 2 en voices, 1 he voice
    assert wp.gate(rep) == []


def test_a_hair_trigger_model_fails_the_near_miss_check_and_the_gate(tmp_path):
    rep = train(tmp_path / "m.npz")
    hot = wt.load_head(tmp_path / "m.npz")
    hot["b2"] = hot["b2"] + 40.0                                                 # scores ~1.0 on everything
    nm = wp.near_miss_check(hot, 0.5, VOICES, fake_synth, fake_embedder)
    assert len(nm["false_wakes"]) == nm["rendered"] > 0 and nm["worst_score"] > 0.99
    why = wp.gate({**rep, "near_miss": nm})
    assert why and "near-miss" in why[0]


def test_gate_blocks_a_model_that_misses_the_owner():
    rep = {"near_miss": {"false_wakes": []}, "owner": {"held_out_indices": [3, 7], "held_out_recall_at_threshold": 0.5}}
    assert "held-out" in wp.gate(rep)[0]
    rep["owner"]["held_out_recall_at_threshold"] = 1.0
    assert wp.gate(rep) == []


# ---------------------------------------------------------------------- CLI: candidate, --apply, --revert
@pytest.fixture
def fast_cli(monkeypatch):
    monkeypatch.setattr(wt, "local_voices", lambda: VOICES)
    real = wt.train_phrase

    def fast(*a, **kw):
        kw.update(synth=fake_synth, embedder=fake_embedder, rates=(1.0,), pitches=(0.9, 1.1))
        return real(*a, **kw)
    monkeypatch.setattr(wt, "train_phrase", fast)


def test_train_without_apply_never_touches_the_active_model(home, fast_cli, capsys):
    active = home / "wake" / "hey_max.npz"
    active.parent.mkdir(parents=True)
    active.write_bytes(b"WORKING MODEL")
    before = Identity(home).wake()
    assert cli.main(["wake", "train", "hey", "max", "--say", "היי מקס"]) == 0
    out = capsys.readouterr().out
    assert "Not applied" in out and "Calibration" in out and "Near-miss" in out
    assert active.read_bytes() == b"WORKING MODEL" and Identity(home).wake() == before
    assert (home / "wake" / "candidates" / "hey_max.npz").is_file()
    assert cli.main(["wake", "train", "hey", "max", "--out", str(active)]) == 1          # explicit --out: still no
    assert active.read_bytes() == b"WORKING MODEL"


def test_apply_archives_the_old_model_and_revert_restores_it(home, fast_cli, capsys):
    active = home / "wake" / "hey_max.npz"
    assert cli.main(["wake", "train", "hey", "max", "--say", "היי מקס", "--apply"]) == 0       # first model
    first = active.read_bytes()
    assert Identity(home).wake()["model"].endswith("hey_max.npz")
    assert cli.main(["wake", "train", "hey", "max", "--say", "היי מקס", "--apply", "--seed", "9"]) == 0
    assert active.read_bytes() != first and len(list((home / "wake" / "history").glob("*.npz"))) == 1
    assert cli.main(["wake", "train", "--revert"]) == 0                                       # back to the first
    assert active.read_bytes() == first and Identity(home).wake()["model"].endswith("hey_max.npz")
    assert cli.main(["wake", "train", "--revert"]) == 0                                       # back to the stock model
    w = Identity(home).wake()
    assert w["model"] == "hey_jarvis" and w["phrase"] == "Hey Jarvis" and w["threshold"] is None
    assert active.is_file()                                                                   # trained file is kept
    capsys.readouterr()
    assert cli.main(["wake", "train", "--revert"]) == 1
    assert "Nothing to revert" in capsys.readouterr().err


def test_apply_is_refused_when_a_near_miss_would_wake(home, fast_cli, monkeypatch, capsys):
    real = wp.near_miss_check
    monkeypatch.setattr(wp, "near_miss_check", lambda *a, **k: {**real(*a, **k), "false_wakes": [
        {"text": "hey Travis", "voice": "en1", "score": 0.9}], "rendered": 1})
    assert cli.main(["wake", "train", "hey", "max", "--apply"]) == 1
    assert "hey Travis" in capsys.readouterr().err and not (home / "wake" / "hey_max.npz").exists()
    assert Identity(home).wake()["model"] == "hey_jarvis"
    assert cli.main(["wake", "train", "hey", "max", "--apply", "--accept-risk"]) == 0
    assert (home / "wake" / "hey_max.npz").is_file()


def test_owner_samples_folder_is_used_by_default_and_checked(home, fast_cli, capsys):
    d = home / "wake" / "samples"
    d.mkdir(parents=True)
    for i in range(5):
        write_wav(d / f"r{i}.wav", phrase_audio(np.random.default_rng(i)))
    write_wav(d / "bad.wav", np.zeros(16000))
    assert cli.main(["wake", "train", "--check-samples"]) == 0                    # 5 usable >= minimum
    out = capsys.readouterr().out
    assert "5 usable of 6" in out and "REJECTED bad.wav" in out
    assert cli.main(["wake", "train", "hey", "max", "--say", "היי מקס"]) == 0
    out = capsys.readouterr().out
    assert "your recordings: 5 usable" in out and "Your recordings wake it" in out


def test_check_samples_without_recordings_says_where_to_put_them(home, capsys):
    assert cli.main(["wake", "train", "--check-samples"]) == 1
    assert "samples" in capsys.readouterr().err
