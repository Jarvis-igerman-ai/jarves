"""Owner-voice wake experiment (docs/reviews/SWV4_WAKE_TRAIN.md): custom "hey jarvis" head vs the standard model.

  python tools/wake_owner_eval.py train  [--splits 0,1,2]   # one head per hold-out split (+ "all" with --all)
  python tools/wake_owner_eval.py eval                      # score traces, threshold sweep, real-listener check

Everything stays local: the owner's clips in <state>/wake/samples are only READ (copied to a scratch folder per
split), TTS is the local Windows voices, models/reports go to <state>/wake/owner_eval (never into git).

Method
- 24 owner clips, three stratified splits (16 train / 8 test, by "hey"/"hi"/"ok").
- train: wake_train.train_phrase("hey jarvis", say=["hi jarvis", "ok jarvis"], samples=<16 train clips>) =
  synthetic local-TTS corpus + the owner's training clips, own_test_every=0 (our own split is the hold-out).
- eval: one score trace per model (detector.feed per 80 ms frame, as WakeListener calls it) over
  (a) the owner's 8 hold-out clips per split: as recorded / -20 dB (far) / + TV babble at 10 dB SNR,
  (b) >= 24 min non-wake audio (wake_eval.build_corpus) + the known false-positive phrases rendered by every
      local voice ("hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles"),
  (c) synthetic eval-voice positives (secondary).
  Thresholds are swept offline on the traces (2 s cooldown, like WakeListener); the chosen thresholds are then
  re-run through the REAL WakeListener (wake_eval.run_listener) to confirm.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jarves.voice import wake_eval as we  # noqa: E402
from jarves.voice.wake_train import _wav_bytes_to_np, local_voices, resample, train_phrase  # noqa: E402
from jarves.voice.wakeword import make_detector  # noqa: E402

SR = 16000
STATE = Path(os.environ.get("JARVES_HOME") or Path(os.environ["LOCALAPPDATA"]) / "jarves")
SAMPLES = STATE / "wake" / "samples"
OUT = STATE / "wake" / "owner_eval"
KNOWN_FP = ["hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles"]
SEEDS = (101, 202, 303)
TH_GRID = [round(x, 3) for x in np.concatenate([np.arange(0.02, 0.1, 0.01), np.arange(0.1, 0.99, 0.025)])]


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(OUT / "progress.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def clip_files() -> list[Path]:
    return sorted(SAMPLES.glob("*.wav"))


def kind(p: Path) -> str:
    return "ok" if "_ok_" in p.name else "hi" if "_hi_" in p.name else "hey"


def splits() -> list[dict]:
    """Stratified 16/8 splits: 4 hey + 3 hi + 1 ok in each test set (12 hey / 10 hi / 2 ok recorded)."""
    files = clip_files()
    out = []
    for k, seed in enumerate(SEEDS):
        rng = np.random.default_rng(seed)
        test = []
        for kd, n in (("hey", 4), ("hi", 3), ("ok", 1)):
            pool = [f for f in files if kind(f) == kd]
            test += [pool[i] for i in rng.choice(len(pool), min(n, len(pool)), replace=False)]
        out.append({"split": k, "test": sorted(f.name for f in test),
                    "train": sorted(f.name for f in files if f not in test)})
    return out


# ------------------------------------------------------------------------------------------------ train
def cmd_train(a) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    voices = local_voices()
    sp = splits()
    (OUT / "splits.json").write_text(json.dumps(sp, indent=1), encoding="utf-8")
    jobs = [s for s in sp if str(s["split"]) in a.splits.split(",")]
    if a.all:
        jobs.append({"split": "all", "train": [f.name for f in clip_files()], "test": []})
    for s in jobs:
        tmp = OUT / f"train_clips_{s['split']}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        for n in s["train"]:
            shutil.copy2(SAMPLES / n, tmp / n)                 # read-only use of the owner's folder
        log(f"train split {s['split']}: {len(s['train'])} owner clips + synthetic ({len(voices)} voices)")
        t0 = time.monotonic()
        rep = train_phrase("hey jarvis", OUT / f"hey_jarvis_owner_{s['split']}.npz", say=["hi jarvis", "ok jarvis"],
                           samples=tmp, voices=voices, own_test_every=0, progress=lambda m: log("  " + m))
        shutil.rmtree(tmp, ignore_errors=True)
        log(f"train split {s['split']} done in {time.monotonic() - t0:.0f} s: internal synthetic threshold "
            f"{rep['threshold']} eval {rep['eval']}")


# ------------------------------------------------------------------------------------------------ eval
def speech_bounds(a: np.ndarray) -> tuple[int, int]:
    """Start/end sample of the speech in a clip: 20 ms frames within 30 dB of the loudest frame."""
    f = a[: len(a) // 320 * 320].reshape(-1, 320)
    db = 20 * np.log10(np.sqrt((f ** 2).mean(1)) + 1e-9)
    idx = np.flatnonzero(db > db.max() - 30)
    return int(idx[0] * 320), int((idx[-1] + 1) * 320)


def owner_segments(names: list[str], cond: str, babble: np.ndarray, seed: int):
    rng = np.random.default_rng(seed)
    yield we.room_floor(SR, rng), None
    for n in names:
        a = read_samples_one(SAMPLES / n)
        s0, s1 = speech_bounds(a)
        if cond == "far":
            a = a * 0.1                                        # -20 dB: the same voice ~3-4x further away
        elif cond == "babble10":
            o = int(rng.integers(0, len(babble) - len(a)))
            b = babble[o:o + len(a)]
            a = a + b / (np.sqrt(np.mean(b ** 2)) + 1e-9) * float(np.sqrt(np.mean(a[s0:s1] ** 2))) / 10 ** 0.5
        seg = np.concatenate([a, np.zeros(int(SR * 3.0), np.float32)])
        yield seg + we.room_floor(len(seg), rng), {"len": len(a), "text": n, "voice": "owner", "cond": cond,
                                                   "kind": "pos", "s0": s0, "s1": s1}


def read_samples_one(p: Path) -> np.ndarray:
    a, sr = _wav_bytes_to_np(p.read_bytes())
    return np.clip(resample(a, sr), -1, 1).astype(np.float32)


def trace(det, segments) -> dict:
    """Feed every 80 ms frame (as WakeListener does) and keep the raw score; events get absolute positions."""
    det.threshold = 1.01
    det._load_sync()
    scores, events, total, carry = [], [], 0, b""
    t0 = time.perf_counter()
    for seg, ev in segments:
        if ev is not None:
            ev = dict(ev, start=total, end=total + ev["len"])
            if "s1" in ev:
                ev["speech_end"] = total + ev["s1"]
            events.append(ev)
        total += len(seg)
        carry += (np.clip(seg, -1, 1) * 32767).astype("<i2").tobytes()
        while len(carry) >= 2560:
            det.feed(carry[:2560])
            carry = carry[2560:]
            scores.append(det.last_score)
    wall = time.perf_counter() - t0
    return {"scores": np.asarray(scores, np.float32), "events": events, "samples": total,
            "ms_per_frame": round(wall * 1000 / max(1, len(scores)), 3)}


def wakes_at(tr: dict, th: float, cooldown: float = 2.0) -> list[int]:
    out, cool = [], -1.0
    for i, s in enumerate(tr["scores"]):
        t = (i + 1) * 1280
        if s >= th and t >= cool:
            out.append(t)
            cool = t + cooldown * SR
    return out


def pos_metrics(tr: dict, th: float) -> dict:
    w = np.asarray(wakes_at(tr, th))
    hits, lat = [], []
    for e in tr["events"]:
        m = (w >= e["start"]) & (w <= e["end"] + SR) if len(w) else np.zeros(0, bool)
        hits.append(bool(m.any()))
        if m.any() and "speech_end" in e:
            lat.append((w[m][0] - e["speech_end"]) / SR * 1000)
    return {"hit": int(sum(hits)), "n": len(hits), "lat_ms": lat,
            "missed": [e["text"] for e, h in zip(tr["events"], hits) if not h]}


def neg_metrics(tr: dict, th: float) -> dict:
    w = wakes_at(tr, th)
    r = we.score_negatives(w, tr["events"], tr["samples"])
    fp = sum(1 for x in w for e in tr["events"] if e["text"] in KNOWN_FP and e["start"] - 0.2 * SR <= x <= e["end"] + SR)
    r["all_incl_bare"] = r["false_wakes"] + r["bare_name_wakes"]
    r["per_hour_incl_bare"] = round(r["all_incl_bare"] / (tr["samples"] / SR / 3600), 2)
    r["known_fp_wakes"] = fp
    r["known_fp_clips"] = sum(e["text"] in KNOWN_FP for e in tr["events"])
    return r


def cmd_eval(a) -> None:
    sp = json.loads((OUT / "splits.json").read_text(encoding="utf-8"))
    cache = STATE / "wake" / "eval_cache"
    corpus = we.build_corpus(cache, minutes=a.minutes, progress=log)
    fp = we.local_clips(KNOWN_FP, "near", cache, rates=(0.9, 1.0, 1.15), split="all")
    for c in fp:
        if c.text in we.BARE:
            c.kind = "bare"
    corpus.neg += fp * 3
    log(f"negatives: {len(corpus.neg)} clips incl. {len(fp)} known-FP renders x3")
    models = {"standard": "hey_jarvis"}
    for s in sp:
        p = OUT / f"hey_jarvis_owner_{s['split']}.npz"
        if p.exists():
            models[f"custom_{s['split']}"] = str(p)
    if (OUT / "hey_jarvis_owner_all.npz").exists():
        models["custom_all"] = str(OUT / "hey_jarvis_owner_all.npz")
    traces: dict = {}
    conds = ("raw", "far", "babble10")
    for name, path in models.items():
        t0 = time.monotonic()
        tr = {"neg": trace(make_detector(path, 1.01), corpus.negatives()),
              "syn": trace(make_detector(path, 1.01), corpus.positives())}
        for s in sp:
            for c in conds:
                tr[f"own{s['split']}_{c}"] = trace(make_detector(path, 1.01),
                                                   owner_segments(s["test"], c, corpus.babble, 7 + s["split"]))
        traces[name] = tr
        log(f"traces {name}: {time.monotonic() - t0:.0f} s, {tr['neg']['samples'] / SR / 60:.1f} neg min, "
            f"{tr['neg']['ms_per_frame']} ms/frame")
    # ---- sweep
    rows = {}
    for name, tr in traces.items():
        own_split = [s["split"] for s in sp] if name in ("standard", "custom_all") else [int(name.split("_")[1])]
        sweep = []
        for th in TH_GRID:
            n = neg_metrics(tr["neg"], th)
            r = {"th": th, "fph": n["per_hour_incl_bare"], "fw": n["all_incl_bare"], "fw_excl_bare": n["false_wakes"],
                 "bare": n["bare_name_wakes"], "known_fp": n["known_fp_wakes"], "fw_texts": n["texts"]}
            for c in conds:
                ms = [pos_metrics(tr[f"own{k}_{c}"], th) for k in own_split]
                r[f"own_{c}"] = [m["hit"] for m in ms]
                r[f"own_{c}_n"] = [m["n"] for m in ms]
                if c == "raw":
                    lat = [x for m in ms for x in m["lat_ms"]]
                    r["lat_ms_median"] = round(float(np.median(lat)), 0) if lat else None
                    r["missed_raw"] = [x for m in ms for x in m["missed"]]
            syn = pos_metrics(tr["syn"], th)
            r["syn"] = round(syn["hit"] / max(1, syn["n"]), 3)
            sweep.append(r)
        rows[name] = {"model": models[name], "ms_per_frame": tr["neg"]["ms_per_frame"],
                      "neg_minutes": round(tr["neg"]["samples"] / SR / 60, 1), "sweep": sweep}
    res = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "splits": sp, "corpus": corpus.info,
           "known_fp": KNOWN_FP, "models": rows}
    (OUT / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"saved {OUT / 'results.json'}")
    if a.verify:
        verify(res, corpus, a.verify)


def verify(res: dict, corpus, spec: str) -> None:
    """Re-run name@threshold pairs through the real WakeListener (negatives + owner raw hold-out)."""
    out = {}
    for item in spec.split(","):
        name, th = item.split("@")
        path = res["models"][name]["model"]
        wakes, ev, st = asyncio.run(we.run_listener(make_detector(path, float(th)), corpus.negatives()))
        n = we.score_negatives(wakes, ev, st["samples"])
        hits = 0
        tot = 0
        ks = [s["split"] for s in res["splits"]] if name in ("standard", "custom_all") else [int(name.split("_")[1])]
        for k in ks:
            s = res["splits"][k]
            w, e, _ = asyncio.run(we.run_listener(make_detector(path, float(th)),
                                                  owner_segments(s["test"], "raw", corpus.babble, 7 + k)))
            p = we.score_positives(w, e)
            hits, tot = hits + p["detected"], tot + p["positives"]
        out[item] = {"neg": n, "own_raw": f"{hits}/{tot}", "cpu": st}
        log(f"verify {item}: own raw {hits}/{tot}, false wakes {n['false_wakes']} (+{n['bare_name_wakes']} bare "
            f"'Jarvis') in {n['minutes']} min, avg {st['avg_cpu_ms']} ms/frame")
    (OUT / "verify.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("train", "eval", "verify"))
    ap.add_argument("--splits", default="0,1,2")
    ap.add_argument("--all", action="store_true", help="train: also a head on all 24 clips")
    ap.add_argument("--minutes", type=float, default=24.0)
    ap.add_argument("--verify", default="", help="eval/verify: name@th,... re-run through the real WakeListener")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.stage == "train":
        cmd_train(a)
    elif a.stage == "eval":
        cmd_eval(a)
    else:
        res = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
        corpus = we.build_corpus(STATE / "wake" / "eval_cache", minutes=a.minutes, progress=log)
        fp = we.local_clips(KNOWN_FP, "near", STATE / "wake" / "eval_cache", rates=(0.9, 1.0, 1.15), split="all")
        for c in fp:
            if c.text in we.BARE:
                c.kind = "bare"
        corpus.neg += fp * 3
        verify(res, corpus, a.verify)


if __name__ == "__main__":
    main()
