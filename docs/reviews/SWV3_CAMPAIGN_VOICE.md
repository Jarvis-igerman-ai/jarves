# SW-V3 campaign PASS C — voice (#17, branch `v3-campaign-voice`, 20 Oct 2026)

**Result: 44 PASS, 3 FAIL, 0 BLOCKED of 47 scenarios** (latest run of each; every fixed failure retested).
Open FAILs: 18 english_switch (P3), 22/23 wake false positives (P3, known model limit). Scenario 46 (pronunciation)
counts as PASS on its first run's audio transcript with a caveat (see below).
**Cost: about $3.80 upper bound** of the $10 budget (live voice 60.0 min ≈ $3.00; backend 136 calls, 2.69 M input
tokens at the priority price ≤ $0.54; 25 web searches $0.25; 3 output-audio transcriptions ≈ $0.01).
Bugs: 7 P2 fixed (+ test/harness fixes), open 1 P2 (intermittent) + 9 P3; no P0/P1 found — [bug table](SWV3_CAMPAIGN_BUG_TABLE_VOICE.md).

## Method
`core/tools/suite_v3_voice.py` runs every `suite_voice.py` scenario (1-35) plus 12 new V3 scenarios (36-47).
Real Core, real GPT-Live (`gpt-live-1` -> `gpt-6-luna`, priority tier), real openWakeWord in the Core wake loop,
real Skills/approvals/helper/browser (headless Edge); virtual mic (WinRT speech, never played) and virtual speaker
(measured in real time, never played); a fake face client. Every temp home is created as the offline **TEST
profile** (`profiles._write_test_defaults` + `profile.json` kind test), then only what a scenario needs is switched on
on purpose: live voice + API-key text with the owner's key from his environment (never printed or logged — #29
scans every home for it: never found), web search, and per scenario helper / browser / extra profile. Approval
popups open for real but invisibly and never answer (`--test-invoke hold`); `webbrowser.open` (WhatsApp, dashboard)
and the profile-switch process spawn are recorded, never executed. One Core and one audio session at a time.
Raw per-turn data: [swv3_campaign_voice/SUITE_RAW_REPORT.md](swv3_campaign_voice/SUITE_RAW_REPORT.md).

## Results (latest run)

| # | Scenario | Result | Evidence / key number |
|---|---|---|---|
| 1 | greeting | PASS | first sound 0.52 s after face connect, greeting 2.5 s (x2 runs) |
| 2 | smalltalk | PASS | 3/3, dead air avg 0.14 s |
| 3 | time_date | PASS | after harness fix (it rejected the spelling "שתיים עשרה"); no delegation |
| 4 | maths | PASS | 4/4 (delegated; screen shows "searching" — CV-08) |
| 5-7 | web search weather/news/facts | PASS | backend 2.5 / 7.8 / 7.4 s |
| 8-10 | emotions | PASS | laugh / cry / love |
| 11 | memory_recall | PASS | stored + recalled 37 |
| 12-14 | forget approve / deny / timeout | PASS | deleted / kept / kept; #14 now says "תגיד כן או לא, או אשר על המסך" (CV-02); 1 of 4 forget runs stalled (CV-15) |
| 15 | long_conversation 26 turns | PASS | 26/26 answered, 9/10 correct, 0 disconnects, context name/city/plan, voice 319 s vs wall 317 s |
| 16 | rapid_fire | PASS | 6/6 correct (regression run 5/6 "answered" = harness window attribution, 6/6 correct in order) |
| 17 | long_utterance 40 s | PASS | Eilat, dead air 0.17-0.32 s |
| 18 | english_switch | **FAIL** | English heard correctly, answered "קנברה." in Hebrew, 3/3 runs (CV-05) |
| 19 | noise_robustness | PASS | 0.0 s spoken over 40 s noise (x2) |
| 20 | idle_disconnect | PASS | ended 14.7 s after the last sound |
| 21 | wake_word_x3 | PASS | 3/3, scores 0.47-0.48, connect 2.5-3.5 s |
| 22 | wake_false_positives | **FAIL** | 4/20 at 0.15 (CV-04, = 19 Oct baseline) |
| 23 | wake_thresholds | **FAIL** | detect 10/10 at 0.15 and 0.35; false 4/20 and 3/20 (CV-04) |
| 24 | software_mute / unmute | PASS | live ended, mics closed, muted wake lost, unmute wake + answer |
| 25 | budget_voice | PASS | VOICE_BUDGET, 60 s, no reservations |
| 26 | budget_requests | PASS | AI_BUDGET, 2 requests |
| 27 | face_protocol | PASS | after harness fix (valid kind `reading` was missing from its list) |
| 28 | start_stop_x5 | PASS | 5/5 clean |
| 29 | log_privacy | PASS | after fix CV-01 (curator facts leaked); key never in any log |
| 30 | idle_soak_6min | PASS | 406 s, 26 WAKE_HEALTH, 0 false wakes, then wake + answer |
| 31 | false_wake_closes | PASS | closed 14.8 s after a false wake |
| 32 | privacy_strip_live | PASS | muted (no mic audio sent) 0.18 s, cloud off 2.94 s |
| 33 | camera_off_honest | PASS | "היא כבויה כרגע, אז אני לא רואה" (x2) |
| 34 | calendar_not_connected | PASS | no claim, Google connect help (x2) |
| 35 | barge_in (half_duplex off) | PASS | old speech stopped **0.7 s** after the interruption, answered 4 (CV-09 noted) |
| 36 | timer, face closed, live open | PASS | timer_start; face gone; TIMER_RANG 2.2 s after due; spoken "הטיימר נגמר." |
| 37 | alarm, no session, face closed | PASS | alarm_set; session idle-closed + face gone; Core's own speech "שעון מעורר: תזכורת" 1.2 s after due |
| 38 | lists + clear approval by voice | PASS | add/show; "לא" -> denied via voice 3.4 s, list kept; "כן" -> approved via voice 3.6 s, cleared; shown popup+face+voice; face dialog closed (confirm.cancel), popup closed (x2 after fix CV-10) |
| 39 | helper command proposal | PASS | shell_propose `whoami`, kind shell, approved by spoken "כן", ran (audit exit 0), user name spoken |
| 40 | browser on a local test page | PASS | localhost allow-listed: browser_open + browser_read ok, code word "אבטיח" spoken (first run: "localhost" heard as "oraclehost", failed honestly) |
| 41 | briefing without Google | PASS | after fix CV-11: weather + "היומן והמייל לא זמינים כי גוגל..." |
| 42 | WhatsApp / Telegram handoff | PASS | draft read back, "כן", approval "כן" via voice, exactly 1 `wa.me/972541234567?text=` link (recorded), "תלחץ על שלח"; Telegram: "זה עוד לא מוגדר ... Jarvis Telegram Start" |
| 43 | profile switch needs approval | PASS | after fixes CV-12/13: "לא" -> denied via voice; spoken "כן" refused (screen only); face Approve -> spawn `dana run --background --show` (recorded) |
| 44 | self-check by voice | PASS | "תבדוק את עצמך" -> self_check_run ok, 19.7 s spoken summary, dead air 0.87 s |
| 45 | wake variants (no live cost) | PASS | see table below |
| 46 | Hebrew name pronunciation | PASS* | output-audio transcript (gpt-4o-transcribe): "אני ג'ארוויס ... שלום לנועה, לביטל וליצי" — own name and נועה right; ל+אביטל / ל+צחי written by the model as "לביטל"/"ליצחי" (CV-16). A second, prefix-free probe was inconclusive (transcript came back in Latin letters) |
| 47 | greeting delay 0.5 s | PASS | first sound 0.53 / 0.51 / 0.52 s after the face connects (3 boots) |

### Measured latency
- **Dead air** (question end -> first sound): typical 0.1-0.9 s; avg 0.14-0.30 s for small talk; web lookups add the
  spoken acknowledgement at ~0.5 s and the answer after backend 2.2-7.8 s.
- **First audio**: greeting 0.51-0.53 s after the face connects (= `greeting_delay_s` 0.5); after a wake word the
  session connects in 2.5-3.5 s.
- **Barge-in stop**: 0.7 s.
- **Spoken approval decision**: 3.4-3.6 s after the word (GPT-Live input-transcript latency).
- **After an approval**: the WhatsApp "opened the chat" sentence came 9 s after the spoken yes (CV-14).

### Wake variants (scenario 45, threshold 0.15, normal / quiet / very quiet, clean + room noise)
| Variant | Detected |
|---|---|
| Hey Jarvis (David, Zira) | 12/12 |
| Hi Jarvis (Mark, Zira) | 12/12 |
| ok jarvis / Okay Jarvis | 12/12 |
| "hey jarvis" by the Hebrew voice | 6/6 |
| "היי ג'רוויס" (Hebrew voice) | 0/6 |
| "הי ג'ארביס" (Hebrew voice) | 2/6 |

## Log review (every WARN/ERROR, all groups)
- `LINK_FACE_DISCONNECTED` (1 per home): the fake face closes before `core.stop()` — expected (harness).
- `LIVE_GREETING_NUDGE` (1): server sent no audio 6 s after `session.started`; the P0.4 Q05 nudge fixed it — expected (server stall, known).
- `LIVE_FALSE_ACTION_CLAIM` (1, #15): real, CV-06.
- `APPROVAL_VOICE_REFUSED` (#43): correct behaviour; its text wrongly said "typed code" — fixed.
- Tool/approval timeouts (`TOOL_TIMEOUT`, `APPROVAL_DECIDED timeout/cancelled`): only in harness-timing runs that were then fixed and retested.
- No ERROR lines in any campaign home.

## Fixes on `v3-campaign-voice`
CV-01 curator privacy, CV-02 voice-answer hint, CV-03 invisible popup in silent suites, CV-10 always delegate
approval actions, CV-11 honest briefing, CV-12 Hebrew-spoken profile names, CV-13 screen-only profile switch, plus
test/harness fixes (test_profiles vs the approvals cooldown, suite_voice spelling/kind lists). Focused tests, one file
at a time: test_campaign_voice_fixes 4, test_profiles 33, test_helper_approvals 42, test_conversation_quality 6,
test_suite_voice_fixes 15, test_channels 27 — all green.

## Not verified
Real microphone/speaker/room echo, the visible popup on the desktop, the owner's real voice for the wake word, the
real WhatsApp app opening, a real Telegram bot, Google-connected briefing (Pass E).
