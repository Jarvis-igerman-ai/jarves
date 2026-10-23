# Wake fixes from the owner's PC (4.0.1-beta.18) - 2026-10-23

Branch `fix/wake-apply-threshold` (local, not pushed; push after PR #106 merges).

## A) `wake test --apply` ignored
Cause: `Identity.apply_to_config` replaces `voice.wake_threshold` with the threshold stored in identity.json (set by the trained head) at every start, so the config.toml value written by `--apply` never won. Fix: `cli.apply_wake_threshold` writes identity.json when a custom model with a stored threshold is active (else config.toml) and says which file; `apply_to_config` logs `WAKE_THRESHOLD_OVERRIDDEN` when config.toml differs.

## B) Slow / rejecting confirm stage
- Slow: default `voice.wake_confirm_model = "small"` (int8, CPU, competing with the wake loop for cores); Whisper pads to 30 s, so a 2 s clip still costs a full encoder pass. Default is now `base.en` (about 3x faster, English-only decoding was already forced). Not measured on the owner's PC.
- `name_match("Hi, Javis.")` was already True in code, so the logged rejections came from other transcripts (the log deliberately omits the text). Made it more tolerant: y- initial, b/w for v, -sh/-z endings (Yarvis, Jarbis, Jarvish). Travis/Charles/Davis/Harvis/Marvis are still rejected.
- Open: to see the real transcripts run `jarvis wake test` (prints what the speech check heard). Owners with `wake_confirm_model = "small"` in config.toml keep it.

## C) gain_db 0.0 after calibration
The load path (`_wake_cfg` -> `AutoGain(initial_gain_db)`) is correct. The live tracker decayed the loaded +2.3 dB to 0 as soon as speech was seen (its target was clamped at 0). Fix: the calibrated gain is now the tracker's minimum (`AutoGain.min_gain_db`). A mic-name mismatch is not ruled out on the owner's PC (Core logs `mic=`; compare with the key in `wake/calibration.json`).

## Unverified
No real mic: speed gain, accent matching and gain behaviour on hardware need a manual `jarvis wake test` + `jarvis start` check.
