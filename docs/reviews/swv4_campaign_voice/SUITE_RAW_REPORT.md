# Pre-use voice test report (silent, headless) — 2026-10-20 23:40

Produced by `core/tools/suite_voice.py`. Virtual mic with locally synthesised speech (WinRT, never played), virtual speaker (measured, never played), fake face client; REAL Core, GPT-Live, backend model and web search, Skills, budget and openWakeWord. No real microphone, speaker, Godot window or OS volume.
Each row is the LATEST run of that scenario (runs are listed at the end; live voice is capped, so scenarios ran in groups).

**Result: 33/35 PASS, 2 FAIL, 0 skipped.** Live voice over all 3 suite runs (conservative wall time while a session was open or connecting): **27.1 min ≈ $1.35** plus backend tokens.


| # | Scenario | Result | Key number | Time | Live voice | Run |
|---|---|---|---|---|---|---|
| 1 | greeting | **PASS** | dead air 0.52 s, greeting 2.2 s | 19 s | 0.2 min | 1020-225418 |
| 2 | smalltalk | **PASS** | 3/3 answered, dead air avg 0.45 s | 43 s | 0.7 min | 1020-225418 |
| 3 | time_date | **PASS** | hour ok, day ok, delegations 0 | 15 s | 0.2 min | 1020-225418 |
| 4 | maths | **PASS** | 3/4 correct | 57 s | 0.9 min | 1020-225418 |
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
| 24 | software_mute | **PASS** | live stopped True, open mics after mute [], muted wake phrase -> lost / woke False; after unmute woke True connected True answered True | 53 s | 0.6 min | 1020-230236 |
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
| 35 | barge_in | **PASS** | long answer playing True; old speech stopped 0.6 s after the interruption; answered 4 True; barge_ins 1 | 41 s | 0.6 min | 1020-232531 |

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

## Failures with evidence

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

<details><summary>4. maths: PASS</summary>

- `כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה?` -> said `רגע, מחשב את זה. / שלוש מאות ארבעים ושבע כפול שמונה זה אלפיים שבע מאות שבעים ושש.` (dead air 0.72 s, answer 8.3 s, backend [1.2, 1.3], screen ['thinking'], face [])
- `כמה זה שתים עשרה כפול שתים עשרה?` -> said `רגע, בודק. / שתים עשרה כפול שתים עשרה שווה מאה ארבעים וארבע.` (dead air 0.89 s, answer 7.3 s, backend [0.7, 0.9], screen ['thinking'], face [])
- `כמה זה מאה עשרים וחמש ועוד שבעים וחמש?` -> said `סכום. שנייה. / זה יוצא מאתיים.` (dead air 1.11 s, answer 5.2 s, backend [1.1, 1.5], screen ['thinking'], face [])
- `מה השורש הריבועי של שמונים ואחת?` -> said `רגע, בודק. / הוא תשע.` (dead air 1.29 s, answer 4.8 s, backend [1.2, 1.1], screen ['thinking'], face [])

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

<details><summary>24. software_mute: PASS</summary>

- `שלום, מה שלומך?` -> said `אח / לה, תודה ששאלת. מה המצב אצלך היום?` (dead air 0.4 s, answer 3.8 s, backend [], screen [], face [])
- {"mic": "cut@16000", "wake_logged": true, "score": 1.0, "connected": true, "connect_s": 3.5, "chimes": ["wake", "ready"], "answered": true, "correct": true}
- `כמה זה חמש ועוד חמש?` -> said `רגע. / עשר.` (dead air 1.75 s, answer 3.7 s, backend [1.1, 1.1], screen ['thinking'], face [])

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

<details><summary>35. barge_in: PASS</summary>

- `רגע, עצור. כמה זה שתיים ועוד שתיים?` -> said `יאללה, בוא נצלול רגע, כי יש לירושלים המון שכבות. / רגע. פא... פא... פא... / ארבע.` (dead air -4.09 s, answer 10.1 s, backend [1.0, 1.1], screen ['thinking'], face [])
- {"segments_after_interrupt": [[-0.4, 0.6], [4.9, 5.4], [6.5, 6.7], [7.0, 7.2], [7.9, 8.0], [9.6, 9.9]]}

</details>

## Suite runs

| Run | Scenarios | Live voice | Wall |
|---|---|---|---|
| 1020-225418 | 1-14 | 6.6 min | 6.9 min |
| 1020-230236 | 11-24 | 13.8 min | 22.5 min |
| 1020-232531 | 25-35 | 6.6 min | 15.2 min |
