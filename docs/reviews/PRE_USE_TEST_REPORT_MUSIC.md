# Pre-use test report: music (Pear Desktop / YouTube Music)

Generated 2026-10-19 19:00 by `core/tools/suite_music.py` against the REAL Pear Desktop on this PC, silently (Pear volume 0, watchdog cap 3, restored at the end; virtual mic/speaker for voice).

## Issue #16 re-run (19 Oct 2026 evening, WP-E): read this first

The table below holds the LATEST run of each row: the 18:56-19:00 run, made while **Pear itself had stalled**.
It is not the best evidence for the code. All runs on this build (real Pear 3.12, volume 0 with the watchdog, Pear
restored to slider 70 and paused after each run, 0 volume violations):

| Run (19 Oct) | Build | skill S01-S13 | errors/robust | Notes |
|---|---|---|---|---|
| run 1 | #16 fixes | 12/13 | 7/7 | S07/S08 PASS (not re-verified before). S11 7/10: "נועה קירל" artist-script bug, "ABBA Dancing Queen" parsed to nothing, one Pear timeout. Fixed. |
| run 2 | + search/artist fixes | 11/13 | (not run) | S11 9/10 (ABBA and Noa Kirel now play). S07: Pear applied a resume ~9 s late. Fixed (press /play again). |
| run 3 | + fail-fast, final look | S07/S10/S11 only: 1/3 | (not run) | S11 9/10. Two plays answered MUSIC_TIMEOUT: a Pear request timed out (5 s) during the play. Fixed (check anyway). |
| run 4 | same | 10/13 | (not run) | "Daft Punk Get Lucky" played the stalled Radio Edit (Rgrt_8mXrK8) and it was reported playing. Every request after it timed out: S02, S11 2x, S07 stop. |
| run 5, 18:56 (table) | + timeout check | 4/13 | 7/7 | **Pear stalled**: GET /queue took 14.9 s (1 MB answer), a video sat at elapsed 0 ignoring pause, and the suite could not pause it. K01 (5 min soak) stopped after 5 s on a timeout. Afterwards a skip + pause left Pear paused at slider 70, unmuted. |

What this shows:
- **P1 (stalled playback reported as playing) is confirmed on real Pear and is a limit of Pear's API.** Pear has no
  live progress signal. `elapsedSeconds` (REST) and `position` (`/api/v1/ws`, token as `?token=`) only change on
  state events. In a muted probe a song really played 27 s -> 36 s while both read 27 the whole time. So a stalled
  video and a playing one look the same. Results now say `playback: "player_reports_playing"` with a note that
  JARVES cannot hear the speakers. An ignored pause (the stall's visible symptom) gets an actionable message:
  "restart Pear Desktop".
- **Volume scale measured** (paused): slider -> state 0->0 (muted), 10->2, 20->5, 30->8, 40->13, 50->20, 60->29,
  70->40, 80->55, 90->74, 100->100. The 10·sqrt(state) estimate is off by 5-7 points mid-range, so JARVES uses this
  table (`_VOLUME_CURVE`). POST /volume 0 mutes and any other POST unmutes. toggle-mute keeps the slider.
- **Still open on real Pear:** a clean full run after a Pear restart (S01-S13 + K01 soak), and the voice rows
  V01-V10 on this build (not re-run: Pear had stalled and free RAM was ~850 MB of 7.5 GB). The new `music_mute` /
  `music_unmute` / relative volume were exercised on real Pear only through scripted probes (paused): volume
  table, mute/unmute, restore. They were not driven by voice.

**Result of the latest rows: 23/31 scenarios PASS.**

| # | Group | Scenario | Result | Key number | Time | Run at |
|---|---|---|---|---|---|---|
| S01 | skill | play Hebrew artist: שלמה ארצי | **FAIL** | confirm 21.0 s | 21.0 s | 10-19 18:56 |
| S02 | skill | play Hebrew artist: עומר אדם | PASS | confirm 10.0 s | 10.0 s | 10-19 18:56 |
| S03 | skill | play English artist+song: Queen Bohemian Rhapsody | PASS | confirm 5.7 s | 5.7 s | 10-19 18:57 |
| S04 | skill | play English artist: Beatles | PASS | confirm 5.3 s | 5.3 s | 10-19 18:57 |
| S05 | skill | play a mood: משהו רגוע | PASS | confirm 18.8 s | 18.8 s | 10-19 18:57 |
| S06 | skill | play a different song while one is playing (queue-index fix) | PASS | confirm 5.5 s | 14.7 s | 10-19 18:57 |
| S07 | skill | pause, resume, next, stop vs Pear state | **FAIL** | pause 0.0s, resume 0.0s, next FAIL, stop FAIL | 26.4 s | 10-19 18:58 |
| S08 | skill | status tool text matches Pear (playing and paused) | **FAIL** | mismatch | 9.2 s | 10-19 18:58 |
| S09 | skill | volume 0 and 2 on real Pear; cap enforced (fake player, max 70) | PASS | cap 70 held (7/7) | 3.2 s | 10-19 18:58 |
| S10 | skill | rapid play -> next -> pause -> resume -> next (no waits) | **FAIL** | sequence 33.0 s, in sync=True | 39.1 s | 10-19 18:59 |
| S11 | skill | 10 different searches back to back (confirm latency each) | **FAIL** | 0/10 ok; median 5.0 s, max 5.0 s | 50.2 s | 10-19 18:59 |
| S12 | skill | nonsense query -> honest 'not found' | **FAIL** | MUSIC_TIMEOUT in 5.0 s | 5.0 s | 10-19 18:59 |
| S13 | skill | pause / next / resume / status when nothing is playing -> graceful | **FAIL** | misleading reply | 12.3 s | 10-19 19:00 |
| E01 | errors | Pear unreachable (port 26599) -> MUSIC_PLAYER_OFFLINE, Hebrew | PASS | 3/3 offline + health unavailable | 8.3 s | 10-19 19:00 |
| E02 | errors | unpaired id at the real port -> MUSIC_NOT_PAIRED, no /auth (no dialog) | PASS | /auth calls 0, 5 requests | 0.1 s | 10-19 19:00 |
| E03 | errors | 401 -> silent re-pair (mock), no loop; 403 on /auth -> PAIR_DENIED | PASS | re-pair 1 auth; no loop; deny honoured | 0.5 s | 10-19 19:00 |
| R01 | robust | search edge cases: empty, no videoIds, malformed JSON, 500, timeout, 204 | PASS | 8/8 sensible | 0.1 s | 10-19 19:00 |
| R02 | robust | now_playing edge cases: 204, malformed, missing fields, Unicode/RTL titles | PASS | no crash, RTL kept | 0.9 s | 10-19 19:00 |
| R03 | robust | video result vs its 'song' counterpart (other videoId / title suffix) | PASS | 4/4 honest | 2.4 s | 10-19 19:00 |
| R04 | robust | queue endpoint broken / slow -> play still works or fails honestly | PASS | no crash | 6.2 s | 10-19 19:00 |
| K01 | soak | long soak: alternating play/next/pause/resume/status at volume 0 | **FAIL** | CRASH MusicError | 5.1 s | 10-19 19:00 |
| V01 | voice | voice: 'נגן את שלמה ארצי' -> Pear plays the artist | PASS | Pear 2.6 s, dead air 1.13 s | 15.2 s | 10-19 13:33 |
| V02 | voice | voice: 'עצור' -> paused | PASS | Pear 1.66 s, dead air 1.22 s | 15.2 s | 10-19 13:33 |
| V03 | voice | voice: 'תמשיך' -> resumed | PASS | Pear 1.45 s, dead air 1.21 s | 15.2 s | 10-19 13:33 |
| V04 | voice | voice: 'השיר הבא' -> track changed | PASS | Pear 1.74 s, dead air 1.18 s | 15.2 s | 10-19 13:33 |
| V05 | voice | voice: 'מה מתנגן עכשיו?' -> names the real title | PASS | named title=True, dead air 0.83 s | 15.2 s | 10-19 13:33 |
| V06 | voice | voice: 'נגן משהו של Queen' | PASS | Pear 3.49 s, dead air 1.14 s | 15.2 s | 10-19 13:33 |
| V07 | voice | voice: 'תנגן שיר שמח' (vague) | PASS | Pear 3.31 s, dead air 1.07 s | 15.2 s | 10-19 13:33 |
| V08 | voice | voice: web question while music plays (mixed) | PASS | answered, music undisturbed=True, dead air 0.77 s | 15.2 s | 10-19 13:33 |
| V09 | voice | voice: music right after the web question | PASS | Pear 4.21 s, dead air 1.67 s | 15.2 s | 10-19 13:33 |
| V10 | voice | voice: screen activity 'music' shows the song | PASS | 4/4 play turns showed the song | 15.2 s | 10-19 13:33 |

## Confirm latency (search -> Pear reports the song playing), S11

| Query | Latency |
|---|---|
| Adele Hello | 5.0 s |
| Coldplay Yellow | 5.0 s |
| אריק איינשטיין אוהב להיות בבית | 5.0 s |
| Michael Jackson Billie Jean | 5.0 s |
| שלום חנוך | 5.0 s |
| Daft Punk Get Lucky | 5.0 s |
| עידן רייכל | 5.0 s |
| Eminem Lose Yourself | 5.0 s |
| נועה קירל | 5.0 s |
| ABBA Dancing Queen | 5.0 s |

## Voice turns: dead air and time until Pear actually played

| Turn | Dead air (question end -> first sound) | Pear state reached after |
|---|---|---|
| V01 | 1.13 s | 2.6 s |
| V02 | 1.22 s | 1.66 s |
| V03 | 1.21 s | 1.45 s |
| V04 | 1.18 s | 1.74 s |
| V05 | 0.83 s | None s |
| V06 | 1.14 s | 3.49 s |
| V07 | 1.07 s | 3.31 s |
| V08 | 0.77 s | None s |
| V09 | 1.67 s | 4.21 s |

Live GPT-Live usage: 4 sessions, 10.4 wall-minutes in total (upper bound of billed voice time; about $0.52 voice + backend tokens). Runs: 3.89 min / 9 turns, 0.99 min / 2 turns, 3.04 min / 9 turns, 2.48 min / 10 turns

## Findings (19 Oct 2026)

Product bugs found and fixed in `core/src/jarves/skills/music.py` (regressions in `core/tests/test_music_qa.py`):

1. **Gibberish played a random video and claimed success** (S12). `asdkjhqwe zzxxcc` played a 2.7K-view user upload; JARVES said 'מנגן: ...'. That video never actually started (elapsed stayed 0) and ignored pause. Fix: `search_is_relevant()`, which requires at least one query word in the text YouTube shows. Otherwise the answer is MUSIC_NOT_FOUND.
2. **'next' stayed on the same song** (V04 live, S07, S10). Every repeated request inserted another copy after the current item, so the real queue held back-to-back copies (19 adjacent duplicates in 77 items) and Pear's next landed on the same song. Fix: `play_track` reuses a copy that is current or next, and `next()` skips copies of the current song. `music_next` now waits up to 7 s, presses next once more after 4 s, and names the new song. If the song still has not changed, it answers MUSIC_NOT_CONFIRMED honestly.
3. **'next' / 'resume' with nothing playing claimed an action** (S13): they answered 'השיר הבא.' or 'ממשיך לנגן.'. They now answer 'שום דבר לא מתנגן כרגע.' (MUSIC_NOTHING_PLAYING).
4. **Crashes on odd Pear answers** (R01, R02, R04): malformed or empty JSON from search, queue or song, or non-numeric durations, gave TOOL_CRASH. They now give MUSIC_PLAYER_ERROR or 'not found' with a Hebrew message. An unreadable queue falls back to the old path.
5. **False 'not confirmed' for the song counterpart** (R02, R03): titles differing only by bidi marks, '(Official Video)', '[Official Music Video]' or '(2013 Remaster)' did not match. `_same_track` now compares the cleaned base title; a different song still never matches.
6. **Self-inflicted and caught by the soak:** the first version of the relevance check (fix 1) was quadratic. It took 8 s per real 250 KB search answer (50 s under tracemalloc), so confirms grew from ~3.5 s to 10-15 s and every soak play timed out. Rewritten iteratively, it takes 0.007 s; a performance regression test was added. The 'Pear search is slow' theory was wrong: raw /search answered in 0.5 s.
7. `music_next` timeout 10 s -> 15 s (it now confirms the change, up to 7 s).
8. **Every play kept the PREVIOUS request playing** (one run: S11 0/10, V01 and V06 live). On a ~100-item queue the list shifted by one between our read and the jump. Fix: `_select()` checks that the selected item is the song and re-selects it only when the song has really moved. Re-jumping merely because GET /queue still showed the old 'selected' froze playback (seen once: elapsed stuck, pause ignored). After the fix: S11 10/10.
9. Tried and REVERTED: requiring elapsed > 0 for the confirm. Pear's elapsedSeconds stays 0 while really playing and is refreshed only on events: 0 for 9 s, then 9 right after a pause. Requiring it gave 0/10 false 'not confirmed'. A regression test keeps it out.
10. `play_track` no longer reads the whole queue first; the 'already playing' check uses /song (the queue answer reached 2.4 MB and 2.2 s per read).

Remaining, not fixed (needs a user decision):

- **Pear slows down as its queue grows.** Each new song is inserted, and nothing is ever removed. After ~250 test plays the queue held ~170 items (2.4 MB per read). Pear then answered slowly (one 5 s HTTP timeout), 'next' took >7 s, and once it ignored jumps for ~40 s (S04, S07, S10, S11 3/10 in the last run). JARVES stayed honest ('לא מדווח שהשיר מתנגן'); it never claimed a song that was not playing. With the 32-item queue at the start of the night: S11 10/10 at 2-4.6 s. Options: (a) JARVES removes already-played items (DELETE /queue/{i}) to keep the queue under ~50, or (b) it plays via a fresh queue. Both change the user's queue, so they were not tried tonight. Restarting Pear also clears it.
- **Final 15-min soak (K01) on that 187-207-item queue:** 197 ops; 38 honest failures (mostly 'next' not reflected within 7.4 s; 4 of 20 plays not confirmed) and 8 pause/resume desyncs. No crash, no false success, flat memory and asyncio tasks, queue +1 per play. The earlier soak on a shorter queue (pre-fix build) had only play timeouts, caused by bug 6. A soak on a freshly started Pear is still owed.
- The voice rows V02-V05 and V07-V10 come from runs on earlier builds of this branch; V06 and V10 failed on the builds with bugs 8 and 9. The live budget (10 min) was used up before a final all-voice rerun. The causes are fixed and verified at Skill level (S06, S11).

Environment facts (Pear Desktop 3.12, precise-volume plugin on):

- **Volume scale:** POST /volume sets the SLIDER; GET reports the effective level on a lower, roughly quadratic scale. POST 8 reads back 1; POST 2 or 3 read back 0 (unmuted); POST 0 reads back 0 and muted. Unmuting via toggle-mute restored a hidden level 5. The suite only ever POSTed 0-3 during tests (the cap). So 'restore to 8' POSTs 8, which reads back about 1: the user may find the music quieter than before. Where the slider sat when the night began cannot be recovered from the reading of 8 without raising the volume, which the night rules forbid.
- The queue grows by one item per new song (insert after current); the soak table shows the growth.
- An unplayable user video (seen for the gibberish query) reports isPaused=false with elapsed stuck at 0, and it ignores pause. The suite's safety step skips it with 'next' at volume 0. JARVES can no longer reach such a video for gibberish, but a stuck video reached by a real query would still be reported as playing (open item).

## Safety

- Final Pear state: `{"pause_workaround": ["skipped stuck xUNqsfFUwhY", "skipped stuck xUNqsfFUwhY"], "pause_error": "MusicError('Pear Desktop did not answer in time')", "volume": "left at 0 because Pear could not be paused", "max_volume_seen_during_tests": 0, "volume_violations": 0}`

## Evidence per scenario

### S01 play Hebrew artist: שלמה ארצי - FAIL
Dancing Queen – ABBA
- tool: ok=False code=MUSIC_NOT_CONFIRMED msg='ניסיתי 2 גרסאות של השיר, ואף אחת לא התחילה לנגן בזמן.' (21.0 s)
- pear /song: title='Dancing Queen' artist='ABBA' id=YkLLcIKhJ64 paused=True

### S02 play Hebrew artist: עומר אדם - PASS
שני משוגעים – עומר אדם
- tool: ok=True code=ok msg='מנגן: שני משוגעים – עומר אדם' (10.0 s)
- pear /song: title='שני משוגעים' artist='עומר אדם' id=fRFEiZhAVO0 paused=False

### S03 play English artist+song: Queen Bohemian Rhapsody - PASS
Bohemian Rhapsody – Queen
- tool: ok=True code=ok msg='מנגן: Bohemian Rhapsody – Queen' (5.7 s)
- pear /song: title='Bohemian Rhapsody' artist='Queen' id=BSTsnWoslP4 paused=False

### S04 play English artist: Beatles - PASS
Here Comes The Sun (Remastered 2009) – The Beatles
- tool: ok=True code=ok msg='מנגן: Here Comes The Sun (Remastered 2009) – The Beatles' (5.3 s)
- pear /song: title='Here Comes The Sun (Remastered 2009)' artist='The Beatles' id=xUNqsfFUwhY paused=False

### S05 play a mood: משהו רגוע - PASS
משהו בשביל מישהו – חוה אלברשטיין
- tool: ok=True code=ok msg='מנגן: משהו בשביל מישהו – חוה אלברשטיין' (18.8 s)
- pear /song: title='משהו בשביל מישהו' artist='חוה אלברשטיין' id=HDwmZqpjry0 paused=False

### S06 play a different song while one is playing (queue-index fix) - PASS
old next=BSTsnWoslP4, now=BciS5krYL80 'Hotel California'
- tool: ok=True code=ok msg='מנגן: Bohemian Rhapsody – Queen' (9.0 s)
- pear /song: title='Bohemian Rhapsody' artist='Queen' id=BSTsnWoslP4 paused=False
- tool: ok=True code=ok msg='מנגן: Hotel California – Eagles' (5.5 s)
- pear /song: title='Hotel California' artist='Eagles' id=BciS5krYL80 paused=False
- queue len 58, selected 4, old next BSTsnWoslP4

### S07 pause, resume, next, stop vs Pear state - FAIL
- music_pause: ok=True msg='השהיתי.' tool 0.33s; pear reached state after 0.01 s: 'Hotel California' paused=True
- music_resume: ok=True msg='ממשיך לנגן: Hotel California – Eagles' tool 0.34s; pear reached state after 0.00 s: 'Hotel California' paused=False
- music_next: ok=False msg='ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.' tool 9.41s; pear reached state after never s: 'Hotel California' paused=False
- music_stop: ok=False msg='ביקשתי מהנגן להשהות, אבל הוא עדיין מדווח שהוא מנגן. אם זה נמשך, כדאי לסגור ולפתוח מחדש את Pear Desktop.' tool 4.15s; pear reached state after never s: 'Hotel California' paused=False

### S08 status tool text matches Pear (playing and paused) - FAIL
- status='מתנגן: Hotel California – Eagles. עוצמה 0 מתוך 100, מושתק.' (0.01s) vs pear title='Hotel California' paused=False
- status='מתנגן: Hotel California – Eagles. עוצמה 0 מתוך 100, מושתק.' (0.02s) vs pear title='Hotel California' paused=False

### S09 volume 0 and 2 on real Pear; cap enforced (fake player, max 70) - PASS
- real: asked 2: 'העוצמה עכשיו 2 מתוך 100.'; pear reports {'state': 0, 'isMuted': True} after 3.1s
- real: asked 0: 'העוצמה עכשיו 0 מתוך 100.'; pear reports {'state': 0, 'isMuted': True} after 0.0s
- real, max_volume=3: asked 50 -> 'העוצמה עכשיו 3 מתוך 100. (התקרה שהוגדרה היא 3)', data {'volume': 3, 'previous': 0, 'max_volume': 3, 'changed': True, 'capped': True, 'muted': True}
- fake: asked 100: code=ok msg='העוצמה עכשיו 70 מתוך 100. (התקרה שהוגדרה היא 70)' player volume 70 OK
- fake: asked 71: code=ok msg='העוצמה כבר 70 מתוך 100, וזו התקרה שהוגדרה (70); לא הגברתי.' player volume 70 OK
- fake: asked 70: code=ok msg='העוצמה עכשיו 70 מתוך 100.' player volume 70 OK
- fake: asked 50: code=ok msg='העוצמה עכשיו 50 מתוך 100.' player volume 50 OK
- fake: asked 0: code=ok msg='העוצמה עכשיו 0 מתוך 100.' player volume 0 OK
- fake: asked 150: code=TOOL_BAD_ARGS msg='invalid arguments for music_volume' player volume 0 OK
- fake: asked -1: code=TOOL_BAD_ARGS msg='invalid arguments for music_volume' player volume 0 OK

### S10 rapid play -> next -> pause -> resume -> next (no waits) - FAIL
- play: ok=False 'הנגן איטי מדי כרגע: ביקשתי לנגן, אבל לא קיבלתי ממנו אישור בזמן.' 18.8s
- music_next: ok=False 'נגן המוזיקה לא עונה.' 5.03s -> pear 'Here Comes The Sun (Remastered 2009)' paused=False
- music_pause: ok=False 'ביקשתי מהנגן להשהות, אבל הוא עדיין מדווח שהוא מנגן. אם זה נמשך, כדאי לסגור ולפתוח מחדש את Pear Desktop.' 4.08s -> pear 'Here Comes The Sun (Remastered 2009)' paused=False
- music_resume: ok=True 'ממשיך לנגן: Here Comes The Sun (Remastered 2009) – The Beatles' 0.02s -> pear 'Here Comes The Sun (Remastered 2009)' paused=False
- music_next: ok=False 'נגן המוזיקה לא עונה.' 5.01s -> pear 'Here Comes The Sun (Remastered 2009)' paused=False
- settled: pear 'Here Comes The Sun (Remastered 2009)' paused=False; status 'מתנגן: Here Comes The Sun (Remastered 2009) – The Beatles. עוצמה 0 מתוך 100, מושתק.'

### S11 10 different searches back to back (confirm latency each) - FAIL
latencies: 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0
- 'Adele Hello': FAIL rel=NO 5.03 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'Coldplay Yellow': FAIL rel=NO 5.02 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'אריק איינשטיין אוהב להיות בבית': FAIL rel=NO 5.01 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'Michael Jackson Billie Jean': FAIL rel=NO 5.02 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'שלום חנוך': FAIL rel=NO 5.01 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'Daft Punk Get Lucky': FAIL rel=NO 5.02 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'עידן רייכל': FAIL rel=NO 5.01 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'Eminem Lose Yourself': FAIL rel=NO 5.01 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'נועה קירל': FAIL rel=NO 5.02 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)
- 'ABBA Dancing Queen': FAIL rel=NO 5.02 s -> 'Here Comes The Sun (Remastered 2009)' / 'The Beatles' | tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' (5.0 s)

### S12 nonsense query -> honest 'not found' - FAIL
נגן המוזיקה לא עונה.
- tool: ok=False code=MUSIC_TIMEOUT msg='נגן המוזיקה לא עונה.' data={'action': 'check that the app is not frozen'} (5.0s)
- pear before 'Here Comes The Sun (Remastered 2009)' -> after 'Here Comes The Sun (Remastered 2009)'

### S13 pause / next / resume / status when nothing is playing -> graceful - FAIL
- fake (nothing playing) music_pause: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- fake (nothing playing) music_next: ok=False code=MUSIC_NOTHING_PLAYING 'שום דבר לא מתנגן כרגע. אפשר לבקש ממני שיר.' OK
- fake (nothing playing) music_resume: ok=False code=MUSIC_NOTHING_PLAYING 'שום דבר לא מתנגן כרגע. אפשר לבקש ממני שיר.' OK
- fake (nothing playing) music_stop: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- fake (nothing playing) music_status: ok=True code=ok 'שום דבר לא מתנגן כרגע. עוצמה 50 מתוך 100.' OK
- real pause-while-paused: ok=False 'ביקשתי מהנגן להשהות, אבל הוא עדיין מדווח שהוא מנגן. אם זה נמשך, כדאי לסגור ולפתוח מחדש את Pear Desktop.'; pear paused=False

### E01 Pear unreachable (port 26599) -> MUSIC_PLAYER_OFFLINE, Hebrew - PASS
- music_play: code=MUSIC_PLAYER_OFFLINE 'נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.' 2.04s
- music_pause: code=MUSIC_PLAYER_OFFLINE 'נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.' 2.03s
- music_status: code=MUSIC_PLAYER_OFFLINE 'נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.' 2.05s
- health: ('unavailable', 'Pear Desktop is not reachable; start Pear Desktop and enable Plugins > API Server (host 127.0.0.1, port 26538)')

### E02 unpaired id at the real port -> MUSIC_NOT_PAIRED, no /auth (no dialog) - PASS
- music_play: code=MUSIC_NOT_PAIRED 'עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.'
- music_next: code=MUSIC_NOT_PAIRED 'עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.'
- music_status: code=MUSIC_NOT_PAIRED 'עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.'
- health: ('degraded', 'pear running but not paired; run `jarves music pair` once and click Allow in Pear Desktop'); requests: [('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401)]

### E03 401 -> silent re-pair (mock), no loop; 403 on /auth -> PAIR_DENIED - PASS
- token rotated: status ok=True 'מתנגן: Song One – Artist A.'; requests [('GET', '/api/v1/song'), ('POST', '/auth/jarves-mock'), ('GET', '/api/v1/song'), ('GET', '/api/v1/volume')]
- always 401: code=MUSIC_PLAYER_ERROR 'יש בעיה בנגן המוזיקה.'; 3 requests (no loop) [('GET', '/api/v1/song'), ('POST', '/auth/jarves-mock'), ('GET', '/api/v1/song')]
- 403 on /auth: code=MUSIC_PAIR_DENIED 'החיבור לנגן נדחה. צריך לאשר אותו ב-Pear Desktop.'; marker removed=True
- after deny (no marker): code=MUSIC_NOT_PAIRED 'עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.'; auth calls after: [('POST', '/auth/jarves-mock')]

### R01 search edge cases: empty, no videoIds, malformed JSON, 500, timeout, 204 - PASS
- empty results: code=MUSIC_NOT_FOUND 'לא מצאתי את "Song One".' OK
- only artist/browse results: code=MUSIC_NOT_FOUND 'לא מצאתי את "Song One".' OK
- malformed JSON: code=MUSIC_PLAYER_ERROR 'יש בעיה בנגן המוזיקה.' OK
- JSON array: code=MUSIC_NOT_FOUND 'לא מצאתי את "Song One".' OK
- HTTP 500: code=MUSIC_PLAYER_ERROR 'יש בעיה בנגן המוזיקה.' OK
- HTTP 204 empty: code=MUSIC_NOT_FOUND 'לא מצאתי את "Song One".' OK
- timeout: code=MUSIC_TIMEOUT 'נגן המוזיקה לא עונה.' OK
- connection reset: code=MUSIC_PLAYER_OFFLINE 'נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.' OK

### R02 now_playing edge cases: 204, malformed, missing fields, Unicode/RTL titles - PASS
- 204 (nothing): ok 'שום דבר לא מתנגן כרגע.'
- malformed JSON: ok=False code=MUSIC_PLAYER_ERROR 'יש בעיה בנגן המוזיקה.' OK
- missing fields: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- null fields: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- string durations: ok=True code=ok 'מתנגן: X – Y.' OK
- RTL/emoji title: play ok=True 'מנגן: \u200fשיר\u200f של \u202bאהבה\u202c 🎵 (Live) – ארטיסט \u200eA\u200e'; status 'מתנגן: \u200fשיר\u200f של \u202bאהבה\u202c 🎵 (Live) – ארטיסט \u200eA\u200e.'
- bidi marks / spaces differ (other id): ok=True code=ok 'מנגן: שני  משוגעים\u200f – עומר אדם'

### R03 video result vs its 'song' counterpart (other videoId / title suffix) - PASS
- same title, other id: searched 'Bohemian Rhapsody' played 'Bohemian Rhapsody': ok=True code=ok 'מנגן: Bohemian Rhapsody – Queen' OK
- video title has '(Official Video)': searched 'Bohemian Rhapsody (Official Video)' played 'Bohemian Rhapsody': ok=True code=ok 'מנגן: Bohemian Rhapsody – Queen' OK
- '[Official Music Video]' + remaster: searched 'Hotel California [Official Music Video]' played 'Hotel California (2013 Remaster)': ok=True code=ok 'מנגן: Hotel California (2013 Remaster) – Queen' OK
- a different song entirely: searched 'Bohemian Rhapsody' played 'Another One Bites the Dust': ok=False code=MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.' OK

### R04 queue endpoint broken / slow -> play still works or fails honestly - PASS
- queue malformed: ok=True code=ok 'מנגן: Song One – Artist A' 3.0s OK
- queue 500: ok=False code=MUSIC_PLAYER_ERROR 'יש בעיה בנגן המוזיקה.' 0.0s OK
- queue empty body: ok=True code=ok 'מנגן: Song One – Artist A' 3.1s OK

### K01 long soak: alternating play/next/pause/resume/status at volume 0 - FAIL
Pear Desktop did not answer in time
-     q0 = len(await c.player.queue())
-              ^^^^^^^^^^^^^^^^^^^^^^
-   File "C:\work\jarvis\.claude\worktrees\agent-a764419aef9a54f07\core\src\jarves\skills\music.py", line 390, in queue
-     r = await self._req("GET", "/queue")
-         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
-   File "C:\work\jarvis\.claude\worktrees\agent-a764419aef9a54f07\core\src\jarves\skills\music.py", line 364, in _req
-     raise MusicError("MUSIC_TIMEOUT", "Pear Desktop did not answer in time", "check that the app is not frozen") from e
- jarves.skills.music.MusicError: Pear Desktop did not answer in time

### V01 voice: 'נגן את שלמה ארצי' -> Pear plays the artist - PASS
heard 'נגיד את שלמה ארצי'; said "רגע. / מנגן עכשיו את 'תתארו לכם' של שלמה ארצי."
- heard as: 'נגיד את שלמה ארצי'
- said: "רגע. / מנגן עכשיו את 'תתארו לכם' של שלמה ארצי."
- tools: ['music_play']; results: [('music_play', True, 'music_play -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('music', 'מנגן', 'תתארו לכם – שלמה ארצי'), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'Dancing Queen'/'ABBA' paused=True -> after: 'תתארו לכם'/'שלמה ארצי' paused=False

### V02 voice: 'עצור' -> paused - PASS
heard 'עצור'; said 'שנייה. עצרתי את המוזיקה.'
- heard as: 'עצור'
- said: 'שנייה. עצרתי את המוזיקה.'
- tools: ['music_stop']; results: [('music_stop', True, 'music_stop -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מוזיקה…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'תתארו לכם'/'שלמה ארצי' paused=False -> after: 'תתארו לכם'/'שלמה ארצי' paused=True

### V03 voice: 'תמשיך' -> resumed - PASS
heard 'תמשיך'; said "רגע. / ממשיך את 'תתארו לכם' של שלמה ארצי."
- heard as: 'תמשיך'
- said: "רגע. / ממשיך את 'תתארו לכם' של שלמה ארצי."
- tools: ['music_resume']; results: [('music_resume', True, 'music_resume -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'ממשיך…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'תתארו לכם'/'שלמה ארצי' paused=True -> after: 'תתארו לכם'/'שלמה ארצי' paused=False

### V04 voice: 'השיר הבא' -> track changed - PASS
heard 'השיר הבא'; said "שנייה. השיר הבא הוא 'שני משוגעים' של עומר אדם."
- heard as: 'השיר הבא'
- said: "שנייה. השיר הבא הוא 'שני משוגעים' של עומר אדם."
- tools: ['music_next']; results: [('music_next', True, 'music_next -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'השיר הבא…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'תתארו לכם'/'שלמה ארצי' paused=False -> after: 'שני משוגעים'/'עומר אדם' paused=False

### V05 voice: 'מה מתנגן עכשיו?' -> names the real title - PASS
heard 'מה מתנגן עכשיו'; said "רגע. כרגע מתנגן 'שני משוגעים' של עומר אדם."
- heard as: 'מה מתנגן עכשיו'
- said: "רגע. כרגע מתנגן 'שני משוגעים' של עומר אדם."
- tools: ['music_status']; results: [('music_status', True, 'music_status -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מוזיקה…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'שני משוגעים'/'עומר אדם' paused=False -> after: 'שני משוגעים'/'עומר אדם' paused=False

### V06 voice: 'נגן משהו של Queen' - PASS
heard 'נגן משהו של כווין'; said "רגע. / מנגן עכשיו את 'Bohemian Rhapsody' של Queen."
- heard as: 'נגן משהו של כווין'
- said: "רגע. / מנגן עכשיו את 'Bohemian Rhapsody' של Queen."
- tools: ['music_play']; results: [('music_play', True, 'music_play -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('music', 'מנגן', 'Bohemian Rhapsody – Queen'), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'שני משוגעים'/'עומר אדם' paused=False -> after: 'Bohemian Rhapsody'/'Queen' paused=False

### V07 voice: 'תנגן שיר שמח' (vague) - PASS
heard 'תנגן שיר שמח'; said "שנייה. / מנגן עכשיו את 'Can't Hold Us' של מקלמור וראיין לואיס."
- heard as: 'תנגן שיר שמח'
- said: "שנייה. / מנגן עכשיו את 'Can't Hold Us' של מקלמור וראיין לואיס."
- tools: ['music_play']; results: [('music_play', True, 'music_play -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('music', 'מנגן', "Can't Hold Us – מקלמור וראיין לואיס"), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'Bohemian Rhapsody'/'Queen' paused=False -> after: "Can't Hold Us"/'מקלמור וראיין לואיס' paused=False

### V08 voice: web question while music plays (mixed) - PASS
heard 'מה מזג האוויר היום בתל אביב'; said 'רגע. אני בודק מה מזג האוויר היום בתל אביב. היום, יום שני, תשעה עשר באוקטובר, בתל אביב מעונן חלקית עד שמשי, בערך עשרים וש'
- heard as: 'מה מזג האוויר היום בתל אביב'
- said: 'רגע. אני בודק מה מזג האוויר היום בתל אביב. היום, יום שני, תשעה עשר באוקטובר, בתל אביב מעונן חלקית עד שמשי, בערך עשרים ושבע מעלות. בלילה סביב העשרים.'
- tools: []; results: []
- activity: [('thinking', 'חושב…', ''), ('searching', 'מחפש ברשת…', ''), ('searching', 'מחפש ברשת…', ''), ('searching', 'מחפש ברשת…', ''), ('searching', 'מחפש ברשת…', ''), ('done', '', '')]
- pear before: "Can't Hold Us"/'מקלמור וראיין לואיס' paused=False -> after: "Can't Hold Us"/'מקלמור וראיין לואיס' paused=False

### V09 voice: music right after the web question - PASS
heard 'עכשיו תנגן את עומר אדם'; said "אוקיי. רגע, אני מעביר לעומר אדם. / סבבה, מנגן את 'שני משוגעים' של עומר אדם."
- heard as: 'עכשיו תנגן את עומר אדם'
- said: "אוקיי. רגע, אני מעביר לעומר אדם. / סבבה, מנגן את 'שני משוגעים' של עומר אדם."
- tools: ['music_play']; results: [('music_play', True, 'music_play -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('music', 'מנגן', 'שני משוגעים – עומר אדם'), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: "Can't Hold Us"/'מקלמור וראיין לואיס' paused=False -> after: 'שני משוגעים'/'עומר אדם' paused=False

### V10 voice: screen activity 'music' shows the song - PASS
- V01: music activity details ['תתארו לכם – שלמה ארצי'] vs pear 'תתארו לכם' -> True
- V06: music activity details ['Bohemian Rhapsody – Queen'] vs pear 'Bohemian Rhapsody' -> True
- V07: music activity details ["Can't Hold Us – מקלמור וראיין לואיס"] vs pear "Can't Hold Us" -> True
- V09: music activity details ['שני משוגעים – עומר אדם'] vs pear 'שני משוגעים' -> True

