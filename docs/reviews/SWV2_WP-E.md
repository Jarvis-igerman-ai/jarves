# SW-V2 WP-E: Issue #16 review findings, volume/mute UX (#17 item 7), all suites

Owner: WP-E (music Skill, voice test suites). Issue #16 stays OPEN: the lead closes it after review.

## Triage of the Issue #16 findings

| Finding | Triage | Evidence | Change |
|---|---|---|---|
| P1 stalled playback reported as playing | **Confirmed on real Pear; a Pear API limit** | Muted probe: a song really played 27 -> 36 s while REST `elapsedSeconds` and ws `position` both read 27. Run 4: the stalled "Get Lucky (Radio Edit)" was reported playing. | Pear offers no progress signal. Results say `playback: "player_reports_playing"` and never claim the song is audible. An ignored pause gets "restart Pear Desktop". Requiring elapsed > 0 stays out (0/10 false negatives on 19 Oct). |
| P2 title-only match / page-level relevance | **Confirmed bug** | Offline regressions; real S11 | Title AND artist must match when both name one in the same script. The video->song swap stays allowed. Relevance is checked per playable candidate (whole words, Hebrew one-letter prefixes). Songs inside an artist card inherit the card's name. |
| P2 queue trim mutates history | **Design choice, now explicit** | n/a | `music.trim_queue` (default true, documented in config.example.toml). `MusicSkill.close()` cancels a running trim on Core shutdown. |
| P2 25 s budget vs candidates | **Confirmed risk, fixed** | Offline slow/stuck-player tests | One end-to-end deadline (21 s, always 2 s or more inside the tool timeout) covers search, every candidate's queue work and confirm polling. A song the player skips to fails that candidate fast. One last look catches a late start. A timed-out Pear request is checked rather than abandoned. |
| Risk: long utterance vs 12 s no-speech cutoff | **Confirmed in a deterministic simulation** | `test_long_first_utterance_*` FAILS with the old rule at 20 s and 35 s. Live scenario 17 (40.6 s) and 31 (false wake closes in 14.9 s) PASS. | The cutoff waits while the mic hears the user, at most +30 s (`ENERGY_EXTEND_S`). Noise-only sessions still close by ~50 s. |
| Risk: dropped audio vs caption/face | **Confirmed in simulation, fixed** | `test_caption_of_a_dropped_early_answer_is_never_shown` FAILS without the fix | Assistant text is held while the user talks and JARVES is silent. It is released when its audio really plays and discarded when that audio is dropped. Limit: text arriving more than 1 s after its dropped audio can still show (no shared response id). |

## Volume / mute UX (real session 19 Oct 14:14)

What went wrong: "תגביר" jumped to the 70 cap. "כמה אחוז ווליום" was answered from memory. "תעשה מיוט" called music_pause.

- `music_volume` takes `change: "up"|"down"` (default step 10, `music.volume_step`) or `percent`. It starts from the **real** level and reads the level back after setting it. At the cap it does not change and says why.
- Pear's GET state is converted to the slider value using a **measured** curve, not 10·sqrt(state), which was 5-7 points off mid-range: 0->0, 10->2, 20->5, 30->8, 40->13, 50->20, 60->29, 70->40, 80->55, 90->74, 100->100.
- `music_status` reports volume and mute.
- New `music_mute` / `music_unmute` use `/toggle-mute`: the song keeps playing, and the result is verified. They are added to `DEFAULT_ALLOWED_ACTIONS`. Tool descriptions tell the model that mute is not pause.

## Tests run (this package)

- pytest: **442 passed, 1 skipped** (final commit; ~50 new tests in `core/tests/test_issue16.py`).
- suite_system: 12/12 + S01 (Godot viewer/face checks PASS, face protocol test PASS, 3-min soak).
- suite_voice (real GPT-Live, silent): scenarios 1, 17, 19, 31 PASS. 22/23 FAIL is the known openWakeWord false-wake limit (4/20 at 0.15, 3/20 at 0.35). 2.1 live min ≈ **$0.11**. The other rows were not re-run.
- suite_music (real Pear, volume 0, watchdog, restored): five runs, see the top of PRE_USE_TEST_REPORT_MUSIC.md. The best run was skill 12/13 + errors/robust 7/7. In the last run Pear itself stalled (GET /queue 14.9 s, a stuck video ignored pause). Pear was left paused at slider 70, unmuted, as found. **The voice group (V01-V10) and the K01 soak were not completed on this build.**
- Muted/paused probes of Pear (ws, volume curve, mute) for the evidence above. Pear was always restored to state 40, unmuted, paused.

Cost: about $0.11 live voice, plus the five ChatGPT-plan text questions in suite_system (plan, not API).

## Open questions (for HANDOFF decision queue)

1. **trim_queue default** (USER DECISION): keep trimming Pear's played history (current default, keeps Pear fast) or default to off? Recommendation: keep it on. The queue reached 60 items / 1 MB during these runs, and a slow /queue is what timed out plays.
2. **Stalled Pear** (BLOCKED on Pear): no API signal tells a stalled video from a playing one. Should JARVES, after an ignored pause, offer to restart Pear itself? Recommendation: no automatic restart; keep the message.
3. Real-Pear re-verification owed after a fresh Pear start, with enough free RAM: S01-S13, K01 soak, V01-V10, and mute/relative volume by voice.

## Integration hooks (files outside WP-E ownership, minimal)

- `core/src/jarves/skills/__init__.py`: `music_mute`, `music_unmute` added to `DEFAULT_ALLOWED_ACTIONS`.
- `core/src/jarves/config.py` + `core/config.example.toml`: `music.volume_step`, `music.trim_queue`.
- `core/src/jarves/app.py`: passes these two settings to MusicSkill; shutdown calls `MusicSkill.close()`.
- `core/src/jarves/voice/live.py` (lifecycle, not persona/delegation parts): no-speech cutoff and caption hold.
- Not done (owned by WP-C): an offline fast path in `brain.py` for "השתק"/"מיוט" -> music_mute and "תגביר"/"תנמיך" -> relative volume. Today these go through the model.
