"""Hard negatives in the wake trainer, the disjoint held-out gate list and the relative gate (issue wake-hardneg).
Fake synthesizer + fake front end + a fake baseline model: hermetic, silent, frozen seeds, no engine, no owner audio."""
import numpy as np

from jarves.voice import wake_prod as wp
from jarves.voice import wake_train as wt

from test_wake_prod import phrase_audio
from test_wake_train import VOICES, fake_embedder, fake_synth


class FlatScorer:
    """Stand-in for the model being replaced: every 80 ms chunk scores `level`."""
    name, threshold = "standard fake", 0.5

    def __init__(self, level):
        self.level = level

    def scores(self, audio):
        n = len(audio) // wt.CHUNK
        return (np.arange(n) + 1) * wt.CHUNK, np.full(n, self.level)


def small_train(out, **kw):
    return wt.train_phrase("hey max", out, say=["היי מקס"], voices=VOICES, synth=kw.pop("synth", fake_synth),
                           embedder=fake_embedder, rates=(1.0,), pitches=(0.9, 1.1), progress=lambda s: None, **kw)


def test_gate_list_is_disjoint_from_everything_the_trainer_sees():
    held = {t.lower() for _, t in wp.NEAR_MISS_HELDOUT}
    seen = {t.lower() for t in wt.HARD_NEG_EN + wt.HARD_NEG_HE + wt.NEG_EN + wt.NEG_HE + wt.NEAR_EN}
    assert len(held) == len(wp.NEAR_MISS_HELDOUT) and not held & seen
    for must in ("hey Travis", "hey service", "hey Jarvie", "hey Harvey", "hey Garvis", "a jar of", "hey Charles",
                 "hey Davis", "hey Marvis"):
        assert must in wt.HARD_NEG_EN


def test_hard_negatives_are_rendered_for_training_unless_switched_off(tmp_path):
    seen = []

    def spy(text, voice, rate, pitch):
        seen.append(text)
        return fake_synth(text, voice, rate, pitch)
    small_train(tmp_path / "m.npz", synth=spy)
    assert {"hey Travis", "hey service", "hey Garvis", "היי טרוויס"} <= set(seen)
    seen.clear()
    small_train(tmp_path / "n.npz", synth=spy, hard_negatives=False)
    assert "hey Garvis" not in seen and "hey Marvis" not in seen


def test_a_hard_negative_equal_to_the_phrase_is_never_trained_against(tmp_path):
    seen_neg = []

    def spy(text, voice, rate, pitch):
        seen_neg.append(text)
        return fake_synth(text, voice, rate, pitch)
    rep = wt.train_phrase("hey Garvis", tmp_path / "g.npz", voices=VOICES, synth=spy, embedder=fake_embedder,
                          rates=(1.0,), pitches=(1.0,), progress=lambda s: None)
    assert rep["hard_negative_phrases"] == len(wt.HARD_NEG_EN) - 1 + len(wt.HARD_NEG_HE)


def test_sample_weight_makes_a_head_fear_the_weighted_negatives():
    rng = np.random.default_rng(1)
    x = rng.standard_normal((400, wt.WINDOWS, wt.EMB)).astype(np.float32)
    y = np.zeros(400, int)
    y[:60] = 1
    x[:60] += 2.0
    x[60:120] += 1.2                                   # confusable negatives, between the two classes
    plain = wt.train_head(x, y, seed=3, epochs=15)
    w = np.ones(400, np.float32)
    w[60:120] = 10.0
    hard = wt.train_head(x, y, seed=3, epochs=15, sample_weight=w)
    assert wt.head_score(hard, x[60:120]).mean() < wt.head_score(plain, x[60:120]).mean()
    same = wt.train_head(x, y, seed=3, epochs=15, sample_weight=np.ones(400))
    assert all(np.array_equal(plain[k], same[k]) for k in ("w1", "w2", "b1", "b2"))


def test_calibrate_moves_up_until_the_near_miss_budget_holds(monkeypatch):
    peaks = [0.9, 0.85, 0.5, 0.45]
    embs, last = np.zeros((40, wt.EMB), np.float32), np.arange(40)
    monkeypatch.setattr(wt, "evaluate", lambda *a, **k: {"recall": 1.0, "false_per_hour": 0.0, "false_wakes": 0,
                                                         "hours": 1, "positives": 1})
    near = [{"text": "hey Marvin", "voice": "v", "score": 0.8}, {"text": "Jarvis", "voice": "v", "score": 0.99}]
    th, _, curve = wp.calibrate({}, embs, last, [], 100, peaks, [3], near_peaks=near, near_budget=0)
    assert th == 0.85 and curve[0]["near_miss_false"] == 1 and curve[-1]["near_miss_false"] == 0   # bare never counts
    th, _, _ = wp.calibrate({}, embs, last, [], 100, peaks, [3], near_peaks=near, near_budget=1)
    assert th == 0.45
    assert wp.THRESHOLDS[-1] == 0.99
    # nothing under 0.5/h: the false-wake budget (the model being replaced) decides instead of "the highest threshold"
    monkeypatch.setattr(wt, "evaluate", lambda a, e, l, ev, n, th: {"recall": 1.0, "false_per_hour": 30.0 - 20 * th,
                                                                    "false_wakes": 1, "hours": 1, "positives": 1})
    th, _, _ = wp.calibrate({}, embs, last, [], 100, [0.5] * 4, [3], fph_budget=20.0)
    assert th == 0.5 and wp.calibrate({}, embs, last, [], 100, [0.99] * 4, [3])[0] == 0.99


def _base(**kw):
    return {"name": "standard hey_jarvis", "threshold": 0.5, "near_miss_false": 2, "near_miss_rendered": 31,
            "campaign_false": 5, "campaign_rendered": 32, "bare_name_wakes": 1, "owner_recall": 0.7,
            "owner_held_out_recall": 0.75, "false_per_hour": 40.0, "synthetic_recall": 0.8, **kw}


def _cand(wakes=2, camp=3, fph=20.0, recall=0.95):
    w = [{"text": f"n{i}", "voice": "v", "score": 0.99} for i in range(wakes)]
    return {"threshold": 0.95, "near_miss": {"false_wakes": w, "rendered": 31, "bare_name_wakes": []},
            "campaign": {"false_wakes": [{}] * camp, "rendered": 32, "bare_name_wakes": []},
            "eval": {"recall": 0.9, "false_per_hour": fph},
            "owner": {"recordings": 20, "recall_at_threshold": recall, "held_out_indices": [3],
                      "held_out_recall_at_threshold": 1.0},
            "baseline": _base()}


def test_gate_is_relative_when_the_model_being_replaced_fails_the_absolute_bar():
    assert wp.gate(_cand()) == []                                   # 2 near-miss wakes = the standard's 2: fine
    why = wp.gate(_cand(wakes=3))
    assert "near-miss" in why[0] and "wakes on 2" in why[0]
    assert any("campaign" in w for w in wp.gate(_cand(camp=6)))
    assert any("false wakes/hour" in w for w in wp.gate(_cand(fph=41.0)))
    assert any("must beat it" in w for w in wp.gate(_cand(recall=0.75)))            # 0.7 + 0.10 margin needed
    assert wp.gate(_cand(recall=0.8)) == []
    c = _cand(wakes=1)                                              # no baseline / a clean one: zero near-miss wakes
    assert wp.gate({**c, "baseline": None}) != [] and wp.gate({**c, "baseline": _base(near_miss_false=0)}) != []
    assert wp.gate({**_cand(recall=1.0), "baseline": _base(owner_recall=0.97)}) == []   # no margin above 100 %


def test_bare_name_is_reported_but_never_gates():
    peaks = [{"text": "Jarvis", "voice": "v", "score": 0.99}, {"text": "hey Marvin", "voice": "v", "score": 0.2}]
    nm = wp.near_miss_check(None, 0.9, [], None, None, peaks=peaks)
    assert nm["false_wakes"] == [] and [w["text"] for w in nm["bare_name_wakes"]] == ["Jarvis"]
    assert nm["worst_score"] == 0.2


def test_baseline_is_measured_on_the_same_renderings():
    rendered = wp.render_near(VOICES, fake_synth, wp.NEAR_MISS_HELDOUT + wp.NEAR_MISS_SENTENCES)
    audio, events = wt.build_stream([(np.zeros(8000, np.float32), True)] * 3, np.random.default_rng(0))
    own = [phrase_audio(np.random.default_rng(i)) for i in range(4)]
    hot = wp.measure_baseline(FlatScorer(0.9), rendered, own, audio, events)
    assert hot["near_miss_false"] == hot["near_miss_rendered"] == 14 * 2 + 3 and hot["campaign_false"] == 11 * 2 + 7
    assert hot["owner_recall"] == 1.0 and hot["bare_name_wakes"] == 3            # "Jarvis" x2 en voices, he x1
    cold = wp.measure_baseline(FlatScorer(0.1), rendered, own, audio, events)
    assert cold["near_miss_false"] == 0 and cold["owner_recall"] == 0.0


def test_train_phrase_reports_the_baseline_and_the_comparison_table(tmp_path):
    rep = small_train(tmp_path / "m.npz", baseline=FlatScorer(0.9))
    assert rep["baseline"]["near_miss_false"] == 31 and rep["baseline"]["name"] == "standard fake"
    table = "\n".join(wp.format_comparison(rep))
    assert "candidate" in table and "standard fake" in table and "near-miss false wakes" in table
    assert all("near-miss" not in w for w in wp.gate(rep))                       # 0 <= the baseline's 31
    none = small_train(tmp_path / "n.npz")
    assert none["baseline"] is None and wp.format_comparison(none) == []
