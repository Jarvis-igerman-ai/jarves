# SW-V3 campaign PASS C — voice (#17, branch `v3-campaign-voice`, 20 Oct 2026)

**Status: IN PROGRESS** (this file is updated after every group).

Method: `core/tools/suite_v3_voice.py` = every `suite_voice.py` scenario (1-35) + new V3 scenarios (36-47). Real
Core, real GPT-Live (`gpt-live-1` -> `gpt-6-luna`), real openWakeWord in the Core wake loop; virtual mic (WinRT
speech, never played) and virtual speaker (measured, never played); a fake face client. Every temp home starts as
the offline **TEST profile** (`profiles._write_test_defaults`, `profile.json` kind test) and only voice + API-key text
+ web search (and helper / browser / profiles per scenario) are switched on. The owner's key is loaded from his
environment and never printed. Approval popups are real but invisible (`--test-invoke hold`); browser/WhatsApp
opens and profile-switch spawns are recorded, never executed. One Core and one audio session at a time.
Raw per-turn report: [swv3_campaign_voice/SUITE_RAW_REPORT.md](swv3_campaign_voice/SUITE_RAW_REPORT.md).
Bugs: [SWV3_CAMPAIGN_BUG_TABLE_VOICE.md](SWV3_CAMPAIGN_BUG_TABLE_VOICE.md).

## Results so far

| # | Scenario | Result | Evidence / key number |
|---|---|---|---|
| 1 | greeting | PASS | first sound 0.52 s after face connect, greeting 2.5 s |
| 2 | smalltalk | PASS | 3/3, dead air avg 0.30 s |
| 3 | time_date | PASS | hour + day correct, no delegation |
| 4 | maths | PASS | 4/4 (delegated; screen shows "searching": CV-08) |
| 5-7 | web search weather/news/facts | PASS | backend 2.2 / 6.4 / 4.5 s |
| 8-10 | emotions | PASS | laugh / sad / love |
| 11 | memory_recall | PASS | stored + recalled 37 |
| 12-14 | forget approve/deny/timeout | PASS | deleted / kept / kept; #14 spoken hint wrong (CV-02, fixed) |
| 15 | long_conversation (26 turns) | PASS | 26/26 answered, 9/10 correct, 0 disconnects, context name/city/plan recalled, voice 319 s vs wall 317 s |
| 16 | rapid_fire | PASS | 6/6, dead air 0.48-0.77 s |
| 17 | long_utterance (40 s) | PASS | Eilat, dead air 0.32 s |
| 18 | english_switch | FAIL | answered "קנברה." in Hebrew to an English question (CV-05) |
| 19 | noise_robustness | PASS | 0.0 s spoken over 40 s noise |
| 20 | idle_disconnect | PASS | ended 14.7 s after last sound |
| 21 | wake_word_x3 | PASS | 3/3, scores 0.47-0.48, connect 2.5-3.5 s |
| 22 | wake_false_positives | FAIL | 4/20 (CV-04, = 19 Oct baseline) |
| 23 | wake_thresholds | FAIL | detect 10/10 at both; false 4/20 (0.15), 3/20 (0.35) (CV-04) |
| 24 | software_mute | PASS | mics closed, muted wake lost, unmute wake works |
| 25 | budget_voice | PASS | VOICE_BUDGET, 60 s, no reservations |
| 26 | budget_requests | PASS | AI_BUDGET, 2 requests |
| 27 | face_protocol | PASS | 44 states, mic.level 4.1 Hz |
| 28 | start_stop_x5 | PASS | 5/5 clean |
| 29 | log_privacy | FAIL -> fixed | curator facts leaked (CV-01); retest below |
| 30 | idle_soak_6min | PASS | 406 s, 26 WAKE_HEALTH, 0 false wakes |
| 31 | false_wake_closes | PASS | closed after 14.8 s |
| 45 | wake_variants (no live cost) | PASS | Hey Jarvis normal/quiet clean+noise 8/8; Hi Jarvis 12/12; ok/Okay Jarvis 12/12; Hebrew-script 2/12 (CV-07) |

## Log review (WARN/ERROR classification)
- `LINK_FACE_DISCONNECTED` (1 per home): the fake face closes before `core.stop()` without `desktop.close` — expected (harness).
- `LIVE_GREETING_NUDGE` (1, group 19-24): no server audio 6 s after `session.started`; the documented P0.4 Q05 mitigation sent the clock note, greeting followed — expected (server-side stall).
- `LIVE_FALSE_ACTION_CLAIM` (1, #15): real, CV-06.
- No ERROR lines so far.

## Cost so far
Live voice (conservative wall time while a session is open): 22.0 min ≈ $1.10 + backend tokens/web searches (< $0.10).
