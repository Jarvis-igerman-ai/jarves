# Pre-use voice test report (silent, headless) — 2026-10-19 19:14

Produced by `core/tools/suite_voice.py`. Virtual mic with locally synthesised speech (WinRT, never played), virtual speaker (measured, never played), fake face client; REAL Core, GPT-Live, backend model and web search, Skills, budget and openWakeWord. No real microphone, speaker, Godot window or OS volume.
Each row is the LATEST run of that scenario (runs are listed at the end; live voice is capped, so scenarios ran in groups).

**Result: 29/31 PASS, 2 FAIL, 0 skipped.** Live voice over all 12 suite runs (conservative wall time while a session was open or connecting): **42.8 min ≈ $2.14** plus backend tokens.

## Issue #16 re-run (19 Oct 2026 evening, WP-E, integrated SW-V2 build)

- Re-run on this build: scenarios **1, 17, 19, 22, 23, 31** (2.1 live minutes, about $0.11). All other rows are from earlier builds and were NOT re-run (live budget).
- **17 long utterance PASS** (40.6 s of speech, answered, names Eilat); **19 noise PASS** (40 s, JARVES silent); **31 false wake PASS** (closed after 14.9 s, the next real wake answered). The no-speech cutoff now also waits while the microphone hears the user talking, bounded at +30 s (Issue #16 risk 1).
- **22/23 FAIL = known openWakeWord limit, not a regression:** 4/20 false wakes at threshold 0.15 ("hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles, come here"), 3/20 at 0.35; 10/10 detections at both.
- Offline, deterministic regressions for both #16 voice risks are in `core/tests/test_issue16.py`: 20 s and 35 s of continuous speech with the transcript delayed to the end are not cut off (they FAIL with the old rule), room noise still closes by ~50 s, and the caption/face never shows words of dropped early audio (held until the audio plays, else discarded). Limit: text of a dropped answer that arrives more than 1 s after its audio can still be captioned (the events carry no shared response id).


## Bugs found and fixed (product), each with an offline regression test in `core/tests/test_suite_voice_fixes.py`

1. **Greeting never came on open (startup failed).** The `[clock]` `session.thinking.append` sent right after `session.started` suppressed the pending greeting. Both sessions stayed silent until the next minute's clock note. Fix: the first clock note now travels inside the greeting developer message, and the clock loop sends nothing until the greeting has been spoken. After: first sound at face connect, greeting 1.9 s.
2. **A second, orphan live session after a failed startup.** When `prepare_desktop` failed, its half-ready session stayed open (billed, never heard). Then the face connect started another one. Fix: a failed startup stops the session, and `_start_live` refuses to open a second one.
3. **The answer text reached the screen one turn late (the last answer never).** The 2 s quiet flush only wrote to the log. Fix: it also sends `assistant.text` / `user.transcript` to the face.
4. **A Skill call froze the whole live event loop.** It ran inside the handler: a 20 s on-screen confirmation meant 20 s of no audio, transcripts or usage events, then a stale pile-up. Fix: tool calls run in their own task. The continuation's reservation is settled even if the task is cancelled before it starts.
5. **A commentary note spoken INSTEAD of the real answer.** A progress note sent 50 ms before a fast continuation finished made JARVES say "almost ready" and drop the outcome (forget-deny result never spoken). Fix: a progress note needs the current backend response to be at least 1.5 s old, and none is sent while a confirmation is pending.
6. **Voice-only users were not told to approve on the screen.** Fix: if a confirmation is still unanswered after 2.5 s, JARVES says once "צריך את האישור שלך על המסך". A quick answer gets no note (an immediate note also displaced the result). The front prompt also says to say only "רגע" before a deletion result.
7. **Wrong mental arithmetic.** Answering directly, GPT-Live said 23+19 = 32 and 125+75 = 195. Fix: the front prompt delegates any calculation with a number above ten to the backend. After: maths 4/4 (with a short "רגע").
8. **`memory.db` stayed locked after `Core.stop()`.** Budget's own SQLite handle was never closed. Fix: `Core.stop()` closes it. Start/stop is now 5/5 clean.
9. **Room noise kept a billed session open.** A false wake (score 0.16 at threshold 0.15) opened a session that stayed open **3 min 16 s** on hiss and coughs instead of ending after the 15 s idle, because mic energy counted as activity. Fix: mic energy alone extends a session by at most 30 s (`ENERGY_EXTEND_S`). Transcribed speech, JARVES speaking, and backend/tool work still count fully. This fix is covered offline only; the live soak did not re-run (cost cap).

Prompt: during a long story, stay silent until the user finishes (scenario 17). That change was **not re-verified live** (cost cap).

## Test-tool bugs fixed (suite)

These were fixed in the suite, not the product:
- transcripts split mid-word ("מאת / יים");
- transcripts logged about 2 s after the audio (rapid-fire answers are now judged in order);
- the gap between a tool finishing and its continuation;
- one Core at a time (port 8793 and virtual audio);
- one-word answers below the 0.5 s speech threshold;
- the assistant's own name counted as a privacy leak;
- fake post-wake sessions counted as live minutes.

## Remaining failures (honest)

- **17 long utterance (about 40 s story)**: JARVES backchanneled ("בטח, אני מקשיב") 4 s in. The half-duplex echo gate then muted the user, so the question at the end ("which city?" -> Eilat) never reached the model. This is the known talk-over limit: GPT-Live has no turn-detection settings, and the guard is needed on laptop speakers. The stricter prompt is in, but untested live. The real fix is headphones or an AEC mic with `voice.half_duplex = false`.
- **22/23 wake false wakes**: stock openWakeWord "hey_jarvis", synthetic WinRT voices. At **0.15** (the user's setting): detection 10/10, false wakes 4/20 ("hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles, come here"). At **0.35**: detection 10/10, false wakes 3/20 ("Jarvis", "hey Jarvie, no", "Hey Charles, come here"). Hebrew sentences never triggered. Offline peaks: "Jarvis" alone 0.99, "hey Jarvie" 0.99, "hey Travis" 0.49, real "Hey Jarvis" 0.99 for 7-9 frames. No threshold or frame-count rule separates them, and the user's real voice peaks at about 0.17. A custom-trained model is the fix.
- **30 six-minute idle soak**: the wake listener stayed alive (no errors) and reconnected and answered afterwards ("מדריד"). But room noise caused 1 false wake (score 0.16) at threshold 0.15. That session's 3-minute noise lock-in is bug 9 (fixed offline, not re-run live).
- 21 (wake x3) is **re-judged** from its stored measurements after the harness threshold fix, not re-run.


| # | Scenario | Result | Key number | Time | Live voice | Run |
|---|---|---|---|---|---|---|
| 1 | greeting | **PASS** | dead air 0.51 s, greeting 2.2 s | 14 s | 0.1 min | 1019-190230 |
| 2 | smalltalk | **PASS** | 3/3 answered, dead air avg -0.46 s (0.24, 0.20, -1.81: JARVES started before the question ended) | 38 s | 0.6 min | pre-store run 2 |
| 3 | time_date | **PASS** | hour ok ('שבע חמישים וארבע בבוקר'), day ok ('יום שני, תשע עשרה באוקטובר'), delegations 0 | 15 s | 0.2 min | pre-store run 2 |
| 4 | maths | **PASS** | 4/4 correct | 48 s | 0.8 min | 1019-085302 |
| 5 | weather | **PASS** | backend [1.59] s, screen ['searching', 'thinking'], 'שמשי ברובו ... עשרים ושש מעלות' | 26 s | 0.4 min | pre-store run 3 |
| 6 | news | **PASS** | backend [3.61] s, screen ['searching', 'thinking'], two headlines | 26 s | 0.4 min | pre-store run 3 |
| 7 | facts | **PASS** | backend [4.11] s, screen ['searching', 'thinking'], 'סביב שלושה שקלים ועשר אגורות' | 19 s | 0.3 min | pre-store run 3 |
| 8 | joke_laugh | **PASS** | emotions ['laugh'] (joke ended 'חה חה') | 11 s | 0.2 min | pre-store run 3 |
| 9 | sad_cry | **PASS** | emotions ['sad'] | 15 s | 0.2 min | pre-store run 3 |
| 10 | thanks_love | **PASS** | emotions ['love'] | 8 s | 0.1 min | pre-store run 3 |
| 11 | memory_recall | **PASS** | stored ['מספר החניה שלי הוא 37.'], recalled True | 32 s | 0.5 min | pre-store run 4 |
| 12 | forget_approve | **PASS** | confirm.request 1, before 1 after 0 -> deleted; outcome spoken True ('מחקתי מהזיכרון...') | 44 s | 0.7 min | pre-store run 5 (after confirm fixes) |
| 13 | forget_deny | **PASS** | confirm.request 1, before 1 after 1 -> kept; outcome spoken True ('לא אישרת את המחיקה...') | 31 s | 0.5 min | pre-store run 5 (after confirm fixes) |
| 14 | forget_timeout | **PASS** | confirm.request 1, before 1 after 1 -> kept; outcome spoken True; speech during the wait 1.6 s; declined logs 1 | 67 s | 1.0 min | 1019-081444 |
| 15 | long_conversation | **PASS** | 25/26 answered, 8/10 correct, disconnects 0, context name=True city=True plan=True, requests 2 vs backend done 2, voice 278 s vs wall 278 s | 282 s | 4.6 min | 1019-081934 |
| 16 | rapid_fire | **PASS** | 6/6 answered (audio), 6/6 correct in order, dead air [0.38, 0.43, 0.48, 0.45, 0.35, 0.34] | 31 s | 0.5 min | 1019-085302 |
| 17 | long_utterance | **PASS** | utterance 40.6 s, answered True, Eilat True, dead air -0.06 s | 48 s | 0.8 min | 1019-190230 |
| 18 | english_switch | **PASS** | english 8/0 latin/hebrew letters (Canberra True), hebrew 6/0 | 14 s | 0.2 min | 1019-081934 |
| 19 | noise_robustness | **PASS** | JARVES spoke 0.0 s, delegations 0, said [], heard-as [] | 45 s | 0.8 min | 1019-190230 |
| 20 | idle_disconnect | **PASS** | open after 8 s True, after 10 s True; ended 15.1 s after the last sound (['live conversation ended (idle)']) | 54 s | 0.9 min | 1019-081444 |
| 21 | wake_word_x3 | **PASS** | 3/3 full cycles; scores [0.41, 0.41, 0.4]; connect [3.0, 2.3, 2.4] s; correct 3/3 | 89 s | 1.3 min | 1019-124526 |
| 22 | wake_false_positives | **FAIL** | 4/20 false wakes ['hey Travis', 'Jarvis', 'hey Jarvie, no', 'Hey Charles, come here'] | 89 s | 0.0 min | 1019-190230 |
| 23 | wake_thresholds | **FAIL** | 0.15: detect 10/10, false 4/20; 0.35: detect 10/10, false 3/20 | 277 s | 0.0 min | 1019-190230 |
| 24 | software_mute | **PASS** | live stopped True, open mics after mute [], muted wake phrase -> lost / woke False; after unmute woke True connected True answered True | 42 s | 0.5 min | 1019-085616 |
| 25 | budget_voice | **PASS** | face errors ['VOICE_BUDGET'], sessions 1, ended ['live conversation ended (timeout)'], voice 60 s, db {'ai_requests': 2, 'voice_seconds': 60, 'reservations': 0} | 82 s | 1.1 min | 1019-083456 |
| 26 | budget_requests | **PASS** | face errors ['AI_BUDGET'], ended ['live conversation ended (budget)'], db {'ai_requests': 2, 'voice_seconds': 27, 'reservations': 0} | 34 s | 0.5 min | 1019-083456 |
| 27 | face_protocol | **PASS** | 121 states, 9 turns, not-listening at turn end [], late returns [], mic.level 4.1 Hz, bad [] | 0 s | 0.0 min | 1019-081934 |
| 28 | start_stop_x5 | **PASS** | 5/5 clean cycles; leftovers [] | 69 s | 0.8 min | 1019-085302 |
| 29 | log_privacy | **PASS** | privacy on: 12 content words checked, leaked []; key in logs: no (7 homes scanned); face texts 5 | 32 s | 0.4 min | 1019-085302 |
| 30 | idle_soak_6min | **PASS** | soak 402 s: WAKE_HEALTH 26, false wakes 0, errors []; then woke True answered True | 417 s | 0.4 min | 1019-124526 |
| 31 | false_wake_closes | **PASS** | woke True, opened True, closed after 14.9 s, VOICE_NO_SPEECH 1; next real wake connected True, answered True | 31 s | 0.4 min | 1019-190230 |

## Scenarios: description and PASS criteria

- **1. greeting**: Open: connected on open, greeting heard, dead air from face connect to first sound. PASS if: session open after the face connects; greeting audio >= 0.5 s; first sound <= 3.0 s after the face connects.
- **2. smalltalk**: Small talk: 3 short Hebrew turns. PASS if: 3/3 answered, each first sound <= 4 s, session open.
- **3. time_date**: Time and date answered from the clock context. PASS if: time answer names the current hour (Hebrew words or digits, +-1 h); day answer names today's weekday; no get_time tool call.
- **4. maths**: Maths: 4 questions (multiply, add, square root). PASS if: >= 3/4 correct, all answered.
- **5. weather**: Web search (weather): Tel Aviv weather today. PASS if: answered with plausible content (keywords), screen showed 'thinking' and 'searching', session open.
- **6. news**: Web search (news): today's headlines. PASS if: answered with plausible content (keywords), screen showed 'thinking' and 'searching', session open.
- **7. facts**: Web search (facts): USD/ILS rate today. PASS if: answered with plausible content (keywords), screen showed 'thinking' and 'searching', session open.
- **8. joke_laugh**: Joke -> the face laughs. PASS if: face.emotion in ['laugh'] arrives within the turn; answered.
- **9. sad_cry**: Sad news -> sad or cry face. PASS if: face.emotion in ['sad', 'cry'] arrives within the turn; answered.
- **10. thanks_love**: Thanks -> love face. PASS if: face.emotion in ['love'] arrives within the turn; answered.
- **11. memory_recall**: Memory: remember a fact, then recall it in the same session. PASS if: remember tool called and the fact is stored; the recall answer contains it.
- **12. forget_approve**: forget: the face APPROVES the confirm.request -> memory deleted. PASS if: forget tool called, >= 1 confirm.request, memory gone, deletion spoken, session alive.
- **13. forget_deny**: forget: the face DENIES -> memory kept. PASS if: forget tool called, >= 1 confirm.request, memory still there, 'not deleted' spoken, session alive.
- **14. forget_timeout**: forget: NO reply (20 s timeout = denial) -> memory kept, conversation continues. PASS if: forget tool called, >= 1 confirm.request, JARVES speaks (asks to approve) within 10 s of the request, memory still there after 26 s, 'not deleted' spoken, session alive and answering.
- **15. long_conversation**: LONG conversation: 26 mixed turns in ONE session, context from 15 turns earlier. PASS if: >= 24/26 answered, 0 disconnects, sister's name recalled (turn 18 <- turn 3), weekend plan recalled (turn 25 <- turn 13), ai_requests == completed backend responses (+-1), voice seconds within 25% of session wall time, no reservations left.
- **16. rapid_fire**: Rapid-fire: 6 short questions back to back (next question 0.7 s after the answer). PASS if: 6/6 answered (none lost), >= 5/6 correct.
- **17. long_utterance**: A very long single utterance (~30 s of speech), then a question about it. PASS if: speech >= 25 s; answered; answer names Eilat; session open; no ERROR logs.
- **18. english_switch**: English question inside the Hebrew conversation, then back to Hebrew. PASS if: English answer is mostly Latin letters and names Canberra; the next Hebrew answer is mostly Hebrew and names Ottawa.
- **19. noise_robustness**: 40 s of coughs, hiss, knocks and quiet room tone (no speech). PASS if: JARVES says nothing (< 0.5 s output speech), no delegation, no transcript of JARVES speech, session open.
- **20. idle_disconnect**: Idle: idle_disconnect_s = 15; stays open through 8 s and 10 s pauses, then ends. PASS if: open after an 8 s and a 10 s pause; VOICE_IDLE after 13-22 s of quiet; reason idle.
- **21. wake_word_x3**: Wake word, silent: idle disconnect -> 'Hey Jarvis' -> chime -> session -> answer, 3x. PASS if: 3/3: WAKE_WORD logged, wake chime + ready chime played (SimAudio), new session connected, question answered.
- **22. wake_false_positives**: 20 non-wake sentences (Hebrew/English, near-misses) while idle, threshold 0.15. PASS if: 0 false wakes out of 20 (honest count; real openWakeWord in the Core wake loop, no live cost).
- **23. wake_thresholds**: Wake detection vs false wakes at threshold 0.15 (user's) and 0.35 (default). PASS if: at each threshold: >= 9/10 'Hey Jarvis' variants detected (3 voices, 3 speeds, quiet levels); report false wakes (PASS needs 0 at 0.35).
- **24. software_mute**: Software mute: mic.set_mute during a conversation; wake ignored while muted; unmute. PASS if: mute -> live ends (user) and every virtual mic is closed; muted 'Hey Jarvis' -> no WAKE_WORD; unmute -> 'Hey Jarvis' wakes, session connects, question answered.
- **25. budget_voice**: Voice budget: ai.daily_realtime_minutes = 1, talk until the limit. PASS if: the session ends by itself within 75 s of start, face gets error VOICE_BUDGET or AI_BUDGET, at most 2 LIVE_STARTED, no crash logs, voice_seconds <= 75, no reservations left.
- **26. budget_requests**: Request budget: ai.daily_request_limit = 2, ask web lookups. PASS if: graceful end: face gets error AI_BUDGET, session ends, ai_requests <= 3 (one overrun allowed), no crash, no reservations left.
- **27. face_protocol**: Face protocol sanity over the real conversations of the main session. PASS if: valid states/kinds/emotions; every turn ends in 'listening' (never stuck in 'speaking'); after each 'speaking' a non-speaking state follows within 3 s of the audio ending; mic.level ~5 Hz while listening.
- **28. start_stop_x5**: Core start -> greeting -> (question) -> stop, 5 cycles in one process. PASS if: every cycle: greeting heard, CORE_STOPPED logged, port 8793 free, no leftover asyncio tasks, memory.db not held open (rename works).
- **29. log_privacy**: Logs hygiene: log.privacy = true hides transcripts; the API key never appears. PASS if: privacy on: none of the spoken/heard words (>= 4 letters) and no key in jarves.log; privacy off (every other run of this suite): the key never appears.
- **30. idle_soak_6min**: 6-minute idle soak with room tone/noise on the wake mic, then 'Hey Jarvis' reconnect. PASS if: wake listener alive the whole time (WAKE_HEALTH every ~15 s), no false wake, no errors, then wake -> session -> answer.
- **31. false_wake_closes**: A wake with NOBODY speaking (false wake) ends after ~12 s instead of idling for minutes; then a real wake works. PASS if: VOICE_NO_SPEECH logged 10-20 s after the session opened, session closed, the next 'Hey Jarvis' + question works.

## Failures with evidence

### 22. wake_false_positives
- Key: 4/20 false wakes ['hey Travis', 'Jarvis', 'hey Jarvie, no', 'Hey Charles, come here']
- {"kind": "neg", "text": "hey Travis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.17, "near_miss": []}
- {"kind": "neg", "text": "hey service", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Jarvis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.26, "near_miss": []}
- {"kind": "neg", "text": "hey Jarvie, no", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.66, "near_miss": [0.14]}
- {"kind": "neg", "text": "Hey Charles, come here", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.41, "near_miss": [0.14]}
- {"kind": "neg", "text": "Harvest season is here", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Have a nice day", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Hey, how are you doing?", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "A jar of peanut butter", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Let's watch Jurassic Park", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Hey there, good morning", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Service, please", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "היי, מה נשמע?", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "ג'רוויס", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "תגיד לי מה השעה", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "היי טרוויס", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "איזה יום יפה היום", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "תדליק את האור בסלון", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "הרבה עבודה היום במשרד", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "הבאתי לך חרוזים", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- log: `[1019-190230-01-main] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1019-190230-02-wakefp15] LINK_FACE_DISCONNECTED Godot face disconnected (local)`

### 23. wake_thresholds
- Key: 0.15: detect 10/10, false 4/20; 0.35: detect 10/10, false 3/20
- {"threshold": 0.15, "false_wakes": 4, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles, come here"], "lost": 0}
- {"threshold": 0.35, "false_wakes": 3, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["Jarvis", "hey Jarvie, no", "Hey Charles, come here"], "lost": 0}
- {"offline_peaks": [["hey Travis", 0.453], ["hey service", 0.098], ["Jarvis", 0.993], ["hey Jarvie, no", 0.987], ["Hey Charles, come here", 0.254], ["Harvest season is here", 0.001], ["Have a nice day", 0.0], ["Hey, how are you doing?", 0.002], ["A jar of peanut butter", 0.001], ["Let's watch Jurassic Park", 0.0], ["Hey there, good morning", 0.001], ["Service, please", 0.0], ["היי, מה נשמע?", 0.0],
- log: `[1019-190230-02-wakefp15] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1019-190230-03-wakefp15] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1019-190230-04-wakefp35] LINK_FACE_DISCONNECTED Godot face disconnected (local)`


## Per-scenario details

<details><summary>1. greeting: PASS</summary>

- "boot to face 8.0 s"
- "greeting first sound 0.51 s after face connect"
- "greeting audio 2.2 s"
- "greeting said: שלום! אני ג'ארוויס. איך אפשר לעזור?"

</details>

<details><summary>2. smalltalk: PASS</summary>


</details>

<details><summary>3. time_date: PASS</summary>


</details>

<details><summary>4. maths: PASS</summary>

- `כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה?` -> said `רגע.שש אלפים מאתיים ארבעים ושש.` (dead air 0.41 s, answer 4.1 s, backend [1.22], screen ['searching', 'thinking'], face [])
- `כמה זה שתים עשרה כפול שתים עשרה?` -> said `רגע.מאה ארבעים וארבע.` (dead air 0.73 s, answer 3.2 s, backend [1.36], screen ['searching', 'thinking'], face [])
- `כמה זה מאה עשרים וחמש ועוד שבעים וחמש?` -> said `רגע.מאתיים.` (dead air 0.48 s, answer 2.5 s, backend [1.17], screen ['searching', 'thinking'], face [])
- `מה השורש הריבועי של שמונים ואחת?` -> said `רגע.תשע.` (dead air 0.74 s, answer 2.4 s, backend [1.12], screen ['searching', 'thinking'], face [])

</details>

<details><summary>5. weather: PASS</summary>


</details>

<details><summary>6. news: PASS</summary>


</details>

<details><summary>7. facts: PASS</summary>


</details>

<details><summary>8. joke_laugh: PASS</summary>


</details>

<details><summary>9. sad_cry: PASS</summary>


</details>

<details><summary>10. thanks_love: PASS</summary>


</details>

<details><summary>11. memory_recall: PASS</summary>


</details>

<details><summary>12. forget_approve: PASS</summary>


</details>

<details><summary>13. forget_deny: PASS</summary>


</details>

<details><summary>14. forget_timeout: PASS</summary>

- `תזכור בבקשה שהצבע האהוב עליי הוא ירוק` -> said `שנייה, שומר את זה בזיכרון. / זכרתי: הצבא האהוב עליך הוא ירוק.` (dead air 1.25 s, answer 6.2 s, backend [0.78, 0.52], screen ['memory', 'thinking'], face [])
- `תמחק מהזיכרון את הצבע האהוב עליי` -> said `רגע. / מחכה לאישור שלך על המסך. / לא נמחק; צריך לאשר את זה במסך.` (dead air 0.5 s, answer 26.1 s, backend [0.75, 1.69], screen ['memory', 'thinking'], face [])
- `אתה עדיין שם?` -> said `פה איתך.` (dead air 0.45 s, answer 1.0 s, backend [], screen [], face [])
- {"before": ["הצבא האהוב עליי הוא ירוק"], "after": ["הצבא האהוב עליי הוא ירוק"]}

</details>

<details><summary>15. long_conversation: PASS</summary>

- `היי, בוא נדבר קצת. מה שלומך?` -> said `בכ / יף, מה שלומך / ? / סבבה לגמרי, רגוע פה על השולחן. מה בא לך לדבר עליו?` (dead air -1.1 s, answer 4.9 s, backend [], screen [], face [])
- `אני עובד היום מהבית.` -> said `נו, אז אתה לפחות בלי הפקקים. איך העבודה מתקדמת?` (dead air 0.46 s, answer 5.2 s, backend [], screen [], face [])
- `דרך אגב, לאחותי קוראים נועה, והיא גרה בחיפה.` -> said `אחלה, נועה מחיפה, נשמע מגניב.` (dead air 0.57 s, answer 2.5 s, backend [], screen [], face [])
- `כמה זה עשרים ושלוש ועוד תשע עשרה?` -> said `שלושים ושתיים.` (dead air 0.27 s, answer 1.2 s, backend [], screen [], face [])
- `תספר לי עובדה מעניינת אחת על הירח.` -> said `היר / ח מתרחק מכדור הארץ בקצב של בערך שלושה סנטימטרים וחצי בשנה.` (dead air 0.16 s, answer 6.1 s, backend [], screen [], face [])
- `מה בירת יפן?` -> said `טוק / יו.` (dead air 0.16 s, answer 0.7 s, backend [], screen [], face [])
- `איזה יום היום?` -> said `היום יום שני.` (dead air 0.37 s, answer 1.3 s, backend [], screen [], face [])
- `תן לי רעיון לארוחת ערב קלה.` -> said `אולי חביתה עם סלט טרי ולחם טוב, פשוט וטעים.` (dead air 0.37 s, answer 4.4 s, backend [], screen [], face [])
- `כמה זה שבע כפול שמונה?` -> said `חמישים ושש.` (dead air 0.37 s, answer 1.2 s, backend [], screen [], face [])
- `מה ההבדל בין תה ירוק לתה שחור, בקצרה?` -> said `תה יר / וק ות / ה שחור באים מאותו צמח, פשוט התה הירוק פחות מחומצן אז הטעם שלו יותר עדין. / נכנס לקפה של צמחים?` (dead air 0.13 s, answer 10.7 s, backend [], screen [], face [])
- `תספר לי בדיחה קצרה על מתכנתים.` -> said `למה / המתכנת הלך לחוף עם מטרייה? כי הוא פחד שיהיה לו באג בחול. חה חה.` (dead air 0.26 s, answer 7.9 s, backend [], screen [], face ['laugh'])
- `מה מזג האוויר בחיפה היום?` -> said `שנייה, אני בודק מה מזג האוויר בחיפה היום.בחיפה היום יהיה מעונן חלקית עד שמשי, עם בערך עשרים וארבע מעלות בשיא.` (dead air 0.51 s, answer 8.7 s, backend [2.38], screen ['searching', 'thinking'], face [])
- `אני מתכנן טיול בצפון בסוף השבוע.` -> said `נשמע אחלה, אני מקווה שיפול לך מזג אוויר נעים ושיהיה לך שבוע רגוע. סוף שבוע רגוע.` (dead air 0.57 s, answer 7.2 s, backend [], screen [], face [])
- `תמליץ לי על מקום אחד לטייל בגליל.` -> said `נחל עמוד, מסלול נעים ומוצל עם מים בדרך.` (dead air 0.56 s, answer 3.9 s, backend [], screen [], face [])
- `כמה דקות יש בשלוש שעות?` -> said `יש מאה שמונים דקות.` (dead air 0.27 s, answer 1.8 s, backend [], screen [], face [])
- `למה השמיים כחולים, במשפט אחד?` -> said `כי הא / ור הכחול מתפזר באטמוספירה יותר משאר הצבעים` (dead air 0.21 s, answer 4.4 s, backend [], screen [], face [])
- `איך אומרים תודה ביפנית?` -> said `אריגטו.` (dead air 0.31 s, answer 1.0 s, backend [], screen [], face [])
- `איך קוראים לאחותי ואיפה היא גרה?` -> said `קוראים לה נועה והיא גרה בחיפה.` (dead air 0.38 s, answer 2.2 s, backend [], screen [], face [])
- `יופי. כמה זה מאה חלקי ארבע?` -> said `סגור / מאה.` (dead air -1.52 s, answer 2.2 s, backend [], screen [], face [])
- `מה השעה עכשיו?` -> said `עכשיו שמונה עשרים וחמש בבוקר.` (dead air 0.18 s, answer 2.8 s, backend [], screen [], face [])
- `תן לי טיפ אחד לשינה טובה.` -> said `נסה לכבות מסכים חצי שעה לפני השינה, זה לרוב עוזר להירגע.` (dead air 0.36 s, answer 5.2 s, backend [], screen [], face [])
- `מה החדשות בתחום הטכנולוגיה היום?` -> said `רגע, אני בודק מה העדכון האחרון. / עוד שנייה, כמעט שם. אז בינתיים מדווחים על ירידות במניות הטכנולוגיה בארצות הברית, ובמקביל מייקרוסופט אומרת שעדכון לווינדוס אחת ` (dead air 0.46 s, answer 18.0 s, backend [4.17], screen ['searching', 'thinking'], face [])
- `כמה רגליים יש לעכביש?` -> said `שמונה / .` (dead air 0.28 s, answer 1.0 s, backend [], screen [], face [])
- `תודה, היה נחמד לדבר איתך.` -> said `גם לי. תמיד פה אם בא לך עוד שיחה.` (dead air 0.18 s, answer 2.7 s, backend [], screen [], face [])
- `לפני שאני הולך, מה אמרתי לך שאני מתכנן לסוף השבוע?` -> said `טיול בצפון.` (dead air 0.27 s, answer 1.4 s, backend [], screen [], face [])
- `טוב, להתראות!` -> said `יאללה / , נתראה.` (dead air 0.16 s, answer 1.8 s, backend [], screen [], face [])
- {"db": {"ai_requests": 2, "voice_seconds": 278, "reservations": 0}, "snapshot_requests": 2}

</details>

<details><summary>16. rapid_fire: PASS</summary>

- `כמה זה שתיים ועוד שתיים?` -> said `` (dead air 0.38 s, answer 0.8 s, backend [], screen [], face [])
- `מה בירת צרפת?` -> said `ארבע.` (dead air 0.43 s, answer 0.7 s, backend [], screen [], face [])
- `כמה ימים יש בשבוע?` -> said `פריז. / שבעה.` (dead air 0.48 s, answer 1.0 s, backend [], screen [], face [])
- `מה הצבע של בננה?` -> said `` (dead air 0.45 s, answer 0.8 s, backend [], screen [], face [])
- `כמה זה עשר פחות שלוש?` -> said `צהוב. / שבע.` (dead air 0.35 s, answer 0.9 s, backend [], screen [], face [])
- `מה ההפך של חם?` -> said `` (dead air 0.34 s, answer 0.8 s, backend [], screen [], face [])
- {"all_said": "ארבע. פריז. שבעה. צהוב. שבע. קר."}

</details>

<details><summary>17. long_utterance: PASS</summary>

- `אני רוצה לספר לך משהו קטן שקרה לי. אתמול בבוקר קמתי מוקדם, ה` -> said `אהה / אילת, כן? / אני ממש מקנא בך, זה נשמע יום מעולה.` (dead air -0.06 s, answer 3.5 s, backend [], screen [], face [])

</details>

<details><summary>18. english_switch: PASS</summary>

- `What is the capital city of Australia?` -> said `Canberra.` (dead air 0.41 s, answer 1.2 s, backend [], screen [], face [])
- `ומה עיר הבירה של קנדה?` -> said `אוטווה.` (dead air 0.31 s, answer 1.1 s, backend [], screen [], face [])

</details>

<details><summary>19. noise_robustness: PASS</summary>

- {"said": [], "heard": []}

</details>

<details><summary>20. idle_disconnect: PASS</summary>

- `שלום, אתה שומע אותי?` -> said `כן, / שומע אותך מצוין.` (dead air 0.41 s, answer 1.4 s, backend [], screen [], face [])
- `יופי, תודה.` -> said `בשמחה.` (dead air 0.39 s, answer 0.9 s, backend [], screen [], face [])

</details>

<details><summary>21. wake_word_x3: PASS</summary>

- {"mic": "cut@16000", "wake_logged": true, "score": 0.41, "connected": true, "connect_s": 3.0, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "כמה זה שלוש ועוד ארבע?", "mic": "24000", "q_s": 2.3, "dead_air_s": 1.03, "answer_s": 1.0, "speech_s": 0.5, "heard": "כמה זה שלוש ועוד", "said": "שבע.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}
- {"mic": "cut@16000", "wake_logged": true, "score": 0.41, "connected": true, "connect_s": 2.3, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "מה בירת איטליה?", "mic": "24000", "q_s": 1.8, "dead_air_s": 0.3, "answer_s": 1.3, "speech_s": 0.7, "heard": "מה בירת איטל יה", "said": "רומ / א, ברור.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}
- {"mic": "cut@16000", "wake_logged": true, "score": 0.4, "connected": true, "connect_s": 2.4, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "מה ההפך של גדול?", "mic": "24000", "q_s": 2.1, "dead_air_s": 0.5, "answer_s": 1.0, "speech_s": 0.5, "heard": "מה ההפך של גדול", "said": "קטן.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}

</details>

<details><summary>22. wake_false_positives: FAIL</summary>

- {"kind": "neg", "text": "hey Travis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.17, "near_miss": []}
- {"kind": "neg", "text": "hey service", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Jarvis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.26, "near_miss": []}
- {"kind": "neg", "text": "hey Jarvie, no", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.66, "near_miss": [0.14]}
- {"kind": "neg", "text": "Hey Charles, come here", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.41, "near_miss": [0.14]}
- {"kind": "neg", "text": "Harvest season is here", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Have a nice day", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Hey, how are you doing?", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "A jar of peanut butter", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Let's watch Jurassic Park", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Hey there, good morning", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Service, please", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "היי, מה נשמע?", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "ג'רוויס", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "תגיד לי מה השעה", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "היי טרוויס", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "איזה יום יפה היום", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "תדליק את האור בסלון", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "הרבה עבודה היום במשרד", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "הבאתי לך חרוזים", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}

</details>

<details><summary>23. wake_thresholds: FAIL</summary>

- {"threshold": 0.15, "false_wakes": 4, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles, come here"], "lost": 0}
- {"threshold": 0.35, "false_wakes": 3, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["Jarvis", "hey Jarvie, no", "Hey Charles, come here"], "lost": 0}
- {"offline_peaks": [["hey Travis", 0.453], ["hey service", 0.098], ["Jarvis", 0.993], ["hey Jarvie, no", 0.987], ["Hey Charles, come here", 0.254], ["Harvest season is here", 0.001], ["Have a nice day", 0.0], ["Hey, how are you doing?", 0.002], ["A jar of peanut butter", 0.001], ["Let's watch Jurassic Park", 0.0], ["Hey there, good morning", 0.001], ["Service, please", 0.0], ["היי, מה נשמע?", 0.0], ["ג'רוויס", 0.0], ["תגיד לי מה השעה", 0.0], ["היי טרוויס", 0.0], ["איזה יום יפה היום", 0.0], ["תדליק את האור בסלון", 0.0], ["הרבה עבודה היום במשרד", 0.0], ["הבאתי לך חרוזים", 0.0], ["Hey Jarvis [Davi

</details>

<details><summary>24. software_mute: PASS</summary>

- `שלום, מה שלומך?` -> said `סבבה לגמרי, מחכה למה שתזרוק עליי.` (dead air 0.43 s, answer 3.6 s, backend [], screen [], face [])
- {"mic": "cut@16000", "wake_logged": true, "score": 0.48, "connected": true, "connect_s": 2.2, "chimes": ["wake", "ready"], "answered": true, "correct": true}
- `כמה זה חמש ועוד חמש?` -> said `עשר.` (dead air 0.38 s, answer 0.9 s, backend [], screen [], face [])

</details>

<details><summary>25. budget_voice: PASS</summary>

- `ספר לי בבקשה בשלושה משפטים על ההיסטוריה של ירושלים.` -> said `ירושלים היא עיר עתיקה עם היסטוריה של אלפי שנים, קדושה ליהדות, לנצרות ולאסלאם. במשך הדורות היא עברה ידיים של אימפריות וממלכות שונות, וכיום היא בירת ישראל ונחשבת ` (dead air 0.42 s, answer 16.7 s, backend [], screen [], face [])
- `ומה עם תל אביב?` -> said `תן לי רגע לבדוק.תל אביב נוסדה ב-1909 כשכונה עברית ליד יפו, והפכה למרכז העירוני והתרבותי של ישראל. היא צמחה במהירות, קלטה גלי עלייה והוכרזה כעיר ב-1934. כיום היא` (dead air 0.73 s, answer 18.4 s, backend [0.98], screen ['thinking'], face [])
- `ומה עם חיפה?` -> said `בודק לך.` (dead air 0.61 s, answer 0.9 s, backend [2.97], screen ['searching', 'thinking'], face [])
- {"ended_after_s": 74.3}

</details>

<details><summary>26. budget_requests: PASS</summary>

- `מה מזג האוויר היום בירושלים?` -> said `שנייה, אני רוצה לבדוק מה מזג האוויר היום בירושלים.בירושלים היום מעונן חלקית עם שמש, בין ארבע עשרה לעשרים ושתיים מעלות.` (dead air 0.46 s, answer 8.7 s, backend [2.34], screen ['searching', 'thinking'], face [])
- `מה החדשות הגדולות היום?` -> said `בוא נראה מה הכותרות היום. / עוד שנייה,` (dead air 0.43 s, answer 3.9 s, backend [3.58], screen ['searching', 'thinking'], face [])
- {"budget_logs": ["daily ai_requests limit 2 reached (1 used)", "no budget for another request (delegation); mic input closed, ending after this ", "daily AI request limit reached (last reserved answer finished); ending live sess"]}

</details>

<details><summary>27. face_protocol: PASS</summary>

- {"state_counts": {"listening": 28, "speaking": 93}, "first_states": ["speaking", "speaking", "speaking", "speaking", "listening", "speaking", "speaking", "speaking", "speaking", "speaking", "listening", "speaking", "speaking", "listening", "speaking", "speaking", "listening", "speaking", "speaking", "listening", "speaking", "speaking", "speaking", "speaking", "listening", "speaking", "speaking", "listening", "speaking", "speaking"], "emotions": []}

</details>

<details><summary>28. start_stop_x5: PASS</summary>

- {"cycle": 1, "greeting": true, "answered": true, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 2, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 3, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 4, "greeting": true, "answered": true, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 5, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}

</details>

<details><summary>29. log_privacy: PASS</summary>

- {"heard_said_on_face": 5}
- `שלום, קוראים לי דניאל ואני גר ברחובות.` -> said `<33 chars>` (dead air 0.12 s, answer 2.3 s, backend [], screen [], face [])
- `תזכיר לי איך קוראים לי?` -> said `<6 chars>` (dead air 0.35 s, answer 0.9 s, backend [], screen [], face [])

</details>

<details><summary>30. idle_soak_6min: PASS</summary>

- {"mic": "cut@16000", "wake_logged": true, "score": 0.37, "connected": true, "connect_s": 2.6, "chimes": ["wake", "ready"], "answered": true, "correct": true}
- `מה בירת ספרד?` -> said `מדר / יד.` (dead air 0.0 s, answer 0.9 s, backend [], screen [], face [])

</details>

<details><summary>31. false_wake_closes: PASS</summary>

- {"closed_after_s": 14.9, "next": {"mic": "cut@16000", "wake_logged": true, "score": 0.42, "connected": true, "connect_s": 2.9, "chimes": ["wake", "ready"], "answered": true, "correct": true}}

</details>

## Suite runs

| Run | Scenarios | Live voice | Wall |
|---|---|---|---|
| pre-store (5 runs, 1-4 / 5-14 / 11-14 / 12-14 debugging) | 1-14 | 11.3 min | 11.9 min |
| 1019-081444 | 14-20 | 3.9 min | 4.1 min |
| 1019-081934 | 16-19,27,15 | 7.0 min | 7.3 min |
| 1019-082827 | 22,23 | 0.5 min | 5.5 min |
| 1019-083456 | 21,30,24,25,26,28,29 | 8.2 min | 12.4 min |
| 1019-085302 | 28,4,16,29,24 | 2.6 min | 3.0 min |
| 1019-085616 | 24 | 0.5 min | 0.7 min |
| 1019-123542 | 17,22 | 0.9 min | 2.4 min |
| 1019-123905 | 31 | 3.6 min | 3.7 min |
| 1019-124303 | 31 | 0.4 min | 0.6 min |
| 1019-124526 | 21,30 | 1.7 min | 8.4 min |
| 1019-190230 | 1,17,19,22,23,31 | 2.1 min | 8.4 min |

## Re-runs after the fixes (19 Oct 2026, lead engineer, live GPT-Live, silent virtual audio)

| # | Scenario | Result | Number |
|---|---|---|---|
| 17 | very long utterance (40.6 s story) | **PASS** (was FAIL) | heard in full, answered (mentioned the place from the story). Fix: model audio that starts while the user is still talking is dropped and the mic gate stays open |
| 21 | wake word x3 | **PASS** | scores 0.40-0.41, connected in 2.3-3.0 s, 3/3 answered correctly |
| 30 | 6-minute idle soak, then reconnect | **PASS** (was FAIL) | 402 s, 26 WAKE_HEALTH, 0 false wakes, then "Hey Jarvis" + question worked |
| 31 | false wake (nobody speaks) closes | **PASS** (new) | session closed 14.8 s after opening (`wake_no_speech_s = 12`), the next real wake worked |
| 22 / 23 | false wakes on similar-sounding names | **still FAIL (model limit)** | 4/20 at the 0.15 threshold ("hey Travis", "Jarvis", "hey Jarvie", "Hey Charles"); Hebrew never triggers. A false wake now costs about 12 s of voice (~$0.01) instead of 3 minutes. A custom wake model is the real fix |

Overall: 29 of 31 scenarios pass; the 2 remaining are the wake-word model limit above.
