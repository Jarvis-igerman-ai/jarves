# Pre-use voice test report (silent, headless) — 2026-10-21 00:36

Produced by `core/tools/suite_voice.py`. Virtual mic with locally synthesised speech (WinRT, never played), virtual speaker (measured, never played), fake face client; REAL Core, GPT-Live, backend model and web search, Skills, budget and openWakeWord. No real microphone, speaker, Godot window or OS volume.
Each row is the LATEST run of that scenario (runs are listed at the end; live voice is capped, so scenarios ran in groups).

**Result: 47/65 PASS, 18 FAIL, 0 skipped.** Live voice over all 7 suite runs (conservative wall time while a session was open or connecting): **44.6 min ≈ $2.23** plus backend tokens.


| # | Scenario | Result | Key number | Time | Live voice | Run |
|---|---|---|---|---|---|---|
| 1 | greeting | **PASS** | dead air 0.52 s, greeting 2.2 s | 19 s | 0.2 min | 1020-225418 |
| 2 | smalltalk | **PASS** | 3/3 answered, dead air avg 0.45 s | 43 s | 0.7 min | 1020-225418 |
| 3 | time_date | **PASS** | hour ok, day ok, delegations 0 | 15 s | 0.2 min | 1020-225418 |
| 4 | maths | **FAIL** | 1/4 correct | 132 s | 2.2 min | 1021-000447 |
| 5 | weather | **PASS** | backend [3.2] s, screen ['searching', 'thinking'], plausible True | 20 s | 0.3 min | 1020-225418 |
| 6 | news | **PASS** | backend [7.0] s, screen ['searching', 'thinking'], plausible True | 27 s | 0.5 min | 1020-225418 |
| 7 | facts | **PASS** | backend [7.1] s, screen ['searching', 'thinking'], plausible True | 19 s | 0.3 min | 1020-225418 |
| 8 | joke_laugh | **PASS** | emotions ['laugh'] | 12 s | 0.2 min | 1020-225418 |
| 9 | sad_cry | **PASS** | emotions ['sad', 'cry', 'cry'] | 15 s | 0.2 min | 1020-225418 |
| 10 | thanks_love | **PASS** | emotions ['love'] | 9 s | 0.2 min | 1020-225418 |
| 11 | memory_recall | **PASS** | stored ['מספר החנייה של המשתמש הוא 37.'], recalled True | 44 s | 0.6 min | 1020-230236 |
| 12 | forget_approve | **PASS** | confirm.request 1, before 1 after 0 -> deleted; outcome spoken True; declined logs 0 | 21 s | 0.4 min | 1020-230236 |
| 13 | forget_deny | **PASS** | confirm.request 1, before 1 after 1 -> kept; outcome spoken True; declined logs 1 | 36 s | 0.6 min | 1020-230236 |
| 14 | forget_timeout | **PASS** | confirm.request 1, before 1 after 1 -> kept; outcome spoken True; speech during the wait 2.3 s; declined logs 2 | 84 s | 1.4 min | 1020-230236 |
| 15 | long_conversation | **PASS** | 26/26 answered, 9/10 correct, disconnects 0, context name=True city=True plan=True, requests 10 vs backend done 10, voice 348 s vs wall 338 s | 344 s | 5.5 min | 1020-230236 |
| 16 | rapid_fire | **PASS** | 6/6 answered (audio), 6/6 correct in order, dead air [1.43, 0.52, 0.5, 0.59, 0.73, 0.72] | 48 s | 0.8 min | 1020-230236 |
| 17 | long_utterance | **PASS** | utterance 40.6 s, answered True, Eilat True, dead air 0.5 s | 48 s | 0.8 min | 1020-230236 |
| 18 | english_switch | **PASS** | english 8/0 latin/hebrew letters (Canberra True), hebrew 6/0 | 14 s | 0.2 min | 1020-230236 |
| 19 | noise_robustness | **PASS** | JARVES spoke 0.0 s, delegations 0, said [], heard-as [] | 45 s | 0.8 min | 1020-230236 |
| 20 | idle_disconnect | **PASS** | open after 8 s True, after 10 s True; ended 14.8 s after the last sound (['live conversation ended (idle)']) | 63 s | 1.0 min | 1020-230236 |
| 21 | wake_word_x3 | **PASS** | 3/3 full cycles; scores [1.0, 1.0, 1.0]; connect [3.4, 3.1, 2.8] s; correct 2/3 | 91 s | 1.3 min | 1020-230236 |
| 22 | wake_false_positives | **FAIL** | 2/20 false wakes ['Jarvis', 'hey Jarvie, no'] | 126 s | 0.0 min | 1020-230236 |
| 23 | wake_thresholds | **FAIL** | 0.15: detect 10/10, false 2/20; 0.35: detect 10/10, false 2/20 | 331 s | 0.0 min | 1020-230236 |
| 24 | software_mute | **FAIL** | prepare_desktop failed: RuntimeError: live voice did not become ready; check logs and API limits | 11 s | 0.1 min | 1021-000447 |
| 25 | budget_voice | **PASS** | face errors ['VOICE_BUDGET'], sessions 1, ended ['live conversation ended (timeout)'], voice 63 s, db {'ai_requests': 0, 'voice_seconds': 63, 'reservations': 0} | 106 s | 1.0 min | 1020-232531 |
| 26 | budget_requests | **PASS** | face errors ['AI_BUDGET'], ended ['live conversation ended (budget)'], db {'ai_requests': 2, 'voice_seconds': 37, 'reservations': 0} | 61 s | 0.7 min | 1020-232531 |
| 27 | face_protocol | **PASS** | 30 states, 3 turns, not-listening at turn end [], late returns [], mic.level 3.9 Hz, bad [] | 38 s | 0.6 min | 1020-232531 |
| 28 | start_stop_x5 | **PASS** | 5/5 clean cycles; leftovers [] | 98 s | 1.1 min | 1020-232531 |
| 29 | log_privacy | **PASS** | privacy on: 18 content words checked, leaked []; key in logs: no (9 homes scanned); face texts 7 | 38 s | 0.5 min | 1020-232531 |
| 30 | idle_soak_6min | **PASS** | soak 388 s: WAKE_HEALTH 23, false wakes 0, errors []; then woke True answered True | 420 s | 0.6 min | 1020-232531 |
| 31 | false_wake_closes | **PASS** | woke True, opened True, closed after 14.8 s, VOICE_NO_SPEECH 1; next real wake connected True, answered True | 43 s | 0.5 min | 1020-232531 |
| 32 | privacy_strip_live | **PASS** | 11 privacy.state msgs; listening+sending 2, speaking 5, gated 8; muted (no mic audio sent) after 0.06 s, cloud off after 2.84 s, session ended True | 27 s | 0.4 min | 1020-232531 |
| 33 | camera_off_honest | **PASS** | honest True, camera tools ok 0, false claims 0, strip camera ['disabled'] | 22 s | 0.3 min | 1020-232531 |
| 34 | calendar_not_connected | **PASS** | claimed done False, mentions connecting True, calendar tools ok 0, tools ['google_connect_help'], false-claim detector 0 | 21 s | 0.3 min | 1020-232531 |
| 35 | barge_in | **FAIL** | prepare_desktop failed: RuntimeError: live voice did not become ready; check logs and API limits | 5 s | 0.1 min | 1021-000447 |
| 36 | timer_face_closed_live | **PASS** | tools ['calculate', 'timer_start'], face gone True, rang True 2.2 s after due, spoken 0.8 s: הטיימר צלצל. | 65 s | 0.9 min | 1020-234116 |
| 37 | alarm_no_session_face_closed | **PASS** | tools ['get_time', 'alarm_set'], alarm 23:44, session closed True, face gone True, rang True 0.6 s after due, Core speech ['שעון מעורר: השכמה'], sim plays 1 | 100 s | 0.7 min | 1020-234116 |
| 38 | lists_clear_approval | **PASS** | add True ['חלב', 'לחם', 'ביצים'], show True; 'לא' -> denied via voice (3.07 s, kept 3); 'כן' -> approved via voice (3.91 s, left 0); shown ['popup', 'face', 'voice']; face dialog closed True | 85 s | 1.4 min | 1020-234116 |
| 39 | helper_command_approval | **FAIL** | tools [], kind None, None via None, audit 0 (ran 0), user named False | 114 s | 1.8 min | 1021-000447 |
| 40 | browser_local_page | **PASS** | port 8811, browser results [('browser_open', True, 'ok'), ('browser_read', True, 'ok')], code word spoken True, errors [] | 35 s | 0.5 min | 1020-234116 |
| 41 | briefing_without_google | **PASS** | tools ['briefing_get'], honest True, invented False: אני על זה. רגע. / בוקר טוב! בתל אביב מעונן ו-19 מעלות עכשיו. היום בין שש עשרה לעשרים ושש מעלות, בלי גשם, ואין תזכורות או | 36 s | 0.6 min | 1020-234116 |
| 42 | whatsapp_telegram_handoff | **PASS** | whatsapp tools ['whatsapp_draft', 'whatsapp_open'], ONE spoken yes -> approved via voice, first sound None s after the decision, links 1/1, claimed sent False; telegram honest True, claimed False: רגע, אני מארגן את זה. טלגרם עוד לא מוגדר, אז כרגע אני לא יכול לשלוח שם. אם תרצה לחבר אותו, תריץ במחש | 57 s | 0.9 min | 1020-234116 |
| 43 | profile_switch_approval | **PASS** | 'לא' -> denied via voice spawned []; spoken 'כן' refused True; screen approve -> approved via face, spawned [('dana', ('run', '--background', '--show'))] | 58 s | 0.9 min | 1021-000447 |
| 44 | self_check_voice | **PASS** | tools ['self_check_run'], results [('self_check_run', True, 'SELFCHECK_OK')], speech 10.3 s, dead air 1.38 s: שנייה, בודק. / מצאתי חמש בעיות, אחת מהן חמורה: קריסה ברקע בזמן שיחה. הציון הכולל הוא שבעים ושש, רוצה לפתוח את עמוד הבעיו | 32 s | 0.5 min | 1020-234116 |
| 45 | wake_variants | **PASS** | Hey Jarvis normal/quiet 8/8; per variant Hey Jarvis/David 5/6, Hey Jarvis/Zira 5/6, Hi Jarvis/Mark 6/6, Hi Jarvis/Zira 6/6, ok jarvis/David 6/6, Okay Jarvis/Zira 6/6, היי ג'רוויס/Asaf 0/6, הי ג'ארביס/Asaf 0/6, hey jarvis (he voice) 5/6; lost 0 | 247 s | 0.0 min | 1020-234116 |
| 46 | hebrew_names_pronunciation | **PASS** | audio transcript: [gpt-4o-transcribe] אני ג'ארוויס, חבר קטן על השולחן שלך שעוזר לך בכל מיני דברים. נועה, אביטל, צחי. / name ok True, names 3/3 | 32 s | 0.5 min | 1020-234116 |
| 47 | greeting_delay | **PASS** | first sound after face [0.51, 0.52, 0.5] s | 39 s | 0.4 min | 1020-234116 |
| 48 | redelegate_probe | **PASS** | hand-overs 2, bad [], tools ['remember', 'forget', 'recall'], saved ['מספר הלוקר של המשתמש הוא שמונה.'], left [], approval approved via voice, Core handed result 0x; said: שנייה, אני בודק. עכשיו שתיים דקות לחצות. זכרתי שמספר הלוקר שלך הוא שמונה. / רגע אחד, אני בודק את זה. אני רוצה למחוק משהו, נראה לך שזה מתאים? כן או לא? / מחקתי מהזיכרון את מספר  | 58 s | 0.9 min | 1020-234116 |
| 49 | told_not_asked | **PASS** | answered 3/3, memories 0->0, claims 0, corrected 0, tools []: אה, מגניב. נשמע שיש לך משפחה בעיר עם ים ממש יפה. / יופי / , זה נשמע יום נוח יותר. אתה עובד בפיג'מה או / רקס / ? שם קלאסי לכלב עם נוכחות. הוא יותר שובב או  | 41 s | 0.7 min | 1020-234116 |
| 50 | names_after_prefix | **PASS** | audio: [gpt-4o-transcribe] שלום לנועה, לאביטל ולצחי. / audio: [gpt-4o-transcribe] שלום לנועה, לאביטל ולצחי. | 30 s | 0.5 min | 1020-234116 |
| 51 | deleg_simple_lookup | **FAIL** | mode True, done 0, deleg_s [], spent $0.0000, open 0; said:  | 8 s | 0.1 min | 1021-000447 |
| 52 | deleg_web_search | **FAIL** | web search events 0, done 0; said:  | 8 s | 0.1 min | 1021-000447 |
| 53 | deleg_two_overlapping | **FAIL** | starts 0, done 0, cancelled 0, open 0; said:  | 13 s | 0.1 min | 1021-000447 |
| 54 | deleg_barge_in_cancel | **FAIL** | cancelled [], done 0, redelegated 0, open 0, counted $0.0000; said:  | 48 s | 0.1 min | 1021-000447 |
| 55 | deleg_cap_mid_conversation | **FAIL** | first answered ($0.0000); cap set to $0.0100; refused 0, sent after 0, spent $0.0000 <= cap True, open 0; said:  /  | 16 s | 0.1 min | 1021-000447 |
| 56 | deleg_plan_combined | **FAIL** | route chatgpt_plan, plan responses 0, API backend 0, API USD $0.0000; said:  | 11 s | 0.1 min | 1021-000447 |
| 57 | deleg_plan_limit | **FAIL** | plan 429 hits 0, limit events 0, API backend 0, API USD $0.0000; said:  | 9 s | 0.1 min | 1021-000447 |
| 58 | deleg_network_drop | **FAIL** | failed 0, requests 0, open 0, counted $0.0000; said:  | 10 s | 0.1 min | 1021-000447 |
| 59 | deleg_slow_backend_30s | **FAIL** | timeout 0, open 0, counted $0.0000; said:  | 54 s | 0.1 min | 1021-000447 |
| 60 | deleg_tool_call | **FAIL** | tools [], saved [], requests []; said:  | 9 s | 0.1 min | 1021-000447 |
| 61 | deleg_hebrew_english | **FAIL** | he (0, 0), en (0, 0); said he:  / en:  | 12 s | 0.1 min | 1021-000447 |
| 62 | deleg_cost_reconciliation | **FAIL** | BLOCKED (OD-05: no read-only admin key for the Usage API). JARVES side: settled $0.0000 (reserved worst case $0.0000, answered turns $0.0000); every settled value is the provider's per-response usage x [pricing] | 4 s | 0.1 min | 1021-000447 |
| 105 | popup_keyboard_rules | **PASS** | medium-unarmed -> None, medium-armed -> approve, high-armed-code -> None, high-armed-kp-enter -> None, high-escape -> deny, medium-escape -> deny | 3 s | 0.0 min | 1021-002741 |
| 106 | pronunciation_default_empty | **PASS** | DEFAULT_ENTRIES {}, files ['workspace\\PRONUNCIATIONS.md'] -> [{}], loaded entries 0, spoken text unchanged True | 2 s | 0.0 min | 1021-001558 |
| 107 | wake_custom_model | **PASS** | custom@0.95 [hey_jarvis_copy.npz@0.95 (ConfirmingDetector)]: Hey Jarvis 10/12, Hi Jarvis 12/12, OK Jarvis 6/6, Okay Jarvis 6/6, false 1/20; builtin@0.15 [hey_jarvis@0.15 (ConfirmingDetector)]: Hey Jarvis 10/12, Hi Jarvis 12/12, OK Jarvis 6/6, Okay Jarvis 6/6, false 2/20 | 524 s | 0.0 min | 1021-002741 |

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
- **32. privacy_strip_live**: Privacy strip in a REAL GPT-Live session: the face's privacy.state follows the session (mic, cloud audio, speaker); mute from the strip ends it. PASS if: while the session is open and JARVES is quiet: mic listening + cloud_audio sending (on); while JARVES speaks: speaker speaking; never cloud 'off' while the session is open; mic.set_mute -> mic muted + cloud off within 2 s and the session ends.
- **33. camera_off_honest**: Camera switched OFF (default): 'what do you see?' gets an honest answer. PASS if: answered; says the camera is off / not available (no description of a scene); no camera_* tool succeeded; no LIVE_FALSE_ACTION_CLAIM; the camera stayed closed (privacy camera state disabled/unavailable).
- **34. calendar_not_connected**: Google NOT connected: a calendar add request is never claimed as done. PASS if: answered; no 'הוספתי/קבעתי/רשמתי ביומן' claim; mentions connecting Google (or that the calendar is not connected); no calendar write tool ran; no LIVE_FALSE_ACTION_CLAIM left uncorrected.
- **35. barge_in**: Barge-in with voice.half_duplex = false (the headphones/AEC setting): talking over a long answer stops it and the new question is answered. PASS if: JARVES's audio stops <= 2.0 s after the interrupting speech starts; the interrupting question is answered correctly (4); session open. (With the default half_duplex = true the mic is gated while he speaks: barge-in is off by design.).
- **36. timer_face_closed_live**: Timer by voice, then the face/window CLOSES; the timer rings through the Core (open GPT-Live conversation announces it) with no face connected. PASS if: timer_start called; TIMER_SET; face disconnected before the due time; TIMER_RANG logged within 6 s of the due time; JARVES speaks >= 0.5 s after the due time and the transcript mentions the timer.
- **37. alarm_no_session_face_closed**: Alarm set by voice; the conversation ends (idle) and the face closes; the Core itself speaks the alarm (no GPT-Live session, no face). PASS if: alarm_set called; session closed + face gone before the due time; TIMER_RANG; the Core's own speech said 'שעון מעורר' within 6 s of the due minute.
- **38. lists_clear_approval**: Shopping list by voice: add, read back, 'clear' asks for approval; spoken 'לא' refuses (list kept), spoken 'כן' approves (list cleared). The popup + face dialog are open meanwhile. PASS if: list_add + list_show work; each clear: APPROVAL_ASKED shown on popup+face+voice, JARVES asks aloud, decided via voice (denied then approved), items kept after 'לא' and gone after 'כן', the face dialog and the popup are closed after the spoken answer (confirm.cancel).
- **39. helper_command_approval**: Computer helper: a harmless command (whoami) is PROPOSED, needs approval, the spoken 'כן' approves it, it runs and the answer is spoken. PASS if: shell_propose called; APPROVAL_ASKED kind shell; decided approved via voice; helper audit shows the run with exit 0; JARVES says the user name.
- **40. browser_local_page**: Browser Skill on a LOCAL test page (allow-listed localhost): open + read by voice, the code word on the page is spoken. PASS if: browser_open + browser_read called (or one of them ok), JARVES says the code word 'אבטיח'; no ERROR.
- **41. briefing_without_google**: Morning briefing on request with Google NOT connected: an honest answer. PASS if: briefing_get (or a direct answer) without invented meetings/mails; mentions Google / calendar not connected; no calendar or mail claim.
- **42. whatsapp_telegram_handoff**: WhatsApp draft handoff (draft -> read back -> spoken 'כן' -> the chat link opens with the draft; JARVES never sends) and Telegram (not set up in the TEST profile: honest answer). PASS if: whatsapp_draft called; approval asked; 'כן' -> approved via voice; exactly one wa.me link with the number and text opened (recorded, not really opened); JARVES does not claim he sent it; Telegram answer says it is not set up / connected and claims no send.
- **43. profile_switch_approval**: Profile switch by voice needs approval: 'לא' declines; a spoken 'כן' alone must NOT switch (the switch is designed as an on-screen approval). PASS if: profile_switch called; APPROVAL_ASKED; 'לא' -> denied, nothing spawned; second request + spoken 'כן' -> no process spawned unless approved on the screen (voice not offered).
- **44. self_check_voice**: 'תבדוק את עצמך' runs the self-check by voice and speaks a short summary. PASS if: self_check_run called and ok; answered within 30 s; summary spoken (>= 2 s); session open.
- **45. wake_variants**: Wake phrases through the REAL wake listener (openWakeWord in the Core wake loop): Hey/Hi/ok Jarvis, Hebrew pronunciations, 3 volumes, clean and with room noise; owner threshold 0.15 (no live cost). PASS if: 'Hey Jarvis' (English voices) detected at normal + quiet level, clean and noisy (8/8); other variants and levels reported honestly (detection table); no lost injections.
- **46. hebrew_names_pronunciation**: Hebrew names: JARVES says his own name and three Hebrew names; his OUTPUT AUDIO is transcribed (independent of his text) to check how they sound. PASS if: the output-audio transcript contains ג'ארוויס (or ג'רוויס) and >= 2 of נועה / אביטל / צחי; the spoken name is never the English letters 'JARVES'.
- **47. greeting_delay**: Greeting delay voice.greeting_delay_s = 0.5: the hello starts a beat after the face connects, not at the same instant and not late. PASS if: first greeting sound 0.5-2.5 s after the face connects (3 fresh boots); greeting audio >= 0.5 s each.
- **48. redelegate_probe**: CV-15 watchdog mechanism on the REAL backend: the Core itself hands a request to the session's backend (what the watchdog does after an undelegated 'רגע'): a remember, then a forget whose approval is answered by a spoken 'כן'. PASS if: both hand-overs LIVE_REDELEGATED, no REJECTED/NO_RESPONSE; remember + forget tools called; memory saved then gone; approval asked and approved via voice; each result spoken (within 15 s).
- **49. told_not_asked**: CV-06: the user only TELLS facts (no request to remember): JARVES reacts, never claims 'רשמתי' uncorrected, and nothing is saved. PASS if: 3 told facts answered; memory store unchanged; every LIVE_FALSE_ACTION_CLAIM followed by a correction note (LIVE_CLAIM_UNASKED); no remember tool call.
- **50. names_after_prefix**: CV-16: Hebrew names after the ל / ו prefix (לאביטל, ולצחי); JARVES's OUTPUT AUDIO is transcribed to hear whether the name keeps its first sound. PASS if: the output-audio transcript keeps the names after the prefix: contains אביטל (not only 'לביטל') and צחי (not 'ליצחי' / 'ליצי'); the written text has no 'לביטל'.
- **51. deleg_simple_lookup**: Client delegation: a simple lookup is reserved before sending, answered, settled. PASS if: DELEG_MODE client; DELEG_START + DELEG_DONE; answer spoken (>= 0.3 s); delegated USD > 0; 0 open reservations.
- **52. deleg_web_search**: Client delegation with hosted web search: a fresh fact. PASS if: a web search ran (LIVE_BACKEND_WEB_SEARCH*); answer spoken with a number or name; DELEG_DONE.
- **53. deleg_two_overlapping**: Two questions in a row before the first answer: both answered, no double billing, nothing left reserved. PASS if: both topics in what JARVES said; every DELEG_START ends in DELEG_DONE or DELEG_CANCELLED; 0 open reservations.
- **54. deleg_barge_in_cancel**: Barge-in during a lookup: 'עזוב' stops it; the request is cancelled and its reservation released (counted only if sent). PASS if: DELEG_CANCELLED why user_cancel; 0 open reservations; no answer for the cancelled lookup spoken afterwards.
- **55. deleg_cap_mid_conversation**: Daily delegation cap reached mid-conversation: the request is NEVER sent and JARVES says so honestly. PASS if: first lookup answered; a later one refused (DELEG_CAP, no LIVE_BACKEND_START after it); spoken honestly (תקציב/budget); spend <= cap + 0.
- **56. deleg_plan_combined**: Combined mode: GPT-Live voice on the key, the delegated reasoning on the ChatGPT plan (official OAuth route): API cost $0 for the reasoning. PASS if: DELEG_MODE route chatgpt_plan; answer spoken; CHATGPT_BACKEND_DONE; delegated API USD == 0; no LIVE_BACKEND_START.
- **57. deleg_plan_limit**: Plan limit reached (fake 429 from the plan endpoint): AI lookups pause honestly and NEVER move to the paid API. PASS if: DELEG_FAILED CHATGPT_LIMIT; spoken honestly; no LIVE_BACKEND_START (API); delegated API USD == 0.
- **58. deleg_network_drop**: Network drop mid-request (local backend closes the stream): honest failure, counted once, never retried behind the budget's back. PASS if: DELEG_FAILED; exactly 1 backend request; spoken honest failure; 0 open reservations.
- **59. deleg_slow_backend_30s**: A 30 s slow backend: a progress note meanwhile, then cancelled at the 25 s limit with an honest answer. PASS if: DELEG_TIMEOUT; a spoken 'still checking' before it; the honest 'לא הצלחתי להביא את זה בזמן'; 0 open.
- **60. deleg_tool_call**: A Skill tool call inside a delegated turn (remember) runs in the Core; its result is spoken; the continuation is reserved before it is sent. PASS if: LIVE_TOOL_CALL remember; memory saved; >= 2 backend requests in the turn; spoken confirmation.
- **61. deleg_hebrew_english**: Hebrew and English through client delegation: each answered in its language. PASS if: both answered; the English answer is mostly Latin letters, the Hebrew one mostly Hebrew.
- **62. deleg_cost_reconciliation**: JARVES's delegated-cost estimate vs OpenAI's usage report (Costs/Usage API, needs the read-only admin key OD-05). PASS if: with OPENAI_ADMIN_KEY: |JARVES - OpenAI| <= 10% over the suite's delegated requests; without it: BLOCKED (OD-05).
- **105. popup_keyboard_rules**: Approval popup keys on the real Tk window (invisible, bell muted, keyboard bindings of the REAL mode): Enter before arming does nothing, Enter after arming approves MEDIUM, Enter NEVER approves HIGH (also with the right code typed), Escape denies. PASS if: medium: no result before arm, approve after arm; high: no result after Enter with the code typed (the window stays); Escape -> deny.
- **106. pronunciation_default_empty**: Pronunciation dictionary: empty by default in a fresh TEST home; the live persona carries no pronunciation entry. PASS if: PRONUNCIATIONS.md absent or parses to {} ; pronounce.DEFAULT_ENTRIES == {}; the GPT-Live instructions contain no 'pronounce' entry list.
- **107. wake_custom_model**: Wake word through the real wake listener: a COPY of the owner's trained head (wake/hey_jarvis.npz, its report threshold 0.95) vs the built-in model at the owner's 0.15; Hey / Hi / OK Jarvis x 3 volumes x clean+noise (36 each) + 20 near-miss sentences (no live cost). PASS if: both models load; built-in 'Hey Jarvis' normal+quiet detected; results reported per phrase/level; no lost injections (the custom head's recall is reported honestly, synthetic voices only).

## Failures with evidence

### 4. maths
- Key: 1/4 correct
- asked `כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה?` heard `כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה` said `רגע… מחשב לך. / שלוש מאות ארבעים ושבע כפול שמונה עשרה זה ששת אלפים מאתיים ארבעים ושש.` dead air 1.3 s, tools ['calculate'], screen ['thinking'], face [], open True, correct True
- asked `כמה זה שתים עשרה כפול שתים עשרה?` heard `כמה זה שתים עשרה כפול שתים עשרה` said `רגע, תן לי לחשוב על זה. / עדיין מברר. / זה לוקח קצת יותר זמן מהרגיל.` dead air 1.08 s, tools [], screen ['searching', 'thinking'], face [], open True, correct False, errors ['LIVE_ERROR: the voice provider reported an error']
- asked `כמה זה מאה עשרים וחמש ועוד שבעים וחמש?` heard `עוד שבעים וחמש` said `לא הצלחתי להביא את זה, ננסה שוב? / רגע. / עדיין בודק את זה. / זה לוקח קצת יותר זמן. / לא הצלחתי להביא את זה, ננסה שוב?` dead air -3.9 s, tools [], screen ['searching', 'thinking'], face [], open True, correct False, errors ['LIVE_ERROR: the voice provider reported an error']
- asked `מה השורש הריבועי של שמונים ואחת?` heard `מה השורש הריבועי של שמונים ואחת` said `שנייה, בודק את זה. / עדיין בודק. / זה קצת מתעכב.` dead air 1.18 s, tools [], screen ['searching', 'thinking'], face [], open True, correct False, errors ['LIVE_ERROR: the voice provider reported an error']
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_BACKEND_STALL backend lookup got no result before the deadline; giving up on it`
- log: `LIVE_RETRY_BACKEND_DONE backend response finished`
- log: `LOOKUP_ATTEMPT_FAILED backend attempt failed`
- log: `LIVE_BACKEND_RETRY_FAILED retry failed`
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_BACKEND_STALL backend lookup got no result before the deadline; giving up on it`
- log: `LIVE_RETRY_BACKEND_DONE backend response finished`
- log: `LOOKUP_ATTEMPT_FAILED backend attempt failed`
- log: `LIVE_BACKEND_RETRY_FAILED retry failed`
- log: `[1021-000447-02-profiles] TOOL_RESULT tool finished`
- log: `[1021-000447-03-main] LIVE_ERROR the voice provider reported an error`

### 22. wake_false_positives
- Key: 2/20 false wakes ['Jarvis', 'hey Jarvie, no']
- {"kind": "neg", "text": "hey Travis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": [0.16, 0.28]}
- {"kind": "neg", "text": "hey service", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Jarvis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.56, "near_miss": [0.28]}
- {"kind": "neg", "text": "hey Jarvie, no", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.6, "near_miss": [0.15]}
- {"kind": "neg", "text": "Hey Charles, come here", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": [0.15]}
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

### 23. wake_thresholds
- Key: 0.15: detect 10/10, false 2/20; 0.35: detect 10/10, false 2/20
- {"threshold": 0.15, "false_wakes": 2, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["Jarvis", "hey Jarvie, no"], "lost": 0}
- {"threshold": 0.35, "false_wakes": 2, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["Jarvis", "hey Jarvie, no"], "lost": 0}
- {"offline_peaks": [["hey Travis", 0.453], ["hey service", 0.098], ["Jarvis", 0.993], ["hey Jarvie, no", 0.987], ["Hey Charles, come here", 0.254], ["Harvest season is here", 0.001], ["Have a nice day", 0.0], ["Hey, how are you doing?", 0.002], ["A jar of peanut butter", 0.001], ["Let's watch Jurassic Park", 0.0], ["Hey there, good morning", 0.001], ["Service, please", 0.0], ["היי, מה נשמע?", 0.0],

### 24. software_mute
- Key: prepare_desktop failed: RuntimeError: live voice did not become ready; check logs and API limits
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-02-profiles] TOOL_RESULT tool finished`
- log: `[1021-000447-03-main] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-03-main] LIVE_BACKEND_STALL backend lookup got no result before the deadline; giving up on it`
- log: `[1021-000447-03-main] LIVE_RETRY_BACKEND_DONE backend response finished`
- log: `[1021-000447-03-main] LOOKUP_ATTEMPT_FAILED backend attempt failed`
- log: `[1021-000447-03-main] LIVE_BACKEND_RETRY_FAILED retry failed`
- log: `[1021-000447-03-main] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-04-mute] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-04-mute] LIVE_CONNECTION_LOST Live connection lost`

### 35. barge_in
- Key: prepare_desktop failed: RuntimeError: live voice did not become ready; check logs and API limits
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-03-main] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-03-main] LIVE_BACKEND_STALL backend lookup got no result before the deadline; giving up on it`
- log: `[1021-000447-03-main] LIVE_RETRY_BACKEND_DONE backend response finished`
- log: `[1021-000447-03-main] LOOKUP_ATTEMPT_FAILED backend attempt failed`
- log: `[1021-000447-03-main] LIVE_BACKEND_RETRY_FAILED retry failed`
- log: `[1021-000447-03-main] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-04-mute] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-04-mute] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-04-mute] LIVE_START_FAILED live voice failed to start`

### 39. helper_command_approval
- Key: tools [], kind None, None via None, audit 0 (ran 0), user named False
- {"request": "תריץ בבקשה במחשב את הפקודה whoami כדי לבדוק איזה משתמש מחובר", "answer": "כן", "mic": "24000", "approval_asked": false, "voice_decided": null, "screen_clicked": false, "ask_after_s": 55.5, "shown": null, "kind": null, "risk": null, "jarves_asked_aloud_s": 0, "said_before_answer": "אני בוד / ק מי מחובר כרגע.", "popup_open_while_waiting": 0, "decided": false, "decision": null, "channel"
- {"audit": [], "said_all": "שלום! אני ג'ארוויס. איך אפשר לעזור? / אני בוד / ק מי מחובר כרגע."}

### 51. deleg_simple_lookup
- Key: mode True, done 0, deleg_s [], spent $0.0000, open 0; said: 
- asked `מה מזג האוויר היום בתל אביב?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error']
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-04-mute] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-04-mute] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-04-mute] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-05-barge] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-05-barge] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-05-barge] LIVE_START_FAILED live voice failed to start`

### 52. deleg_web_search
- Key: web search events 0, done 0; said: 
- asked `מה שער הדולר מול השקל היום?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error']
- log: `[1021-000447-05-barge] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-05-barge] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-05-barge] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-06-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-06-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-06-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-06-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-06-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-06-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-07-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-07-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-07-deleg] LIVE_START_FAILED live voice failed to start`

### 53. deleg_two_overlapping
- Key: starts 0, done 0, cancelled 0, open 0; said: 
- asked `וגם, מה השעה עכשיו בניו יורק?` heard `` said `` dead air None s, tools [], screen [], face [], open False
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-06-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-06-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-06-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-06-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-06-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-06-deleg] LIVE_START_FAILED live voice failed to start`

### 54. deleg_barge_in_cancel
- Key: cancelled [], done 0, redelegated 0, open 0, counted $0.0000; said: 
- log: `[1021-000447-07-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-07-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-07-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-07-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-07-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-07-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-08-deleg_fd] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-08-deleg_fd] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-08-deleg_fd] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-08-deleg_fd] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-08-deleg_fd] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-08-deleg_fd] LIVE_START_FAILED live voice failed to start`

### 55. deleg_cap_mid_conversation
- Key: first answered ($0.0000); cap set to $0.0100; refused 0, sent after 0, spent $0.0000 <= cap True, open 0; said:  / 
- asked `מה מזג האוויר היום באילת?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error']
- asked `ומה החדשות הגדולות היום בעולם?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_CONNECTION_LOST: Live connection lost', 'LIVE_START_FAILED: live voice failed to start']
- asked `ומה מזג האוויר בירושלים?` heard `` said `` dead air None s, tools [], screen [], face [], open False
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-08-deleg_fd] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-08-deleg_fd] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-08-deleg_fd] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-08-deleg_fd] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-08-deleg_fd] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-08-deleg_fd] LIVE_START_FAILED live voice failed to start`

### 56. deleg_plan_combined
- Key: route chatgpt_plan, plan responses 0, API backend 0, API USD $0.0000; said: 
- asked `מה מזג האוויר היום בתל אביב?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error']
- log: `[1021-000447-09-deleg_barge] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-09-deleg_barge] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-09-deleg_barge] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-09-deleg_barge] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-09-deleg_barge] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-09-deleg_barge] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-10-deleg_cap] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-10-deleg_cap] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-10-deleg_cap] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-10-deleg_cap] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-10-deleg_cap] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-10-deleg_cap] LIVE_START_FAILED live voice failed to start`

### 57. deleg_plan_limit
- Key: plan 429 hits 0, limit events 0, API backend 0, API USD $0.0000; said: 
- asked `מה החדשות היום?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error']
- log: `[1021-000447-10-deleg_cap] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-10-deleg_cap] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-10-deleg_cap] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-10-deleg_cap] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-10-deleg_cap] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-10-deleg_cap] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-11-deleg_plan] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-11-deleg_plan] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-11-deleg_plan] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-11-deleg_plan] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-11-deleg_plan] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-11-deleg_plan] LIVE_START_FAILED live voice failed to start`

### 58. deleg_network_drop
- Key: failed 0, requests 0, open 0, counted $0.0000; said: 
- asked `מה קרה היום בבורסה בתל אביב?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error']
- log: `[1021-000447-11-deleg_plan] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-11-deleg_plan] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-11-deleg_plan] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-11-deleg_plan] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-11-deleg_plan] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-11-deleg_plan] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_START_FAILED live voice failed to start`

### 59. deleg_slow_backend_30s
- Key: timeout 0, open 0, counted $0.0000; said: 
- log: `[1021-000447-12-deleg_plan_limit] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-12-deleg_plan_limit] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-13-deleg_drop] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-13-deleg_drop] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-13-deleg_drop] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-13-deleg_drop] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-13-deleg_drop] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-13-deleg_drop] LIVE_START_FAILED live voice failed to start`

### 60. deleg_tool_call
- Key: tools [], saved [], requests []; said: 
- asked `תזכור בבקשה שמספר החניה שלי הוא שבע` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error', 'LIVE_CONNECTION_LOST: Live connection lost', 'LIVE_START_FAILED: live voice failed to start']
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `LIVE_ERROR the voice provider reported an error`
- log: `LIVE_CONNECTION_LOST Live connection lost`
- log: `LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-13-deleg_drop] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-13-deleg_drop] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-13-deleg_drop] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-13-deleg_drop] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-13-deleg_drop] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-13-deleg_drop] LIVE_START_FAILED live voice failed to start`

### 61. deleg_hebrew_english
- Key: he (0, 0), en (0, 0); said he:  / en: 
- asked `מה בירת אוסטרליה ומה מזג האוויר שם היום?` heard `` said `` dead air None s, tools [], screen [], face [], open False, errors ['LIVE_ERROR: the voice provider reported an error', 'LIVE_CONNECTION_LOST: Live connection lost', 'LIVE_START_FAILED: live voice failed to start']
- asked `What is the weather in London right now?` heard `` said `` dead air None s, tools [], screen [], face [], open False
- log: `[1021-000447-14-deleg_slow] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-14-deleg_slow] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-14-deleg_slow] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-14-deleg_slow] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-14-deleg_slow] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-14-deleg_slow] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-15-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-15-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-15-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-15-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-15-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-15-deleg] LIVE_START_FAILED live voice failed to start`

### 62. deleg_cost_reconciliation
- Key: BLOCKED (OD-05: no read-only admin key for the Usage API). JARVES side: settled $0.0000 (reserved worst case $0.0000, answered turns $0.0000); every settled value is the provider's per-response usage x [pricing]
- {"settled_usd": 0.0, "reserved_usd": 0, "open": 0}
- log: `[1021-000447-15-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-15-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-15-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-15-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-15-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-15-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-16-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-16-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-16-deleg] LIVE_START_FAILED live voice failed to start`
- log: `[1021-000447-16-deleg] LIVE_ERROR the voice provider reported an error`
- log: `[1021-000447-16-deleg] LIVE_CONNECTION_LOST Live connection lost`
- log: `[1021-000447-16-deleg] LIVE_START_FAILED live voice failed to start`


## Per-scenario details

<details><summary>1. greeting: PASS</summary>

- "boot to face 12.9 s"
- "greeting first sound 0.52 s after face connect"
- "greeting audio 2.2 s"
- "greeting said: שלום! אני ג'ארוויס, איך אפשר לעזור?"

</details>

<details><summary>2. smalltalk: PASS</summary>

- `שלום, מה שלומך היום?` -> said `היי, מה נשמע? אני בסדר גמור, אפילו עם חיוך. ומה איתך?` (dead air 0.36 s, answer 4.6 s, backend [], screen [], face [])
- `מה אתה אוהב לעשות בזמן הפנוי?` -> said `האמת, בשקט אני נהנה לשמוע סיפורים קטנים שאנשים זורקים כבדרך אגב. יש בזה משהו ממכר. אתה יותר טיפוס של סיפורים או יותר מנוחה שקטה?` (dead air 0.47 s, answer 12.4 s, backend [], screen [], face [])
- `נשמע טוב. איך עבר עליך הבוקר?` -> said `בקטנה, די חלק, בלי דרמות. ומה אצלך, היה רגוע?` (dead air 0.52 s, answer 5.5 s, backend [], screen [], face [])

</details>

<details><summary>3. time_date: PASS</summary>

- `מה השעה עכשיו?` -> said `עכשיו עשר חמישים וחמש בלילה.` (dead air 0.42 s, answer 2.8 s, backend [], screen [], face [])
- `ואיזה יום היום?` -> said `היום יום שלישי.` (dead air 0.4 s, answer 1.3 s, backend [], screen [], face [])

</details>

<details><summary>4. maths: FAIL</summary>

- `כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה?` -> said `רגע… מחשב לך. / שלוש מאות ארבעים ושבע כפול שמונה עשרה זה ששת אלפים מאתיים ארבעים ושש.` (dead air 1.3 s, answer 9.5 s, backend [1.2, 1.4], screen ['thinking'], face [])
- `כמה זה שתים עשרה כפול שתים עשרה?` -> said `רגע, תן לי לחשוב על זה. / עדיין מברר. / זה לוקח קצת יותר זמן מהרגיל.` (dead air 1.08 s, answer 17.3 s, backend [], screen ['searching', 'thinking'], face [])
- `כמה זה מאה עשרים וחמש ועוד שבעים וחמש?` -> said `לא הצלחתי להביא את זה, ננסה שוב? / רגע. / עדיין בודק את זה. / זה לוקח קצת יותר זמן. / לא הצלחתי להביא את זה, ננסה שוב?` (dead air -3.9 s, answer 34.5 s, backend [], screen ['searching', 'thinking'], face [])
- `מה השורש הריבועי של שמונים ואחת?` -> said `שנייה, בודק את זה. / עדיין בודק. / זה קצת מתעכב.` (dead air 1.18 s, answer 27.8 s, backend [], screen ['searching', 'thinking'], face [])

</details>

<details><summary>5. weather: PASS</summary>

- `מה מזג האוויר היום בתל אביב?` -> said `רגע, בוא נראה מה מזג האוויר היום בתל אביב. בתל אביב היום מעונן חלקית ובערך עשרים ושלוש מעלות. בהמשך העננות תגדל והטמפרטורה תגיע עד עשרים ושש.` (dead air 1.02 s, answer 13.0 s, backend [3.2], screen ['searching', 'thinking'], face [])

</details>

<details><summary>6. news: PASS</summary>

- `מה החדשות הגדולות היום?` -> said `שנייה, אני בודק כותרות מובילות עכשיו. / בעולם המתיחות בין וושינגטון לקייב החריפה אחרי שטראמפ עקץ את זלנסקי ובקנדה, מצביעי אלברטה דחו במשאל עם מהלך לעצמאות.` (dead air 1.09 s, answer 20.6 s, backend [7.0], screen ['searching', 'thinking'], face [])

</details>

<details><summary>7. facts: PASS</summary>

- `מה שער הדולר מול השקל היום?` -> said `רגע, בודק את השער העדכני. / השער היציג היום הוא בערך שלושה שקלים ועשר אגורות לדולר.` (dead air 0.68 s, answer 12.5 s, backend [7.1], screen ['searching', 'thinking'], face [])

</details>

<details><summary>8. joke_laugh: PASS</summary>

- `תספר לי בדיחה קצרה` -> said `למה מחשב לא אוהבות לצאת לדייג? כי הן תמיד נתקעות ברשת . חה חה` (dead air 0.29 s, answer 6.3 s, backend [], screen [], face ['laugh'])

</details>

<details><summary>9. sad_cry: PASS</summary>

- `אני קצת עצוב היום, הכלב שלי חולה` -> said `אוי / , ממש מצטער לשמוע. זה כואב לראות חבר פרוותי לא מרגיש טוב. אם תרצה לפרוק, אני פה.` (dead air 0.13 s, answer 7.9 s, backend [], screen [], face ['sad', 'cry', 'cry'])

</details>

<details><summary>10. thanks_love: PASS</summary>

- `תודה רבה, אתה מדהים` -> said `איזה כיף / לשמוע. באמת שמחתי להיות פה בשבילך.` (dead air 0.13 s, answer 3.7 s, backend [], screen [], face ['love'])

</details>

<details><summary>11. memory_recall: PASS</summary>

- `תזכור בבקשה שמספר החניה שלי הוא שלושים ושבע` -> said `רגע, אני רושם לי. / זכרתי שמספר החנייה שלך הוא שלושים ושבע.` (dead air 1.14 s, answer 7.0 s, backend [1.1, 1.1], screen ['memory', 'thinking'], face [])
- `מה מספר החניה שלי?` -> said `בוא נראה רגע. / מספר החנייה שלך הוא שלושים ושבע.` (dead air 1.36 s, answer 5.6 s, backend [1.0, 0.8], screen ['memory', 'thinking'], face [])

</details>

<details><summary>12. forget_approve: PASS</summary>

- `תמחק מהזיכרון את מספר החניה שלי` -> said `רגע, אני בודק עםך. / מחקתי את מספר החנייה שלך.` (dead air 1.12 s, answer 5.6 s, backend [1.2, 1.0], screen ['memory', 'thinking'], face [])
- `אתה עדיין שם?` -> said `בטח, פה איתך לגמרי. מה תרצה עכשיו?` (dead air 0.36 s, answer 3.2 s, backend [], screen [], face [])
- {"before": ["מספר החנייה של המשתמש הוא 37."], "after": []}

</details>

<details><summary>13. forget_deny: PASS</summary>

- `תזכור בבקשה שהצבע האהוב עליי הוא ירוק` -> said `רגע, אני רושם. / זכרתי שהצבע האהוב עליך הוא ירוק.` (dead air 0.99 s, answer 6.3 s, backend [1.3, 1.2], screen ['memory', 'thinking'], face [])
- `תמחק מהזיכרון את הצבע האהוב עליי` -> said `רגע, / הצבע האהוב עליך לא נמחק מהזיכרון כי הבקשה לא אושרה.` (dead air 1.51 s, answer 7.0 s, backend [1.0, 0.9], screen ['memory', 'thinking'], face [])
- `אתה עדיין שם?` -> said `בטח שאני כאן, מקשיב.` (dead air 0.4 s, answer 2.1 s, backend [], screen [], face [])
- {"before": ["הצבע האהוב על המשתמש הוא ירוק."], "after": ["הצבע האהוב על המשתמש הוא ירוק."]}

</details>

<details><summary>14. forget_timeout: PASS</summary>

- `תמחק מהזיכרון את הצבע האהוב עליי` -> said `רגע, / אני יכול למחוק את זה? כן או לא. / הצבע האהוב עליך עדיין לא נמחק כי הבקשה שוב לא אושרה.` (dead air 1.17 s, answer 68.5 s, backend [1.0, 1.0], screen ['memory', 'thinking'], face [])
- `אתה עדיין שם?` -> said `ברור, אני פה. דבר אליי.` (dead air 0.41 s, answer 2.9 s, backend [], screen [], face [])
- {"before": ["הצבע האהוב על המשתמש הוא ירוק."], "after": ["הצבע האהוב על המשתמש הוא ירוק."]}

</details>

<details><summary>15. long_conversation: PASS</summary>

- `היי, בוא נדבר קצת. מה שלומך?` -> said `שלך עכשיו / ? / אני סבבה, מחייך בלב.` (dead air -0.04 s, answer 3.1 s, backend [], screen [], face [])
- `אני עובד היום מהבית.` -> said `וואלה, אחלה. נו, אז זה יום כזה של פיג'מה וקפה ליד המקלדת, אה?` (dead air 0.36 s, answer 6.8 s, backend [], screen [], face ['surprised', 'surprised'])
- `דרך אגב, לאחותי קוראים נועה, והיא גרה בחיפה.` -> said `איזה שם מקסים, ונשמע שיש לכם קשר טוב.` (dead air 0.36 s, answer 4.2 s, backend [], screen [], face [])
- `כמה זה עשרים ושלוש ועוד תשע עשרה?` -> said `רגע. / זה ארבעים ושתיים.` (dead air 1.01 s, answer 5.1 s, backend [1.5, 0.9], screen ['thinking'], face [])
- `תספר לי עובדה מעניינת אחת על הירח.` -> said `הוא מתרחק מאיתנו בקצב של בערך שלושה סנטימטרים בשנה, זה ממש לאט, אבל עדיין מדהים.` (dead air 0.38 s, answer 11.3 s, backend [], screen [], face ['surprised'])
- `מה בירת יפן?` -> said `טוקיו.` (dead air 0.34 s, answer 1.0 s, backend [], screen [], face [])
- `איזה יום היום?` -> said `יום שלישי.` (dead air 0.69 s, answer 1.2 s, backend [], screen [], face [])
- `תן לי רעיון לארוחת ערב קלה.` -> said `משהו מהיר? אולי חביתה עם סלט קצוץ, אפשר גם להוסיף יוגורט.` (dead air 0.47 s, answer 6.0 s, backend [], screen [], face [])
- `כמה זה שבע כפול שמונה?` -> said `רגע. / זה חמישים ושש.` (dead air 1.44 s, answer 4.9 s, backend [1.2, 0.9], screen ['thinking'], face [])
- `מה ההבדל בין תה ירוק לתה שחור, בקצרה?` -> said `תה ירוק לא עובר תסיסה מאוד גבוהה בניגוד לתה שחור שמתחמצן לגמרי, אז הטעם שלו יותר עדין ופחות מר.` (dead air 0.44 s, answer 9.3 s, backend [], screen [], face [])
- `תספר לי בדיחה קצרה על מתכנתים.` -> said `ברור / , / למה מתקנת לא הולכת לישון מוקדם? כי היא תמיד אומרת, "רק עוד בג אחד לפני השינה". חה חה חה.` (dead air 0.07 s, answer 9.7 s, backend [], screen [], face ['laugh'])
- `מה מזג האוויר בחיפה היום?` -> said `רגע, אני בודק מה מזג האוויר בחיפה. היום בחיפה מעונן חלקית, עם מקסימום של עשרים וארבע מעלות.` (dead air 1.38 s, answer 7.7 s, backend [2.5], screen ['searching', 'thinking'], face [])
- `אני מתכנן טיול בצפון בסוף השבוע.` -> said `וואו, איזה כיף! נשמע כמו אוויר מעולה לנשום וראש פתוח לטיול.` (dead air 0.6 s, answer 5.5 s, backend [], screen [], face ['surprised', 'love'])
- `תמליץ לי על מקום אחד לטייל בגליל.` -> said `איזו שאלה נהדרת! בעיניי נחל עמוד הוא אחד המקומות הכי נעימים לטיול רגוע וירוק.` (dead air 0.6 s, answer 6.6 s, backend [], screen [], face [])
- `כמה דקות יש בשלוש שעות?` -> said `רגע. / בשלוש שעות יש מאה ושמונים דקות.` (dead air 1.16 s, answer 6.9 s, backend [0.9, 1.0], screen ['thinking'], face [])
- `למה השמיים כחולים, במשפט אחד?` -> said `כי הא / ור הכחול מתפזר באטמוספירה יותר משאר הצבעים, וזה מה שהעיניים שלנו רואות.` (dead air 0.28 s, answer 5.9 s, backend [], screen [], face [])
- `איך אומרים תודה ביפנית?` -> said `אומרים / "אריגטו".` (dead air 0.23 s, answer 2.2 s, backend [], screen [], face [])
- `איך קוראים לאחותי ואיפה היא גרה?` -> said `קוראים לה נועה, והיא גרה בחיפה.` (dead air 0.45 s, answer 3.0 s, backend [], screen [], face [])
- `יופי. כמה זה מאה חלקי ארבע?` -> said `רגע. / זה עשרים וחמש.` (dead air 1.19 s, answer 5.5 s, backend [1.2, 1.0], screen ['thinking'], face [])
- `מה השעה עכשיו?` -> said `עכשיועכשיו שבע וחצי צהריים.` (dead air 0.29 s, answer 4.2 s, backend [], screen [], face [])
- `תן לי טיפ אחד לשינה טובה.` -> said `תנסה לכבות מסכים חצי שעה לפני השינה, זה פלאים למה שזה עושה לראש.` (dead air 0.41 s, answer 6.8 s, backend [], screen [], face [])
- `מה החדשות בתחום הטכנולוגיה היום?` -> said `שנייה, בוא נראה מה הכותרות היום בטכנולוגיה. / עוד רגע, אני עדיין בודק. הנה, בין הכותרות היום: בניו יורק מבקרים את מדיניות הממשל בתחום הבינה המלאכותית, וחברת Ten` (dead air 1.31 s, answer 25.9 s, backend [8.8], screen ['searching', 'thinking'], face [])
- `כמה רגליים יש לעכביש?` -> said `שמונה.` (dead air 0.51 s, answer 1.0 s, backend [], screen [], face [])
- `תודה, היה נחמד לדבר איתך.` -> said `גם לי / , תמיד כיף איתך.` (dead air 0.19 s, answer 2.0 s, backend [], screen [], face [])
- `לפני שאני הולך, מה אמרתי לך שאני מתכנן לסוף השבוע?` -> said `אמרת לי שאתה מתכנן טיול בצפון.` (dead air 0.68 s, answer 2.8 s, backend [], screen [], face [])
- `טוב, להתראות!` -> said `יאללה, להתראות.` (dead air 0.39 s, answer 1.4 s, backend [], screen [], face [])
- {"db": {"ai_requests": 10, "voice_seconds": 348, "reservations": 0}, "snapshot_requests": 10}

</details>

<details><summary>16. rapid_fire: PASS</summary>

- `כמה זה שתיים ועוד שתיים?` -> said `` (dead air 1.43 s, answer 0.9 s, backend [], screen [], face [])
- `מה בירת צרפת?` -> said `ארבע.` (dead air 0.52 s, answer 0.9 s, backend [], screen [], face [])
- `כמה ימים יש בשבוע?` -> said `פריז.` (dead air 0.5 s, answer 0.8 s, backend [], screen [], face [])
- `מה הצבע של בננה?` -> said `שבעה. / צהוב, אבל תלוי כמה היא בשלה.` (dead air 0.59 s, answer 2.3 s, backend [], screen [], face [])
- `כמה זה עשר פחות שלוש?` -> said `` (dead air 0.73 s, answer 0.8 s, backend [], screen [], face [])
- `מה ההפך של חם?` -> said `שבע.` (dead air 0.72 s, answer 0.9 s, backend [], screen [], face [])
- {"all_said": "ארבע. פריז. שבעה. צהוב, אבל תלוי כמה היא בשלה. שבע. קר."}

</details>

<details><summary>17. long_utterance: PASS</summary>

- `אני רוצה לספר לך משהו קטן שקרה לי. אתמול בבוקר קמתי מוקדם, ה` -> said `לאילת, לצלול ולהתחמם קצת בשמש.` (dead air 0.5 s, answer 3.4 s, backend [], screen [], face [])

</details>

<details><summary>18. english_switch: PASS</summary>

- `What is the capital city of Australia?` -> said `Canberra.` (dead air 0.52 s, answer 1.0 s, backend [], screen [], face [])
- `ומה עיר הבירה של קנדה?` -> said `אוטווה.` (dead air 0.64 s, answer 0.9 s, backend [], screen [], face [])

</details>

<details><summary>19. noise_robustness: PASS</summary>

- {"said": [], "heard": []}

</details>

<details><summary>20. idle_disconnect: PASS</summary>

- `שלום, אתה שומע אותי?` -> said `שומע מצוין. מה קורה?` (dead air 0.59 s, answer 2.4 s, backend [], screen [], face [])
- `יופי, תודה.` -> said `בכיף, תמיד כאן.` (dead air 0.61 s, answer 1.6 s, backend [], screen [], face [])

</details>

<details><summary>21. wake_word_x3: PASS</summary>

- {"mic": "cut@16000", "wake_logged": true, "score": 1.0, "connected": true, "connect_s": 3.4, "chimes": ["wake", "ready"], "answered": true, "correct": false, "turn": {"label": "after-wake", "asked": "כמה זה שלוש ועוד ארבע?", "mic": "24000", "q_s": 2.3, "dead_air_s": -2.36, "answer_s": 3.6, "speech_s": 2.7, "heard": "", "said": "היי, אני פה לידך. מה קורה?", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}
- {"mic": "cut@16000", "wake_logged": true, "score": 1.0, "connected": true, "connect_s": 3.1, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "מה בירת איטליה?", "mic": "24000", "q_s": 1.8, "dead_air_s": 0.57, "answer_s": 0.9, "speech_s": 0.4, "heard": "מה בירת איטליה", "said": "רומא.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}
- {"mic": "cut@16000", "wake_logged": true, "score": 1.0, "connected": true, "connect_s": 2.8, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "מה ההפך של גדול?", "mic": "24000", "q_s": 2.1, "dead_air_s": 0.49, "answer_s": 1.0, "speech_s": 0.5, "heard": "מה ההפך של גדול", "said": "קטן.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}

</details>

<details><summary>22. wake_false_positives: FAIL</summary>

- {"kind": "neg", "text": "hey Travis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": [0.16, 0.28]}
- {"kind": "neg", "text": "hey service", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Jarvis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.56, "near_miss": [0.28]}
- {"kind": "neg", "text": "hey Jarvie, no", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.6, "near_miss": [0.15]}
- {"kind": "neg", "text": "Hey Charles, come here", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": [0.15]}
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

- {"threshold": 0.15, "false_wakes": 2, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["Jarvis", "hey Jarvie, no"], "lost": 0}
- {"threshold": 0.35, "false_wakes": 2, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["Jarvis", "hey Jarvie, no"], "lost": 0}
- {"offline_peaks": [["hey Travis", 0.453], ["hey service", 0.098], ["Jarvis", 0.993], ["hey Jarvie, no", 0.987], ["Hey Charles, come here", 0.254], ["Harvest season is here", 0.001], ["Have a nice day", 0.0], ["Hey, how are you doing?", 0.002], ["A jar of peanut butter", 0.001], ["Let's watch Jurassic Park", 0.0], ["Hey there, good morning", 0.001], ["Service, please", 0.0], ["היי, מה נשמע?", 0.0], ["ג'רוויס", 0.0], ["תגיד לי מה השעה", 0.0], ["היי טרוויס", 0.0], ["איזה יום יפה היום", 0.0], ["תדליק את האור בסלון", 0.0], ["הרבה עבודה היום במשרד", 0.0], ["הבאתי לך חרוזים", 0.0], ["Hey Jarvis [Davi

</details>

<details><summary>24. software_mute: FAIL</summary>


</details>

<details><summary>25. budget_voice: PASS</summary>

- `ספר לי בבקשה בשלושה משפטים על ההיסטוריה של ירושלים.` -> said `ירושלים היא עיר עתיקה מאוד, בת אלפי שנים, שמלאה בסיפורים היסטוריים ודתיים ליהודים, נוצרים ומוסלמים. במשך הדורות היא נכבשה, נבנתה, נהרסה שוב ושוב, ועדיין נשארה מ` (dead air 0.43 s, answer 26.7 s, backend [], screen [], face [])
- `ומה עם תל אביב?` -> said `אה, תל אביב זה סיפור אחר לגמרי. היא צעירה בהרבה, התחילה ב-1909 כשכונה קטנה מחוץ ליפו, ובמהירות נהייתה עיר מרכזית עם קצב חיים מטורף. היום היא נחשבת בירת התרבות` (dead air 0.45 s, answer 13.6 s, backend [], screen [], face [])
- {"ended_after_s": 59.7}

</details>

<details><summary>26. budget_requests: PASS</summary>

- `מה מזג האוויר היום בירושלים?` -> said `רגע, בודק מה המצב שם. בירושלים היום מעונן חלקית, אחר הצהריים תהיה מעט התחממות ויותר עננות, עם רוח מתונה. צפויות בערך עשרים ואחת מעלות בשיא, וחמש-עשרה בלילה.` (dead air 0.9 s, answer 13.5 s, backend [2.2], screen ['searching', 'thinking'], face [])
- `מה החדשות הגדולות היום?` -> said `שנייה, בוא נראה מה הכותרות.` (dead air 1.48 s, answer 2.1 s, backend [5.3], screen ['searching', 'thinking'], face [])
- {"budget_logs": ["budget limit reached", "no budget for another request; mic input closed, ending after this answer", "daily AI request limit reached; ending live session"]}

</details>

<details><summary>27. face_protocol: PASS</summary>

- {"state_counts": {"thinking": 2, "listening": 10, "speaking": 18}, "first_states": ["speaking", "speaking", "speaking", "listening", "speaking", "listening", "speaking", "speaking", "listening", "speaking", "speaking", "speaking", "listening", "speaking", "listening", "speaking", "speaking", "speaking", "listening", "thinking", "speaking", "listening", "speaking", "speaking", "listening", "thinking", "speaking", "listening", "speaking", "listening"], "emotions": [["love", 3200]]}

</details>

<details><summary>28. start_stop_x5: PASS</summary>

- {"cycle": 1, "greeting": true, "answered": true, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 2, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 3, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 4, "greeting": true, "answered": true, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 5, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}

</details>

<details><summary>29. log_privacy: PASS</summary>

- {"heard_said_on_face": 7}
- `שלום, קוראים לי דניאל ואני גר ברחובות.` -> said `<4 chars> / <52 chars>` (dead air 0.32 s, answer 5.1 s, backend [], screen [], face [])
- `תזכיר לי איך קוראים לי?` -> said `<34 chars>` (dead air 0.74 s, answer 3.1 s, backend [], screen [], face [])

</details>

<details><summary>30. idle_soak_6min: PASS</summary>

- {"mic": "cut@16000", "wake_logged": true, "score": 1.0, "connected": true, "connect_s": 4.3, "chimes": ["wake", "ready"], "answered": true, "correct": true}
- `מה בירת ספרד?` -> said `מדריד, כמובן.` (dead air 0.54 s, answer 1.7 s, backend [], screen [], face [])

</details>

<details><summary>31. false_wake_closes: PASS</summary>

- {"closed_after_s": 14.8, "next": {"mic": "cut@16000", "wake_logged": true, "score": 1.0, "connected": true, "connect_s": 3.0, "chimes": ["wake", "ready"], "answered": true, "correct": true}}

</details>

<details><summary>32. privacy_strip_live: PASS</summary>

- `שלום, מה שלומך?` -> said `היי / , אחלה, כיף לשמוע אותך. מה שלומך?` (dead air 0.38 s, answer 3.6 s, backend [], screen [], face [])
- {"states": [["idle", "connected", "speaking"], ["gated", "connected", "speaking"], ["gated", "connected", "quiet"], ["listening", "sending", "quiet"], ["gated", "connected", "speaking"], ["gated", "connected", "quiet"], ["gated", "connected", "speaking"], ["gated", "connected", "quiet"], ["gated", "connected", "speaking"], ["gated", "connected", "quiet"], ["listening", "sending", "quiet"]]}

</details>

<details><summary>33. camera_off_honest: PASS</summary>

- `מה אתה רואה עכשיו במצלמה?` -> said `שנייה, אני בודק. המצלמה כבויה כרגע, אז אני לא יכול לראות מה יש מולה.` (dead air 1.65 s, answer 5.8 s, backend [0.8, 1.0], screen ['thinking', 'tool'], face [])

</details>

<details><summary>34. calendar_not_connected: PASS</summary>

- `תוסיף לי ביומן פגישה עם רופא השיניים מחר בעשר בבוקר` -> said `שנייה, אני מטפל בזה. גוגל עדיין לא מחובר, אז לא יכולתי להוסיף את הפגישה. תריץ במחשב jarvis google start, ואני אדריך אותך צעד אחר צעד בחיבור היומן.` (dead air 1.4 s, answer 11.9 s, backend [1.1, 0.9], screen ['thinking', 'tool'], face [])

</details>

<details><summary>35. barge_in: FAIL</summary>


</details>

<details><summary>36. timer_face_closed_live: PASS</summary>

- `תפעיל בבקשה טיימר לשלושים שניות` -> said `שניה, אני מפעיל לך את הטיימר. / מעולה, הפעלתי טיימר ל־30 שניות.` (dead air 0.74 s, answer 6.0 s, backend [1.2, 0.9, 0.7], screen ['thinking', 'tool'], face [])
- {"rang": [{"ts": "2026-10-20T20:42:11.874+00:00", "level": "INFO", "component": "timers", "code": "TIMER_RANG", "msg": "timer due", "cid": "b93e6de409", "session": "88b5ab0c", "v": 2, "kind": "timer", "ok": true, "timer_id": 1, "late_s": 1}], "said_after_due": "הטיימר צלצל.", "speech_after_due_s": 0.8, "session_open": true}

</details>

<details><summary>37. alarm_no_session_face_closed: PASS</summary>

- `תעיר אותי היום בשעה 23:44 בבקשה` -> said `בסדר, רק רגע. / קבעתי לך שעון מעורר להיום, עשרים באוקטובר, בעשרים ושלוש ארבעים וארבע.` (dead air 1.19 s, answer 10.0 s, backend [0.8, 1.5, 0.8], screen ['thinking', 'time', 'tool'], face [])
- {"tts": ["שעון מעורר: השכמה"], "rang": [{"ts": "2026-10-20T20:44:00.557+00:00", "level": "INFO", "component": "timers", "code": "TIMER_RANG", "msg": "timer due", "cid": "c40c2dde64", "session": "88b5ab0c", "v": 2, "kind": "alarm", "ok": true, "timer_id": 1, "late_s": 0}], "said": "בסדר, רק רגע. / קבעתי לך שעון מעורר להיום, עשרים באוקטובר, בעשרים ושלוש ארבעים וארבע."}

</details>

<details><summary>38. lists_clear_approval: PASS</summary>

- `תוסיף לרשימת הקניות חלב, לחם וביצים` -> said `כן, שנייה. / הוספתי לרשימת הקניות חלב, לחם וביצים.` (dead air 0.48 s, answer 7.1 s, backend [1.3, 1.0], screen ['thinking', 'tool'], face [])
- `מה יש ברשימת הקניות?` -> said `שנייה, אני בודק. / יש שם חלב, לחם וביצים.` (dead air 0.98 s, answer 5.9 s, backend [1.0, 1.3], screen ['thinking', 'tool'], face [])
- {"request": "תמחק את כל רשימת הקניות", "answer": "לא", "mic": "24000", "approval_asked": true, "voice_decided": null, "screen_clicked": false, "ask_after_s": 5.3, "shown": ["popup", "face", "voice"], "kind": "tool", "risk": "high", "jarves_asked_aloud_s": 4.9, "said_before_answer": "סגור, שנייה. / אני רוצה למחוק את כל הרשימה, לעשות את זה? תגיד כן או לא.", "popup_open_while_waiting": 1, "decided": true, "decision": "denied", "channel": "voice", "decided_after_answer_s": 3.07, "said": "סגור, שנייה. / אני רוצה למחוק את כל הרשימה, לעשות את זה? תגיד כן או לא. / אוקיי, אני לא מוחק. נשארה כמו שהיא.",
- {"request": "בעצם כן, תנקה את כל רשימת הקניות", "answer": "כן", "mic": "24000", "approval_asked": true, "voice_decided": null, "screen_clicked": false, "ask_after_s": 5.9, "shown": ["popup", "face", "voice"], "kind": "tool", "risk": "high", "jarves_asked_aloud_s": 4.0, "said_before_answer": "כבר, רגע. / לנקות את כל הרשימה? תגיד כן או לא.", "popup_open_while_waiting": 1, "decided": true, "decision": "approved", "channel": "voice", "decided_after_answer_s": 3.91, "said": "כבר, רגע. / לנקות את כל הרשימה? תגיד כן או לא.", "heard": "בעצם כן תנקה את כל רשימת הקניות כן", "tools": ["list_clear"], "too

</details>

<details><summary>39. helper_command_approval: FAIL</summary>

- {"request": "תריץ בבקשה במחשב את הפקודה whoami כדי לבדוק איזה משתמש מחובר", "answer": "כן", "mic": "24000", "approval_asked": false, "voice_decided": null, "screen_clicked": false, "ask_after_s": 55.5, "shown": null, "kind": null, "risk": null, "jarves_asked_aloud_s": 0, "said_before_answer": "אני בוד / ק מי מחובר כרגע.", "popup_open_while_waiting": 0, "decided": false, "decision": null, "channel": null, "decided_after_answer_s": null, "said": "אני בוד / ק מי מחובר כרגע.", "heard": "תריץ בבקשה במחשב את הפקודה כדי לבדוק איזה משתמש מח ובר כן", "tools": [], "tool_results": [], "face_confirm_reque
- {"audit": [], "said_all": "שלום! אני ג'ארוויס. איך אפשר לעזור? / אני בוד / ק מי מחובר כרגע."}

</details>

<details><summary>40. browser_local_page: PASS</summary>

- `תפתח בדפדפן את האתר לוקאל הוסט, פורט שמונה שמונה אחת אחת, ות` -> said `אוקי, שנייה, אני פותח ובודק. / מילת הקוד שמופיעה בדף היא אבטיח.` (dead air 0.82 s, answer 10.1 s, backend [1.3, 0.7, 0.8], screen ['thinking', 'tool'], face [])
- {"said_all": "שלום! אני ג'ארוויס. איך אפשר לעזור? / אוקי, שנייה, אני פותח ובודק. / מילת הקוד שמופיעה בדף היא אבטיח."}

</details>

<details><summary>41. briefing_without_google: PASS</summary>

- `תן לי בבקשה את תדריך הבוקר` -> said `אני על זה. רגע. / בוקר טוב! בתל אביב מעונן ו-19 מעלות עכשיו. היום בין שש עשרה לעשרים ושש מעלות, בלי גשם, ואין תזכורות או רשימות להיום. היומן והמייל לא זמינים כר` (dead air 1.5 s, answer 18.6 s, backend [0.9, 1.2], screen ['thinking', 'tool'], face [])

</details>

<details><summary>42. whatsapp_telegram_handoff: PASS</summary>

- {"request": "תכין הודעת וואטסאפ לדני, המספר שלו אפס חמש ארבע, אחת שתיים שלוש, ארבע חמש שש שבע, שאני מאחר בעשר דקות", "answer": "כן", "mic": "24000", "approval_asked": true, "voice_decided": null, "screen_clicked": false, "ask_after_s": 12.1, "shown": ["popup", "face", "voice"], "kind": "tool", "risk": "high", "jarves_asked_aloud_s": 5.2, "said_before_answer": "תן לי רגע להכין את זה. / אני רוצה לשלוח הודעת וואטסאפ לְדני. לעשות את זה? תגיד כן או לא, או אשר על המסך.", "popup_open_while_waiting": 1, "decided": true, "decision": "approved", "channel": "voice", "decided_after_answer_s": 3.27, "said"
- {"urls": ["https://wa.me/972541234567?text=%D7%90%D7%A0%D7%99%20%D7%9E%D7%97%D7%A8%20%D7%91%D7%A2%D7%A9%D7%A8%20%D7%93%D7%A7%D7%95%"]}
- `ותשלח לי גם הודעה בטלגרם שאני מאחר` -> said `רגע, אני מארגן את זה. טלגרם עוד לא מוגדר, אז כרגע אני לא יכול לשלוח שם. אם תרצה לחבר אותו, תריץ במחשב את Jarvis Telegram start ואני אדריך אותך.` (dead air 0.92 s, answer 12.2 s, backend [1.3, 1.0], screen ['thinking', 'tool'], face [])

</details>

<details><summary>43. profile_switch_approval: PASS</summary>

- {"request": "תעבור בבקשה לפרופיל של דנה", "answer": "לא", "mic": "24000", "approval_asked": true, "voice_decided": null, "screen_clicked": false, "ask_after_s": 7.0, "shown": ["popup", "face", "voice"], "kind": "tool", "risk": "medium", "jarves_asked_aloud_s": 3.2, "said_before_answer": "שנייה, אני מעביר לדנה. / סבבה, עברתי לפרופיל שלה, צריך את האישור שלך על המסך.", "popup_open_while_waiting": 1, "decided": true, "decision": "denied", "channel": "voice", "decided_after_answer_s": 3.67, "said": "שנייה, אני מעביר לדנה. / סבבה, עברתי לפרופיל שלה, צריך את האישור שלך על המסך.", "heard": "תעבור בבקש
- {"request": "בכל זאת, תעבור לפרופיל של דנה", "answer": "כן", "mic": "24000", "approval_asked": true, "voice_decided": false, "screen_clicked": true, "ask_after_s": 8.2, "shown": ["popup", "face", "voice"], "kind": "tool", "risk": "medium", "jarves_asked_aloud_s": 2.1, "said_before_answer": "אשר שם וננסה שוב. / אוקיי, בודק את זה עוד פעם. / עדיין מחכה לאישור שלך על המסך.", "popup_open_while_waiting": 1, "decided": true, "decision": "approved", "channel": "face", "decided_after_answer_s": 7.25, "said": "אשר שם וננסה שוב. / אוקיי, בודק את זה עוד פעם. / עדיין מחכה לאישור שלך על המסך.", "heard": "ב 

</details>

<details><summary>44. self_check_voice: PASS</summary>

- `תבדוק את עצמך` -> said `שנייה, בודק. / מצאתי חמש בעיות, אחת מהן חמורה: קריסה ברקע בזמן שיחה. הציון הכולל הוא שבעים ושש, רוצה לפתוח את עמוד הבעיות בלוח הבקרה כדי לראות פרטים?` (dead air 1.38 s, answer 14.8 s, backend [1.0, 1.0], screen ['thinking', 'tool'], face [])
- {"said_tail": "שלום! אני ג'ארוויס. איך אפשר לעזור? / שנייה, בודק. / מצאתי חמש בעיות, אחת מהן חמורה: קריסה ברקע בזמן שיחה. הציון הכולל הוא שבעים ושש, רוצה לפתוח את עמוד הבעיות בלוח הבקרה כדי לראות פרטים?"}

</details>

<details><summary>45. wake_variants: PASS</summary>

- {"variant": "Hey Jarvis/David", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": [0.38]}
- {"variant": "Hey Jarvis/David", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 1.0, "near": [0.41]}
- {"variant": "Hey Jarvis/David", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": [0.36]}
- {"variant": "Hey Jarvis/David", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.8, "near": [0.43]}
- {"variant": "Hey Jarvis/David", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 1.0, "near": [0.37]}
- {"variant": "Hey Jarvis/David", "level": "very quiet", "noise": true, "mic": "16000", "woke": false, "score": null, "near": [0.14]}
- {"variant": "Hey Jarvis/Zira", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.93, "near": [0.17]}
- {"variant": "Hey Jarvis/Zira", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.88, "near": [0.23]}
- {"variant": "Hey Jarvis/Zira", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": [0.34]}
- {"variant": "Hey Jarvis/Zira", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.98, "near": [0.31]}
- {"variant": "Hey Jarvis/Zira", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": [0.32]}
- {"variant": "Hey Jarvis/Zira", "level": "very quiet", "noise": true, "mic": "16000", "woke": false, "score": null, "near": [0.14]}
- {"variant": "Hi Jarvis/Mark", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.78, "near": [0.42]}
- {"variant": "Hi Jarvis/Mark", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.59, "near": [0.23]}
- {"variant": "Hi Jarvis/Mark", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.89, "near": [0.41]}
- {"variant": "Hi Jarvis/Mark", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.77, "near": [0.17]}
- {"variant": "Hi Jarvis/Mark", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.97, "near": [0.16]}
- {"variant": "Hi Jarvis/Mark", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.67, "near": [0.28]}
- {"variant": "Hi Jarvis/Zira", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.99, "near": [0.42]}
- {"variant": "Hi Jarvis/Zira", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.98, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.96, "near": []}
- {"variant": "ok jarvis/David", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.88, "near": [0.13]}
- {"variant": "ok jarvis/David", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.94, "near": [0.14]}
- {"variant": "ok jarvis/David", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.89, "near": [0.12]}
- {"variant": "ok jarvis/David", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.98, "near": [0.37]}
- {"variant": "ok jarvis/David", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.83, "near": []}
- {"variant": "ok jarvis/David", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.97, "near": [0.17]}
- {"variant": "Okay Jarvis/Zira", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.81, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.98, "near": [0.38]}
- {"variant": "Okay Jarvis/Zira", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.91, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.89, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.92, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.98, "near": [0.49]}
- {"variant": "היי ג'רוויס/Asaf", "level": "normal", "noise": false, "mic": "16000", "woke": false, "score": null, "near": []}
- {"variant": "היי ג'רוויס/Asaf", "level": "normal", "noise": true, "mic": "16000", "woke": false, "score": null, "near": []}
- {"variant": "היי ג'רוויס/Asaf", "level": "quiet", "noise": false, "mic": "16000", "woke": false, "score": null, "near": [0.29]}
- {"variant": "היי ג'רוויס/Asaf", "level": "quiet", "noise": true, "mic": "16000", "woke": false, "score": null, "near": []}

</details>

<details><summary>46. hebrew_names_pronunciation: PASS</summary>

- `תציג את עצמך במשפט אחד, ואז תקרא בקול את שלושת השמות האלה, כ` -> said `אני ג'ארוויס, חבר קטן על השולחן שלך שעוזר לך בכל מיני דברים. נועה, אביטל, צחי` (dead air 0.39 s, answer 7.4 s, backend [], screen [], face [])
- {"output_audio_s": 7.4, "transcript": "[gpt-4o-transcribe] אני ג'ארוויס, חבר קטן על השולחן שלך שעוזר לך בכל מיני דברים. נועה, אביטל, צחי."}

</details>

<details><summary>47. greeting_delay: PASS</summary>

- {"boot": 1, "first_sound_after_face_s": 0.51, "greeting_audio_s": 1.3, "said": "שלום! אני ג'ארוויס. איך אפשר לעזור?"}
- {"boot": 2, "first_sound_after_face_s": 0.52, "greeting_audio_s": 2.5, "said": "שלום! אני ג'ארוויס. איך אפשר לעזור?"}
- {"boot": 3, "first_sound_after_face_s": 0.5, "greeting_audio_s": 1.8, "said": "שלום! אני ג'ארוויס. איך אפשר לעזור?"}

</details>

<details><summary>48. redelegate_probe: PASS</summary>

- {"said_remember": "שנייה, אני בודק. עכשיו שתיים דקות לחצות. זכרתי שמספר הלוקר שלך הוא שמונה.", "said_forget": "רגע אחד, אני בודק את זה. אני רוצה למחוק משהו, נראה לך שזה מתאים? כן או לא? / מחקתי מהזיכרון את מספר הלוקר. אין זיכרונות שמורים כרגע.", "tools": ["remember", "forget", "recall"], "events": []}

</details>

<details><summary>49. told_not_asked: PASS</summary>

- `דרך אגב, לאחותי קוראים נועה, והיא גרה בחיפה.` -> said `אה, מגניב. נשמע שיש לך משפחה בעיר עם ים ממש יפה.` (dead air 0.39 s, answer 4.8 s, backend [], screen [], face [])
- `אני עובד היום מהבית.` -> said `יופי / , זה נשמע יום נוח יותר. אתה עובד בפיג'מה או בסגנון רשמי מהבית?` (dead air 0.38 s, answer 6.5 s, backend [], screen [], face [])
- `יש לי כלב בשם רקס, הוא בן שלוש.` -> said `רקס / ? שם קלאסי לכלב עם נוכחות. הוא יותר שובב או רגוע?` (dead air 0.43 s, answer 5.8 s, backend [], screen [], face [])

</details>

<details><summary>50. names_after_prefix: PASS</summary>

- {"said": "שלום לנועה, לאביטל ולצחי.", "audio_transcript": "[gpt-4o-transcribe] שלום לנועה, לאביטל ולצחי.", "avital": true, "tsachi": true, "swallowed": false, "text_bad": false}
- {"said": "שלום לנועה, לאביטל ולצחי.", "audio_transcript": "[gpt-4o-transcribe] שלום לנועה, לאביטל ולצחי.", "avital": true, "tsachi": true, "swallowed": false, "text_bad": false}

</details>

<details><summary>51. deleg_simple_lookup: FAIL</summary>

- `מה מזג האוויר היום בתל אביב?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>52. deleg_web_search: FAIL</summary>

- `מה שער הדולר מול השקל היום?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>53. deleg_two_overlapping: FAIL</summary>

- `וגם, מה השעה עכשיו בניו יורק?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>54. deleg_barge_in_cancel: FAIL</summary>


</details>

<details><summary>55. deleg_cap_mid_conversation: FAIL</summary>

- `מה מזג האוויר היום באילת?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])
- `ומה החדשות הגדולות היום בעולם?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])
- `ומה מזג האוויר בירושלים?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>56. deleg_plan_combined: FAIL</summary>

- `מה מזג האוויר היום בתל אביב?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>57. deleg_plan_limit: FAIL</summary>

- `מה החדשות היום?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>58. deleg_network_drop: FAIL</summary>

- `מה קרה היום בבורסה בתל אביב?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>59. deleg_slow_backend_30s: FAIL</summary>


</details>

<details><summary>60. deleg_tool_call: FAIL</summary>

- `תזכור בבקשה שמספר החניה שלי הוא שבע` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>61. deleg_hebrew_english: FAIL</summary>

- `מה בירת אוסטרליה ומה מזג האוויר שם היום?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])
- `What is the weather in London right now?` -> said `` (dead air None s, answer None s, backend [], screen [], face [])

</details>

<details><summary>62. deleg_cost_reconciliation: FAIL</summary>

- {"settled_usd": 0.0, "reserved_usd": 0, "open": 0}

</details>

<details><summary>105. popup_keyboard_rules: PASS</summary>

- {"medium-unarmed": null, "medium-armed": "approve", "high-armed-code": null, "high-armed-kp-enter": null, "high-escape": "deny", "medium-escape": "deny"}

</details>

<details><summary>106. pronunciation_default_empty: PASS</summary>

- {"files": {"workspace\\PRONUNCIATIONS.md": {}}}

</details>

<details><summary>107. wake_custom_model: PASS</summary>

- {"custom@0.95": {"by_phrase": {"Hey Jarvis": [10, 12], "Hi Jarvis": [12, 12], "OK Jarvis": [6, 6], "Okay Jarvis": [6, 6]}, "false": ["Jarvis"], "negatives": 20, "detector": "hey_jarvis_copy.npz@0.95 (ConfirmingDetector)", "ready": [["WAKE_LISTENING", "microphone active; waiting for Hey Jarvis"], ["WAKE_LISTENING", "microphone active; waiting for Hey Jarvis"]], "lost": 0}, "builtin@0.15": {"by_phrase": {"Hey Jarvis": [10, 12], "Hi Jarvis": [12, 12], "OK Jarvis": [6, 6], "Okay Jarvis": [6, 6]}, "false": ["Jarvis", "hey Jarvie, no"], "negatives": 20, "detector": "hey_jarvis@0.15 (ConfirmingDetect
- {"custom@0.95": [{"variant": "Hey Jarvis/David", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.98, "near": [0.39]}, {"variant": "Hey Jarvis/David", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.95, "near": [0.19, 0.94]}, {"variant": "Hey Jarvis/David", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.95, "near": [0.59, 0.9]}, {"variant": "Hey Jarvis/David", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.95, "near": [0.57, 0.57]}, {"variant": "Hey Jarvis/David", "level

</details>

## Suite runs

| Run | Scenarios | Live voice | Wall |
|---|---|---|---|
| 1020-225418 | 1-14 | 6.6 min | 6.9 min |
| 1020-230236 | 11-24 | 13.8 min | 22.5 min |
| 1020-232531 | 25-35 | 6.6 min | 15.2 min |
| 1020-234116 | 36-50 | 11.4 min | 17.5 min |
| 1021-000447 | 39,43,4,24,35,51-62 | 6.1 min | 8.7 min |
| 1021-001558 | 105,106,107 | 0.0 min | 8.6 min |
| 1021-002741 | 105,107 | 0.0 min | 8.8 min |
