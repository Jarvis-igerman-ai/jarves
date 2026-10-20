# SWV4: custom "Hey Jarvis" head trained on the owner's voice (20 Oct 2026) - #17

Branch `v4-wake-train` (off `v3-final`). Everything ran locally on this PC. The owner's 24 WAV clips in
`%LOCALAPPDATA%\jarves\wake\samples` were only read: none were changed, uploaded or committed. No model binary is
in git either.

## Result in one paragraph

On held-out recordings of the owner (3-fold cross-validation, 24 clips), the custom head at threshold **0.95**
detects **21/24 (88 %)**. The standard `hey_jarvis` model at the owner's working threshold 0.15 detects **19/24
(79 %)**. Over all three listening conditions the score is **65/72 vs 56/72**. On the 24-minute non-wake corpus it
gives **23-32 false wakes per fold instead of 42** (57-80/h instead of 105/h, measured with the real
`WakeListener`). On the 20 voice-campaign phrases it wakes on **2-3/20 vs 3/20**, and on the same phrases in all 4
voices **7-9/56 vs 14/56**. It is better on both axes, so it was **applied** at threshold 0.95. It is not better on
latency: the median is +40 ms, the p90 is +150 ms and the worst case is about 1.1 s. CPU per frame is the same
(about 6 ms per 80 ms). Revert with `jarvis wake train --revert`.

## What was done

1. **Tooling.** I used the existing `jarvis wake train` / `voice/wake_train.py`: openWakeWord's frozen melspectrogram and
   embedding front end plus a numpy MLP head (16 x 96 -> 32 ReLU -> sigmoid). Training data was the local Windows TTS
   voices (David, Zira and Mark for the phrase; Asaf for Hebrew negatives), with 4 rates x 3 pitches x 2 augmented
   copies, plus the owner's clips (6 augmented copies each), negatives and noise. Phrase `hey jarvis` with
   `--say "hi jarvis" --say "ok jarvis"`.
2. **One change to the training data (round 2).** The first round's heads (stock negatives) caught every hold-out clip
   but woke 99-110/h even at 0.995. Almost every false wake was a greeting plus a similar name ("hey Charles",
   "hey Jarvie", "hey Travis"). I added `NEAR_EN` to `wake_train.py`: 28 "hey <name>" hard negatives ("hey Marcus",
   "hey Travers", "hey Gervais", ...). These are **deliberately different strings from the eval near-misses**
   (`wake_eval.NEAR_MISS` and the campaign list), so the false-wake numbers stay a real hold-out. Some are
   phonetically close ("hey Travers" vs "hey Travis") because that is what hard negatives are for. All numbers below
   come from round 2.
3. **Splits.** I used stratified 3-fold cross-validation (hey/hi/ok spread round-robin): each clip is tested exactly once, by a model that never saw
   it. Each fold model trained on 16 clips and was tested on 8. `train_phrase` keeps every 4th training clip for its
   own synthetic threshold check, so effectively 12 clips were trained on.
   The test clips per fold (numbers = the clip suffix):
   - fold 0: hey 01, 07, 13, 19; hi 03, 10, 18, 24
   - fold 1: hey 02, 09, 15, 21; hi 06, 12, 20; ok 04
   - fold 2: hey 05, 11, 17, 23; hi 08, 16, 22; ok 14

   The applied model was then trained on all 24 clips (with the same CLI command, see "Applied").
4. **Measurement.** Each eval stream went once through the real openWakeWord front end. I recorded per-80 ms scores
   for `hey_jarvis` and for each head, then replayed every threshold with the 2 s cooldown. The chosen operating
   points were then **re-run with the real `WakeListener`** on a virtual mic (`wake_eval.run_listener`, the class the
   Core uses), and the numbers matched within 1 false wake.
   - Owner hold-out: 8 clips per fold, 3 s of room tone apart. Conditions: **raw** (as recorded), **pink10** (+ pink
     noise at 10 dB SNR) and **quiet** (x0.25 gain). A clip counts as detected if a wake falls in [clip start, clip
     end + 1 s].
   - Non-wake corpus: the `wake eval` default (`build_corpus`, 24 min, local voices, seed 11). It has 597 everyday
     He/En sentences, TV-like mixes, music and noise, and 56 near-miss clips x 3. Bare "Jarvis" is reported apart, as
     in `wake eval`.
   - Campaign phrases: the 20 `NEGATIVES` of `core/tools/suite_voice.py` (scenarios 22/23: "hey Travis", "Jarvis",
     "hey Jarvie, no", "Hey Charles, come here", ... and 8 Hebrew). They were rendered by WinRT TTS like the campaign
     (first voice of the language, rate 1.0, peak 0.6, so "camp20"), and again in all 4 local voices ("camp56").
     Here bare "Jarvis" **counts** as a false wake, as in the campaign. The standard model's peaks reproduce the
     campaign's offline peaks (hey Travis 0.45, Jarvis 0.99, hey Jarvie 0.99).
   - Latency: the wake time minus the phrase end (the last 30 ms frame above 10 % of the peak energy), in audio time.
     CPU is milliseconds per 80 ms frame of the real detector classes.

## Numbers (round 2; hold-out = cross-validation, each model on the clips it never saw)

| | standard `hey_jarvis` @0.15 (working) | custom head @0.95 (chosen) | custom @0.99 |
|---|---|---|---|
| Owner hold-out, raw | **19/24** (6/8, 5/8, 8/8) | **21/24** (8/8, 7/8, 6/8) | 21/24 |
| Owner hold-out, + pink noise 10 dB | 17/24 | 22/24 | 21/24 |
| Owner hold-out, quiet x0.25 | 20/24 | 22/24 | 22/24 |
| False wakes, 24 min corpus (real WakeListener) | **42 = 104.9/h** | **32 / 23 / 24 = 80 / 57 / 60 per h** (per fold) | 21 / 18 / 10 (offline) |
| ... of which not on training sentences | 41 | 29 / 22 / 21 | 19 / 16 / 9 |
| Bare "Jarvis" wakes in the corpus (reported apart) | 4-5 | 5-8 | 5-8 |
| Campaign 20 phrases (campaign voice) | **3/20** (hey Travis, Jarvis, hey Jarvie) | **2/20, 2/20, 3/20** (Jarvis, "A jar of peanut butter", +hey Jarvie in fold 2) | 1-2/20 |
| Campaign phrases x 4 voices | 14/56 | 7 / 7 / 9 of 56 | 5 / 4 / 7 |
| Latency after the phrase end, median / p90 / max | 270 / 558 / 620 ms | 310 / 710 / 1090 ms | |
| CPU per 80 ms frame (avg / p95) | 5.7 / 6.9 ms | 6.6 / 8.1 ms (5.3-6.0 ms inside WakeListener) | |

The standard model's own curve, for comparison: it never matches the custom detection at the custom false-wake level.
At 0.35 (the config default) it detects 17/24 with 28 false wakes. At 0.5 it detects 12/24 with 20 false wakes.

Clips missed:
- standard @0.15: hey_15, hey_19, hi_03, hi_06, hi_20 (soft "hi").
- custom @0.95: hey_15, hey_17, hi_16. Both models miss hey_15 (the hardest clip).

**Threshold choice.** The rule was the highest hold-out detection with false wakes <= the standard model's in
**every** fold, on both the corpus and the campaign phrases. 0.95 is the lowest threshold that keeps fold 2's
campaign count at 3/20 (at 0.90 it is 4/20). Any lower threshold has more false wakes than the standard model.
0.99 halves the corpus false wakes again for one fewer noisy detection; it is the fallback if the room proves noisy.

## Decision: APPLIED

The custom head is better on both measured axes: detection +2/24 raw and +9/72 over all conditions, and false wakes
-24 % to -45 % on the corpus, fewer on the campaign phrases. So it was applied with the real CLI:

```
jarvis wake train hey jarvis --say "hi jarvis" --say "ok jarvis" --samples %LOCALAPPDATA%\jarves\wake\samples --apply
```

Results of the apply run:
- It took 539 s and saved `%LOCALAPPDATA%\jarves\wake\hey_jarvis.npz` + `.json` (outside git).
- The identity now has `{"model": ...\wake\hey_jarvis.npz, "phrase": "hey jarvis", "threshold": 0.95}`.
- The CLI's own threshold pick was also 0.95.
- Its weights are **bit-identical** to the all-clips model measured above (training is deterministic: seed 7, same
  TTS renders). On the 24-min corpus that model gives 21 false wakes (52/h) through the real `WakeListener`, and
  3/20 on the campaign phrases.
- All 24 sample WAVs are untouched.

It takes effect at the next Core start (`jarvis stop`, then `jarvis`). The running Core was not restarted.

What it does NOT fix:
- **Bare "Jarvis"** still wakes it (it was a training negative, but the model still fires on the bare name).
- **"A jar of peanut butter"** is a new false wake.
- **Latency** is about 40 ms slower in the median, and up to about 1 s on soft clips.
- **A second wake within about 4 s of the previous one** can be missed. After a trigger, `HeadWakeDetector.reset()`
  zeroes its 16-embedding ring, so it needs about 2 s of fresh audio. With clips 3 s apart, the all-clips model got
  21/24 on its own clips; 6 s apart, 23/24. In normal use a conversation follows the wake, so this is rare.
- **The two-stage confirm was not measured.** The owner's config runs AGC plus a two-stage confirm: gate 0.02, instant
  at max(threshold, 0.5), so 0.95 here, plus a local Whisper "small" name check. That would have needed Whisper in
  RAM on a low-memory machine. With the custom head, scores between 0.02 and 0.95 go to the Whisper check, which
  rejects "hey Travis"-style names, so the shipped pipeline should be at least as good as the single-stage numbers
  above.

Evidence limits:
- It is 24 recordings of one speaker and one mic; the hold-out has 8 clips per fold.
- All negatives are synthetic TTS. The corpus reuses some sentences that are also training negatives (same texts),
  so the "not on training sentences" row is the cleaner number.
- Confirm in the room with `jarvis wake test` (your voice) and `jarvis wake test --false-wakes 30`.

## How to revert

- `jarvis wake train --revert` (new in this branch, test `tests/test_wake_train.py::test_cli_train_revert`). It
  switches back to the built-in "Hey Jarvis" with `voice.wake_threshold` from `config.toml` (0.15) and keeps the
  trained file.
- Or delete `%LOCALAPPDATA%\jarves\wake\hey_jarvis.npz` (and `.json`). `Identity.wake()` then logs
  `WAKE_MODEL_MISSING` and falls back to "Hey Jarvis".
- Either way, restart the Core.

## Code changes

- `core/src/jarves/cli.py`: `jarvis wake train --revert`.
- `core/src/jarves/voice/wake_train.py`: `NEAR_EN` hard negatives (disjoint from the eval lists).
- `core/tests/test_wake_train.py`: `test_cli_train_revert`.
- The experiment driver lived in the session scratchpad (not committed). It reuses `wake_train` and `wake_eval`
  functions only.
