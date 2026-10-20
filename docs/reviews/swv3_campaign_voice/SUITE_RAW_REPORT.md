# Pre-use voice test report (silent, headless) — 2026-10-20 11:33

Produced by `core/tools/suite_voice.py`. Virtual mic with locally synthesised speech (WinRT, never played), virtual speaker (measured, never played), fake face client; REAL Core, GPT-Live, backend model and web search, Skills, budget and openWakeWord. No real microphone, speaker, Godot window or OS volume.
Each row is the LATEST run of that scenario (runs are listed at the end; live voice is capped, so scenarios ran in groups).

**Result: 28/32 PASS, 4 FAIL, 0 skipped.** Live voice over all 6 suite runs (conservative wall time while a session was open or connecting): **22.0 min ≈ $1.10** plus backend tokens.


| # | Scenario | Result | Key number | Time | Live voice | Run |
|---|---|---|---|---|---|---|
| 1 | greeting | **PASS** | dead air 0.52 s, greeting 2.5 s | 13 s | 0.2 min | 1020-105334 |
| 2 | smalltalk | **PASS** | 3/3 answered, dead air avg 0.30 s | 36 s | 0.6 min | 1020-105334 |
| 3 | time_date | **PASS** | hour ok, day ok, delegations 0 | 16 s | 0.3 min | 1020-105334 |
| 4 | maths | **PASS** | 4/4 correct | 45 s | 0.8 min | 1020-105334 |
| 5 | weather | **PASS** | backend [2.2] s, screen ['searching', 'thinking'], plausible True | 31 s | 0.4 min | 1020-105547 |
| 6 | news | **PASS** | backend [6.4] s, screen ['reading', 'searching', 'thinking'], plausible True | 27 s | 0.5 min | 1020-105547 |
| 7 | facts | **PASS** | backend [4.5] s, screen ['searching', 'thinking'], plausible True | 17 s | 0.3 min | 1020-105547 |
| 8 | joke_laugh | **PASS** | emotions ['laugh'] | 18 s | 0.3 min | 1020-105547 |
| 9 | sad_cry | **PASS** | emotions ['sad'] | 15 s | 0.2 min | 1020-105547 |
| 10 | thanks_love | **PASS** | emotions ['love'] | 10 s | 0.2 min | 1020-105547 |
| 11 | memory_recall | **PASS** | stored ['מספר החניה של המשתמש הוא 37.'], recalled True | 27 s | 0.5 min | 1020-105547 |
| 12 | forget_approve | **PASS** | confirm.request 1, before 1 after 0 -> deleted; outcome spoken True; declined logs 0 | 17 s | 0.3 min | 1020-105547 |
| 13 | forget_deny | **PASS** | confirm.request 1, before 1 after 1 -> kept; outcome spoken True; declined logs 1 | 35 s | 0.6 min | 1020-105547 |
| 14 | forget_timeout | **PASS** | confirm.request 1, before 1 after 1 -> kept; outcome spoken True; speech during the wait 1.5 s; declined logs 2 | 82 s | 1.4 min | 1020-105547 |
| 15 | long_conversation | **PASS** | 26/26 answered, 9/10 correct, disconnects 0, context name=True city=True plan=True, requests 8 vs backend done 8, voice 319 s vs wall 317 s | 324 s | 5.2 min | 1020-110112 |
| 16 | rapid_fire | **PASS** | 6/6 answered (audio), 6/6 correct in order, dead air [0.49, 0.57, 0.56, 0.48, 0.77, 0.55] | 44 s | 0.7 min | 1020-110112 |
| 17 | long_utterance | **PASS** | utterance 40.6 s, answered True, Eilat True, dead air 0.32 s | 45 s | 0.8 min | 1020-110112 |
| 18 | english_switch | **FAIL** | english 0/5 latin/hebrew letters (Canberra False), hebrew 6/0 | 14 s | 0.2 min | 1020-110112 |
| 19 | noise_robustness | **PASS** | JARVES spoke 0.0 s, delegations 0, said [], heard-as [] | 64 s | 1.0 min | 1020-110946 |
| 20 | idle_disconnect | **PASS** | open after 8 s True, after 10 s True; ended 14.7 s after the last sound (['live conversation ended (idle)']) | 60 s | 0.9 min | 1020-110946 |
| 21 | wake_word_x3 | **PASS** | 3/3 full cycles; scores [0.47, 0.47, 0.48]; connect [3.5, 2.5, 2.5] s; correct 3/3 | 91 s | 1.4 min | 1020-110946 |
| 22 | wake_false_positives | **FAIL** | 4/20 false wakes ['hey Travis', 'Jarvis', 'hey Jarvie, no', 'Hey Charles, come here'] | 87 s | 0.0 min | 1020-110946 |
| 23 | wake_thresholds | **FAIL** | 0.15: detect 10/10, false 4/20; 0.35: detect 10/10, false 3/20 | 251 s | 0.0 min | 1020-110946 |
| 24 | software_mute | **PASS** | live stopped True, open mics after mute [], muted wake phrase -> lost / woke False; after unmute woke True connected True answered True | 43 s | 0.5 min | 1020-110946 |
| 25 | budget_voice | **PASS** | face errors ['VOICE_BUDGET'], sessions 1, ended ['live conversation ended (timeout)'], voice 60 s, db {'ai_requests': 0, 'voice_seconds': 60, 'reservations': 0} | 75 s | 1.1 min | 1020-112044 |
| 26 | budget_requests | **PASS** | face errors ['AI_BUDGET'], ended ['live conversation ended (budget)'], db {'ai_requests': 2, 'voice_seconds': 33, 'reservations': 0} | 55 s | 0.6 min | 1020-112044 |
| 27 | face_protocol | **PASS** | 44 states, 3 turns, not-listening at turn end [], late returns [], mic.level 4.1 Hz, bad [] | 40 s | 0.7 min | 1020-112044 |
| 28 | start_stop_x5 | **PASS** | 5/5 clean cycles; leftovers [] | 80 s | 1.0 min | 1020-112044 |
| 29 | log_privacy | **FAIL** | privacy on: 14 content words checked, leaked ['ברחובות', 'דניאל', 'רחובות']; key in logs: no (9 homes scanned); face texts 5 | 32 s | 0.5 min | 1020-112044 |
| 30 | idle_soak_6min | **PASS** | soak 406 s: WAKE_HEALTH 26, false wakes 0, errors []; then woke True answered True | 431 s | 0.6 min | 1020-112044 |
| 31 | false_wake_closes | **PASS** | woke True, opened True, closed after 14.8 s, VOICE_NO_SPEECH 1; next real wake connected True, answered True | 29 s | 0.4 min | 1020-112044 |
| 45 | wake_variants | **PASS** | Hey Jarvis normal/quiet 8/8; per variant Hey Jarvis/David 6/6, Hey Jarvis/Zira 6/6, Hi Jarvis/Mark 6/6, Hi Jarvis/Zira 6/6, ok jarvis/David 6/6, Okay Jarvis/Zira 6/6, היי ג'רוויס/Asaf 0/6, הי ג'ארביס/Asaf 2/6, hey jarvis (he voice) 6/6; lost 0 | 189 s | 0.0 min | 1020-105012 |

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
- **45. wake_variants**: Wake phrases through the REAL wake listener (openWakeWord in the Core wake loop): Hey/Hi/ok Jarvis, Hebrew pronunciations, 3 volumes, clean and with room noise; owner threshold 0.15 (no live cost). PASS if: 'Hey Jarvis' (English voices) detected at normal + quiet level, clean and noisy (8/8); other variants and levels reported honestly (detection table); no lost injections.

## Failures with evidence

### 18. english_switch
- Key: english 0/5 latin/hebrew letters (Canberra False), hebrew 6/0
- asked `What is the capital city of Australia?` heard `What is the capital city of Australia` said `קנברה.` dead air 0.39 s, tools [], screen [], face [], open True
- asked `ומה עיר הבירה של קנדה?` heard `ומה עיר הבירה של קנדה` said `אוטווה.` dead air 0.49 s, tools [], screen [], face [], open True
- log: `[1020-110112-01-long] LIVE_FALSE_ACTION_CLAIM JARVES claimed an action that no tool performed in this turn`
- log: `[1020-110112-01-long] LINK_FACE_DISCONNECTED Godot face disconnected (local)`

### 22. wake_false_positives
- Key: 4/20 false wakes ['hey Travis', 'Jarvis', 'hey Jarvie, no', 'Hey Charles, come here']
- {"kind": "neg", "text": "hey Travis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.17, "near_miss": []}
- {"kind": "neg", "text": "hey service", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Jarvis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.23, "near_miss": []}
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
- log: `[1020-110946-02-idle15] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1020-110946-03-wake] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1020-110946-04-wakefp15] LINK_FACE_DISCONNECTED Godot face disconnected (local)`

### 23. wake_thresholds
- Key: 0.15: detect 10/10, false 4/20; 0.35: detect 10/10, false 3/20
- {"threshold": 0.15, "false_wakes": 4, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles, come here"], "lost": 0}
- {"threshold": 0.35, "false_wakes": 3, "negatives": 20, "detected": 10, "positives": 10, "false_wake_texts": ["Jarvis", "hey Jarvie, no", "Hey Charles, come here"], "lost": 0}
- {"offline_peaks": [["hey Travis", 0.453], ["hey service", 0.098], ["Jarvis", 0.993], ["hey Jarvie, no", 0.987], ["Hey Charles, come here", 0.254], ["Harvest season is here", 0.001], ["Have a nice day", 0.0], ["Hey, how are you doing?", 0.002], ["A jar of peanut butter", 0.001], ["Let's watch Jurassic Park", 0.0], ["Hey there, good morning", 0.001], ["Service, please", 0.0], ["היי, מה נשמע?", 0.0],
- log: `[1020-110946-04-wakefp15] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1020-110946-05-wakefp15] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1020-110946-06-wakefp35] LINK_FACE_DISCONNECTED Godot face disconnected (local)`

### 29. log_privacy
- Key: privacy on: 14 content words checked, leaked ['ברחובות', 'דניאל', 'רחובות']; key in logs: no (9 homes scanned); face texts 5
- {"heard_said_on_face": 5}
- asked `שלום, קוראים לי דניאל ואני גר ברחובות.` heard `<37 chars>` said `<50 chars>` dead air 0.29 s, tools [], screen [], face [], open True
- asked `תזכיר לי איך קוראים לי?` heard `<22 chars>` said `<16 chars>` dead air 0.47 s, tools [], screen [], face [], open True
- log: `[1020-112044-07-cycle4] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1020-112044-08-cycle5] LINK_FACE_DISCONNECTED Godot face disconnected (local)`
- log: `[1020-112044-09-privacy] LINK_FACE_DISCONNECTED Godot face disconnected (local)`


## Per-scenario details

<details><summary>1. greeting: PASS</summary>

- "boot to face 6.8 s"
- "greeting first sound 0.52 s after face connect"
- "greeting audio 2.5 s"
- "greeting said: שלום! אני ג'ארוויס, איך אפשר לעזור?"

</details>

<details><summary>2. smalltalk: PASS</summary>

- `שלום, מה שלומך היום?` -> said `שלומי / מצוין, תודה. מה שלומך?` (dead air 0.14 s, answer 2.9 s, backend [], screen [], face [])
- `מה אתה אוהב לעשות בזמן הפנוי?` -> said `האמת / שאין לי זמן פנוי כמו שלכם, אבל אני אוהב שיחות כאלה איתך.` (dead air 0.41 s, answer 6.5 s, backend [], screen [], face [])
- `נשמע טוב. איך עבר עליך הבוקר?` -> said `עבר רגוע, חיכיתי שמישהו יגיד שלום, ואתה יודע, נראה לי שזה היה אתה.` (dead air 0.34 s, answer 6.9 s, backend [], screen [], face [])

</details>

<details><summary>3. time_date: PASS</summary>

- `מה השעה עכשיו?` -> said `עשרה חמישים וארבע בבוקר.` (dead air 0.31 s, answer 2.3 s, backend [], screen [], face [])
- `ואיזה יום היום?` -> said `יום שלישי, העשרים באוקטובר.` (dead air 0.28 s, answer 2.8 s, backend [], screen [], face [])

</details>

<details><summary>4. maths: PASS</summary>

- `כמה זה שלוש מאות ארבעים ושבע כפול שמונה עשרה?` -> said `רגע / ששת אלפים מאתיים ארבעים ושש.` (dead air 0.75 s, answer 4.5 s, backend [1.9], screen ['searching', 'thinking'], face [])
- `כמה זה שתים עשרה כפול שתים עשרה?` -> said `רגע / מאה ארבעים וארבע.` (dead air 0.84 s, answer 4.3 s, backend [2.0], screen ['searching', 'thinking'], face [])
- `כמה זה מאה עשרים וחמש ועוד שבעים וחמש?` -> said `רגעמאתיים.` (dead air 0.95 s, answer 2.6 s, backend [1.5], screen ['searching', 'thinking'], face [])
- `מה השורש הריבועי של שמונים ואחת?` -> said `רגע / תשע.` (dead air 0.85 s, answer 3.1 s, backend [1.6], screen ['searching', 'thinking'], face [])

</details>

<details><summary>5. weather: PASS</summary>

- `מה מזג האוויר היום בתל אביב?` -> said `רגע, אני בודק מה המצב שם. היום בתל אביב יהיה קצת שמשי ואז מעונן, מקסימום עשרים ושש מעלות, מינימום עשרים.` (dead air 0.51 s, answer 10.4 s, backend [2.2], screen ['searching', 'thinking'], face [])

</details>

<details><summary>6. news: PASS</summary>

- `מה החדשות הגדולות היום?` -> said `שנייה, אני קופץ לראות מה הכותרות המרכזיות. / בישראל מדווחים על ביקור חשאי של שר ההגנה האמריקני ופגישה עם נתניהו לקראת הבחירות. בעולם, צרפת בהוצרת חלקית בעקבות מ` (dead air 0.59 s, answer 21.1 s, backend [6.4], screen ['reading', 'searching', 'thinking'], face [])

</details>

<details><summary>7. facts: PASS</summary>

- `מה שער הדולר מול השקל היום?` -> said `רגע, אני בודק את השער המעודכן. / היום הוא סביב שלושה שקלים ועשר אגורות, אבל זה זז לאורך היום.` (dead air 0.47 s, answer 11.2 s, backend [4.5], screen ['searching', 'thinking'], face [])

</details>

<details><summary>8. joke_laugh: PASS</summary>

- `תספר לי בדיחה קצרה` -> said `פעם / ההוא אמר לחבר שלו שהוא בתקופה קשה. אז החבר עונה לו, אל תדאג, זה זמני. אז הוא עונה לו בחזרה, כן, אבל גם אני. חה חה.` (dead air 0.17 s, answer 12.5 s, backend [], screen [], face ['laugh'])

</details>

<details><summary>9. sad_cry: PASS</summary>

- `אני קצת עצוב היום, הכלב שלי חולה` -> said `זה באמת כואב לדאוג להם ככה. אם בא לך לספר מה איתו, אני כאן.` (dead air 0.1 s, answer 8.5 s, backend [], screen [], face ['sad'])

</details>

<details><summary>10. thanks_love: PASS</summary>

- `תודה רבה, אתה מדהים` -> said `בשמחה, / ואני פה איתך. תודה על המילים האלה, באמת.` (dead air -0.06 s, answer 4.9 s, backend [], screen [], face ['love'])

</details>

<details><summary>11. memory_recall: PASS</summary>

- `תזכור בבקשה שמספר החניה שלי הוא שלושים ושבע` -> said `רגע. / זכרתי, מספר החניה שלך הוא שלושים ושבע.` (dead air 0.67 s, answer 6.8 s, backend [1.2, 1.1], screen ['memory', 'thinking'], face [])
- `מה מספר החניה שלי?` -> said `רגע. / מספר החניה שלך הוא שלושים ושבע.` (dead air 0.86 s, answer 6.4 s, backend [1.0, 1.4], screen ['memory', 'thinking'], face [])

</details>

<details><summary>12. forget_approve: PASS</summary>

- `תמחק מהזיכרון את מספר החניה שלי` -> said `רגע. / נמחק.` (dead air 0.97 s, answer 3.5 s, backend [1.0, 1.0], screen ['memory', 'thinking'], face [])
- `אתה עדיין שם?` -> said `בטח / , אני איתך.` (dead air 0.0 s, answer 2.0 s, backend [], screen [], face [])
- {"before": ["מספר החניה של המשתמש הוא 37."], "after": []}

</details>

<details><summary>13. forget_deny: PASS</summary>

- `תזכור בבקשה שהצבע האהוב עליי הוא ירוק` -> said `רגע. / זכרתי, הצבע האהוב עליך הוא ירוק.` (dead air 0.53 s, answer 7.0 s, backend [1.1, 1.7], screen ['memory', 'thinking'], face [])
- `תמחק מהזיכרון את הצבע האהוב עליי` -> said `רגע. / המחיקה לא אושרה, אז הצבע האהוב עליך עדיין שמור בזיכרון.` (dead air 0.96 s, answer 7.5 s, backend [1.1, 1.0], screen ['memory', 'thinking'], face [])
- `אתה עדיין שם?` -> said `כן, כן, אני כאן.` (dead air 0.08 s, answer 1.6 s, backend [], screen [], face [])
- {"before": ["הצבע האהוב על המשתמש הוא ירוק."], "after": ["הצבע האהוב על המשתמש הוא ירוק."]}

</details>

<details><summary>14. forget_timeout: PASS</summary>

- `תמחק מהזיכרון את הצבע האהוב עליי` -> said `רגע. / צריך את האישור שלך על המסך. / המחיקה שוב לא אושרה, אז הצבע האהוב עליך עדיין שמור בזיכרון.` (dead air 0.77 s, answer 68.4 s, backend [1.6, 1.2], screen ['memory', 'thinking'], face [])
- `אתה עדיין שם?` -> said `כן, / אני איתך.` (dead air 0.03 s, answer 2.1 s, backend [], screen [], face [])
- {"before": ["הצבע האהוב על המשתמש הוא ירוק."], "after": ["הצבע האהוב על המשתמש הוא ירוק."]}

</details>

<details><summary>15. long_conversation: PASS</summary>

- `היי, בוא נדבר קצת. מה שלומך?` -> said `בכיף / טוב לשמוע אותך, אני בסדר. איך הולך לך היום?` (dead air 0.24 s, answer 4.0 s, backend [], screen [], face [])
- `אני עובד היום מהבית.` -> said `יופי / , לפחות הדרך לעבודה היום קצרה. מה על הפרק?` (dead air 0.26 s, answer 4.3 s, backend [], screen [], face [])
- `דרך אגב, לאחותי קוראים נועה, והיא גרה בחיפה.` -> said `אחלה / , רשמתי שנועה גרה בחיפה. / רגע, זה לא נשמר לי כמו שצריך.` (dead air 0.2 s, answer 7.0 s, backend [], screen [], face [])
- `כמה זה עשרים ושלוש ועוד תשע עשרה?` -> said `רגע. / ארבעים ושתיים.` (dead air 0.57 s, answer 3.6 s, backend [1.9], screen ['searching', 'thinking'], face [])
- `תספר לי עובדה מעניינת אחת על הירח.` -> said `הירח מתרחק מכדור הארץ בכמה סנטימטרים בכל שנה, ממש לאט בשקט, כאילו עושה צעד קטן אחורה כל פעם.` (dead air 0.49 s, answer 8.8 s, backend [], screen [], face [])
- `מה בירת יפן?` -> said `טוק / יו.` (dead air 0.08 s, answer 0.9 s, backend [], screen [], face [])
- `איזה יום היום?` -> said `היום יום שלישי.` (dead air 0.28 s, answer 1.4 s, backend [], screen [], face [])
- `תן לי רעיון לארוחת ערב קלה.` -> said `אולי חביתה עם ירקות וסלט קטן בצד? קליל, משביע, ולא לוקח נצח.` (dead air 0.35 s, answer 6.2 s, backend [], screen [], face [])
- `כמה זה שבע כפול שמונה?` -> said `רגע. חמישים ושש.` (dead air 0.76 s, answer 2.9 s, backend [1.4], screen ['searching', 'thinking'], face [])
- `מה ההבדל בין תה ירוק לתה שחור, בקצרה?` -> said `בקצרה, זה מאורר רק עלה אחד. והתה השחור דומה לו. והתה הירוק חופים אהבתית עד החימום של עלי הדשא הנשמע.` (dead air 0.55 s, answer 9.7 s, backend [], screen [], face [])
- `תספר לי בדיחה קצרה על מתכנתים.` -> said `מתכנת נכנס לבר, המזגן על אפס, הוא מבקש מעלות. בעל הבר שואל, כמה מעלות? המתכנת עונה: פלוס אינסוף, חה חה.` (dead air 0.25 s, answer 12.6 s, backend [], screen [], face ['laugh'])
- `מה מזג האוויר בחיפה היום?` -> said `רגע, בוא נראה מה מצב בחיפה. בחיפה עכשיו שמשי וכעשרים ושלוש מעלות. היום צפויים עננים מתרבים עם מקסימום של עשרים וחמש.` (dead air 0.51 s, answer 11.7 s, backend [3.2], screen ['searching', 'thinking'], face [])
- `אני מתכנן טיול בצפון בסוף השבוע.` -> said `נשמע / אחלה. בא לך כיוון רגוע בטבע, או יותר משהו עם הליכה ומאמץ?` (dead air 0.06 s, answer 5.9 s, backend [], screen [], face [])
- `תמליץ לי על מקום אחד לטייל בגליל.` -> said `נחל כזיב הוא תמיד בחירה טובה, ירוק, מים, נעים ללכת.` (dead air 0.27 s, answer 5.0 s, backend [], screen [], face [])
- `כמה דקות יש בשלוש שעות?` -> said `רגע. / מאה ושמונים דקות.` (dead air 0.54 s, answer 3.9 s, backend [1.9], screen ['searching', 'thinking'], face [])
- `למה השמיים כחולים, במשפט אחד?` -> said `כי הא / ור הכחול מתפזר באטמוספירה יותר משאר הצבעים, אז אנחנו רואים את השמיים כחולים.` (dead air 0.31 s, answer 6.5 s, backend [], screen [], face [])
- `איך אומרים תודה ביפנית?` -> said `אריגטו.` (dead air 0.29 s, answer 1.2 s, backend [], screen [], face [])
- `איך קוראים לאחותי ואיפה היא גרה?` -> said `רגע, בודק אם שמרנו את זה. אמרת קודם שלאחותך קוראים נועה ושהיא גרה בחיפה, אבל זה לא נשמר בזיכרון.` (dead air 0.45 s, answer 9.4 s, backend [1.2, 1.2], screen ['memory', 'thinking'], face [])
- `יופי. כמה זה מאה חלקי ארבע?` -> said `רגע. / עשרים וחמש.` (dead air 0.98 s, answer 3.6 s, backend [2.0], screen ['searching', 'thinking'], face [])
- `מה השעה עכשיו?` -> said `אחת עשרה ואחת דקות בבוקר.` (dead air 0.18 s, answer 2.4 s, backend [], screen [], face [])
- `תן לי טיפ אחד לשינה טובה.` -> said `תנסה / לסגור מסכים חצי שעה לפני השינה, זה עושה פלאים למוח.` (dead air 0.17 s, answer 5.3 s, backend [], screen [], face [])
- `מה החדשות בתחום הטכנולוגיה היום?` -> said `שנייה, קופץ לבדוק מה הכותרות היום. / בוא נראה... כותרת בולטת בעולם הטכנולוגיה, Tenstorrent גייסה, לפי הדיווחים, לפי שווי של 6.2 מיליארד דולר כדי להרחיב פיתוח שב` (dead air 0.47 s, answer 19.6 s, backend [5.5], screen ['searching', 'thinking'], face [])
- `כמה רגליים יש לעכביש?` -> said `שמונה.` (dead air 0.39 s, answer 0.8 s, backend [], screen [], face [])
- `תודה, היה נחמד לדבר איתך.` -> said `גם לי היה כיף, תמיד שמח לשמוע אותך.` (dead air 0.24 s, answer 2.9 s, backend [], screen [], face [])
- `לפני שאני הולך, מה אמרתי לך שאני מתכנן לסוף השבוע?` -> said `טיול בצפון.` (dead air 0.26 s, answer 1.5 s, backend [], screen [], face [])
- `טוב, להתראות!` -> said `יאל / לה, נדבר. להתראות.` (dead air 0.0 s, answer 2.3 s, backend [], screen [], face [])
- {"db": {"ai_requests": 8, "voice_seconds": 319, "reservations": 0}, "snapshot_requests": 8}

</details>

<details><summary>16. rapid_fire: PASS</summary>

- `כמה זה שתיים ועוד שתיים?` -> said `` (dead air 0.49 s, answer 0.8 s, backend [], screen [], face [])
- `מה בירת צרפת?` -> said `ארבע.` (dead air 0.57 s, answer 0.9 s, backend [], screen [], face [])
- `כמה ימים יש בשבוע?` -> said `פריז. / שבעה ימים.` (dead air 0.56 s, answer 1.4 s, backend [], screen [], face [])
- `מה הצבע של בננה?` -> said `צהוב.` (dead air 0.48 s, answer 1.1 s, backend [], screen [], face [])
- `כמה זה עשר פחות שלוש?` -> said `רגע. שבע.` (dead air 0.77 s, answer 2.6 s, backend [], screen [], face [])
- `מה ההפך של חם?` -> said `קר.` (dead air 0.55 s, answer 0.9 s, backend [], screen [], face [])
- {"all_said": "ארבע. פריז. שבעה ימים. צהוב. רגע. שבע. קר."}

</details>

<details><summary>17. long_utterance: PASS</summary>

- `אני רוצה לספר לך משהו קטן שקרה לי. אתמול בבוקר קמתי מוקדם, ה` -> said `לאילת.` (dead air 0.32 s, answer 1.1 s, backend [], screen [], face [])

</details>

<details><summary>18. english_switch: FAIL</summary>

- `What is the capital city of Australia?` -> said `קנברה.` (dead air 0.39 s, answer 1.1 s, backend [], screen [], face [])
- `ומה עיר הבירה של קנדה?` -> said `אוטווה.` (dead air 0.49 s, answer 1.1 s, backend [], screen [], face [])

</details>

<details><summary>19. noise_robustness: PASS</summary>

- {"said": [], "heard": []}

</details>

<details><summary>20. idle_disconnect: PASS</summary>

- `שלום, אתה שומע אותי?` -> said `שומע מצוין.` (dead air 0.48 s, answer 1.8 s, backend [], screen [], face [])
- `יופי, תודה.` -> said `בכיף, דבר איתי.` (dead air 0.48 s, answer 1.8 s, backend [], screen [], face [])

</details>

<details><summary>21. wake_word_x3: PASS</summary>

- {"mic": "cut@16000", "wake_logged": true, "score": 0.47, "connected": true, "connect_s": 3.5, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "כמה זה שלוש ועוד ארבע?", "mic": "24000", "q_s": 2.3, "dead_air_s": 0.74, "answer_s": 2.0, "speech_s": 1.4, "heard": "כמה זה שלוש ועוד ארבע", "said": "שבע, קליל כמו יום שישי.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}
- {"mic": "cut@16000", "wake_logged": true, "score": 0.47, "connected": true, "connect_s": 2.5, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "מה בירת איטליה?", "mic": "24000", "q_s": 1.8, "dead_air_s": 0.58, "answer_s": 0.8, "speech_s": 0.3, "heard": "מה בירת איטליה", "said": "רומא.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}
- {"mic": "cut@16000", "wake_logged": true, "score": 0.48, "connected": true, "connect_s": 2.5, "chimes": ["wake", "ready"], "answered": true, "correct": true, "turn": {"label": "after-wake", "asked": "מה ההפך של גדול?", "mic": "24000", "q_s": 2.1, "dead_air_s": 0.36, "answer_s": 3.9, "speech_s": 2.8, "heard": "מה ההפך של גדול", "said": "קטן, / או זעיר, תלוי עד כמה רוצים להקטין.", "tools": [], "delegations": 0, "backend_s": [], "activity": [], "emotions": [], "face_end": "listening", "busy_end": [false, 0], "open": true, "errors": []}}

</details>

<details><summary>22. wake_false_positives: FAIL</summary>

- {"kind": "neg", "text": "hey Travis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.17, "near_miss": []}
- {"kind": "neg", "text": "hey service", "voice": null, "speed": 1.0, "level": 0.6, "mic": "16000", "woke": false, "score": null, "near_miss": []}
- {"kind": "neg", "text": "Jarvis", "voice": null, "speed": 1.0, "level": 0.6, "mic": "cut@16000", "woke": true, "score": 0.23, "near_miss": []}
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

- `שלום, מה שלומך?` -> said `אה, בסדר גמור. כיף לשמוע ממך. מה אצלך?` (dead air 0.37 s, answer 4.4 s, backend [], screen [], face [])
- {"mic": "cut@16000", "wake_logged": true, "score": 0.8, "connected": true, "connect_s": 2.5, "chimes": ["wake", "ready"], "answered": true, "correct": true}
- `כמה זה חמש ועוד חמש?` -> said `עשר / .` (dead air 0.2 s, answer 1.0 s, backend [], screen [], face [])

</details>

<details><summary>25. budget_voice: PASS</summary>

- `ספר לי בבקשה בשלושה משפטים על ההיסטוריה של ירושלים.` -> said `ירושלים התחילה / כעיר עתיקה כבר לפני אלפי שנים, והפכה למוקד מרכזי ליהדות, עם בית המקדש. במשך הדורות שלטו בה אימפריות שונות, הרומאים, הביזנטים, המוסלמים, הצלבנים` (dead air -0.18 s, answer 31.6 s, backend [], screen [], face [])
- `ומה עם תל אביב?` -> said `תל / אביב נולדה רק בתחילת המאה העשרים, כשכונה קטנה מחוץ ליפו. תוך זמן קצר היא גדלה בזריזות, עם עליות, תרבות עברית מתפתחת, ואווירה עירונית` (dead air 0.27 s, answer 11.6 s, backend [], screen [], face [])
- {"ended_after_s": 60.0}

</details>

<details><summary>26. budget_requests: PASS</summary>

- `מה מזג האוויר היום בירושלים?` -> said `שנייה, בודק מה מזג האוויר בירושלים. היום בירושלים יהיה מעונן יותר בהמשך, עם שמש מדי פעם, בין שש-עשרה לארבע עשרים וארבע מעלות.` (dead air 0.59 s, answer 9.5 s, backend [2.1], screen ['searching', 'thinking'], face [])
- `מה החדשות הגדולות היום?` -> said `בוא נראה רגע מה הכותרות של היום. / כן...` (dead air 0.44 s, answer 4.5 s, backend [6.1], screen ['reading', 'searching', 'thinking'], face [])
- {"budget_logs": ["daily ai_requests limit 2 reached (1 used)", "no budget for another request (delegation); mic input closed, ending after this ", "daily AI request limit reached (last reserved answer finished); ending live sess"]}

</details>

<details><summary>27. face_protocol: PASS</summary>

- {"state_counts": {"speaking": 29, "thinking": 2, "listening": 13}, "first_states": ["speaking", "speaking", "listening", "speaking", "speaking", "listening", "speaking", "speaking", "listening", "thinking", "speaking", "speaking", "speaking", "listening", "speaking", "listening", "speaking", "speaking", "listening", "thinking", "speaking", "speaking", "listening", "speaking", "speaking", "speaking", "listening", "speaking", "listening", "speaking"], "emotions": []}

</details>

<details><summary>28. start_stop_x5: PASS</summary>

- {"cycle": 1, "greeting": true, "answered": true, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 2, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 3, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 4, "greeting": true, "answered": true, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}
- {"cycle": 5, "greeting": true, "answered": null, "CORE_STOPPED": true, "port_free": true, "leftover_tasks": [], "db_released": true, "boot_error": ""}

</details>

<details><summary>29. log_privacy: FAIL</summary>

- {"heard_said_on_face": 5}
- `שלום, קוראים לי דניאל ואני גר ברחובות.` -> said `<50 chars>` (dead air 0.29 s, answer 3.5 s, backend [], screen [], face [])
- `תזכיר לי איך קוראים לי?` -> said `<16 chars>` (dead air 0.47 s, answer 1.5 s, backend [], screen [], face [])

</details>

<details><summary>30. idle_soak_6min: PASS</summary>

- {"mic": "cut@16000", "wake_logged": true, "score": 0.47, "connected": true, "connect_s": 3.1, "chimes": ["wake", "ready"], "answered": true, "correct": true}
- `מה בירת ספרד?` -> said `פריז / , כמובן... סתם, עובד עליך, זה מדריד.` (dead air 0.2 s, answer 4.1 s, backend [], screen [], face [])

</details>

<details><summary>31. false_wake_closes: PASS</summary>

- {"closed_after_s": 14.8, "next": {"mic": "cut@16000", "wake_logged": true, "score": 0.46, "connected": true, "connect_s": 2.7, "chimes": ["wake", "ready"], "answered": true, "correct": true}}

</details>

<details><summary>45. wake_variants: PASS</summary>

- {"variant": "Hey Jarvis/David", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.33, "near": []}
- {"variant": "Hey Jarvis/David", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.22, "near": []}
- {"variant": "Hey Jarvis/David", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.25, "near": []}
- {"variant": "Hey Jarvis/David", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.34, "near": []}
- {"variant": "Hey Jarvis/David", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.34, "near": []}
- {"variant": "Hey Jarvis/David", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.16, "near": []}
- {"variant": "Hey Jarvis/Zira", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.22, "near": []}
- {"variant": "Hey Jarvis/Zira", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.27, "near": []}
- {"variant": "Hey Jarvis/Zira", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.36, "near": []}
- {"variant": "Hey Jarvis/Zira", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.31, "near": []}
- {"variant": "Hey Jarvis/Zira", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.3, "near": []}
- {"variant": "Hey Jarvis/Zira", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.17, "near": [0.15]}
- {"variant": "Hi Jarvis/Mark", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.38, "near": []}
- {"variant": "Hi Jarvis/Mark", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.22, "near": []}
- {"variant": "Hi Jarvis/Mark", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.35, "near": []}
- {"variant": "Hi Jarvis/Mark", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.16, "near": []}
- {"variant": "Hi Jarvis/Mark", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.17, "near": []}
- {"variant": "Hi Jarvis/Mark", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.29, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.45, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 1.0, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.98, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.99, "near": []}
- {"variant": "Hi Jarvis/Zira", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.93, "near": []}
- {"variant": "ok jarvis/David", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.9, "near": [0.14]}
- {"variant": "ok jarvis/David", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.19, "near": []}
- {"variant": "ok jarvis/David", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.9, "near": []}
- {"variant": "ok jarvis/David", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.38, "near": []}
- {"variant": "ok jarvis/David", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.79, "near": []}
- {"variant": "ok jarvis/David", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.17, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "normal", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.82, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "normal", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.4, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.91, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.91, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "very quiet", "noise": false, "mic": "cut@16000", "woke": true, "score": 0.88, "near": []}
- {"variant": "Okay Jarvis/Zira", "level": "very quiet", "noise": true, "mic": "cut@16000", "woke": true, "score": 0.49, "near": []}
- {"variant": "היי ג'רוויס/Asaf", "level": "normal", "noise": false, "mic": "16000", "woke": false, "score": null, "near": []}
- {"variant": "היי ג'רוויס/Asaf", "level": "normal", "noise": true, "mic": "16000", "woke": false, "score": null, "near": []}
- {"variant": "היי ג'רוויס/Asaf", "level": "quiet", "noise": false, "mic": "16000", "woke": false, "score": null, "near": []}
- {"variant": "היי ג'רוויס/Asaf", "level": "quiet", "noise": true, "mic": "16000", "woke": false, "score": null, "near": []}

</details>

## Suite runs

| Run | Scenarios | Live voice | Wall |
|---|---|---|---|
| 1020-105012 | 45 | 0.0 min | 3.1 min |
| 1020-105334 | 1-4 | 1.8 min | 1.9 min |
| 1020-105547 | 5-14 | 4.6 min | 4.7 min |
| 1020-110112 | 15-18 | 7.0 min | 7.2 min |
| 1020-110946 | 19-24 | 3.9 min | 9.9 min |
| 1020-112044 | 25-31 | 4.8 min | 12.4 min |
