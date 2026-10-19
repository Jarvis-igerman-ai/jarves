# Pre-use test report: music (Pear Desktop / YouTube Music)

Generated 2026-10-19 13:33 by `core/tools/suite_music.py` against the REAL Pear Desktop on this PC, silently (Pear volume 0, watchdog cap 3, restored at the end; virtual mic/speaker for voice).

**Result: 20/23 scenarios PASS.**

| # | Group | Scenario | Result | Key number | Time | Run at |
|---|---|---|---|---|---|---|
| S01 | skill | play Hebrew artist: שלמה ארצי | PASS | confirm 2.4 s | 2.4 s | 10-19 13:29 |
| S02 | skill | play Hebrew artist: עומר אדם | PASS | confirm 2.9 s | 2.9 s | 10-19 13:29 |
| S03 | skill | play English artist+song: Queen Bohemian Rhapsody | PASS | confirm 2.2 s | 2.2 s | 10-19 13:29 |
| S04 | skill | play English artist: Beatles | PASS | confirm 2.4 s | 2.4 s | 10-19 13:29 |
| S05 | skill | play a mood: משהו רגוע | PASS | confirm 2.4 s | 2.4 s | 10-19 13:29 |
| S06 | skill | play a different song while one is playing (queue-index fix) | PASS | confirm 2.3 s | 3.9 s | 10-19 13:29 |
| S07 | skill | pause, resume, next, stop vs Pear state | **FAIL** | pause FAIL, resume 0.0s, next 0.0s, stop FAIL | 21.4 s | 10-19 13:30 |
| S08 | skill | status tool text matches Pear (playing and paused) | **FAIL** | mismatch | 9.3 s | 10-19 13:30 |
| S09 | skill | volume 0 and 2 on real Pear; cap enforced (fake player, max 70) | PASS | cap 70 held (7/7) | 3.3 s | 10-19 13:30 |
| S10 | skill | rapid play -> next -> pause -> resume -> next (no waits) | **FAIL** | sequence 8.6 s, in sync=True | 8.6 s | 10-19 13:30 |
| S11 | skill | 10 different searches back to back (confirm latency each) | PASS | 10/10 ok; median 2.3 s, max 5.8 s | 26.5 s | 10-19 13:30 |
| S12 | skill | nonsense query -> honest 'not found' | PASS | MUSIC_NOT_FOUND in 0.4 s | 0.4 s | 10-19 13:30 |
| S13 | skill | pause / next / resume / status when nothing is playing -> graceful | PASS | 5/5 graceful | 0.3 s | 10-19 13:30 |
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
| Adele Hello | 2.3 s |
| Coldplay Yellow | 2.3 s |
| אריק איינשטיין אוהב להיות בבית | 2.2 s |
| Michael Jackson Billie Jean | 2.3 s |
| שלום חנוך | 2.3 s |
| Daft Punk Get Lucky | 5.8 s |
| עידן רייכל | 2.2 s |
| Eminem Lose Yourself | 2.3 s |
| נועה קירל | 2.3 s |
| ABBA Dancing Queen | 2.3 s |

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

- Final Pear state: `{"pause_workaround": ["skipped stuck fRFEiZhAVO0", "skipped stuck BSTsnWoslP4"], "paused": true, "volume_posted": 28, "volume": {"state": 8, "isMuted": false}, "max_volume_seen_during_tests": 8, "volume_violations": 1}`

## Evidence per scenario

### S01 play Hebrew artist: שלמה ארצי - PASS
תתארו לכם – שלמה ארצי
- tool: ok=True code=ok msg='מנגן: תתארו לכם – שלמה ארצי' (2.4 s)
- pear /song: title='תתארו לכם' artist='שלמה ארצי' id=G2cWvgK1m6M paused=False

### S02 play Hebrew artist: עומר אדם - PASS
שני משוגעים – עומר אדם
- tool: ok=True code=ok msg='מנגן: שני משוגעים – עומר אדם' (2.9 s)
- pear /song: title='שני משוגעים' artist='עומר אדם' id=fRFEiZhAVO0 paused=False

### S03 play English artist+song: Queen Bohemian Rhapsody - PASS
Bohemian Rhapsody – Queen
- tool: ok=True code=ok msg='מנגן: Bohemian Rhapsody – Queen' (2.2 s)
- pear /song: title='Bohemian Rhapsody' artist='Queen' id=BSTsnWoslP4 paused=False

### S04 play English artist: Beatles - PASS
Get Back (Remastered 2009) – The Beatles
- tool: ok=True code=ok msg='מנגן: Get Back (Remastered 2009) – The Beatles' (2.4 s)
- pear /song: title='Get Back (Remastered 2009)' artist='The Beatles' id=IKJqecxswCA paused=False

### S05 play a mood: משהו רגוע - PASS
משהו קטן וטוב – משינה
- tool: ok=True code=ok msg='מנגן: משהו קטן וטוב – משינה' (2.4 s)
- pear /song: title='משהו קטן וטוב' artist='משינה' id=SpOQwxDvddM paused=False

### S06 play a different song while one is playing (queue-index fix) - PASS
old next=BSTsnWoslP4, now=BciS5krYL80 'Hotel California'
- tool: ok=True code=ok msg='מנגן: Bohemian Rhapsody – Queen' (1.5 s)
- pear /song: title='Bohemian Rhapsody' artist='Queen' id=BSTsnWoslP4 paused=False
- tool: ok=True code=ok msg='מנגן: Hotel California – Eagles' (2.3 s)
- pear /song: title='Hotel California' artist='Eagles' id=BciS5krYL80 paused=False
- queue len 39, selected 3, old next BSTsnWoslP4

### S07 pause, resume, next, stop vs Pear state - FAIL
- music_pause: ok=False msg='ביקשתי מהנגן להשהות, אבל הוא עדיין מנגן.' tool 4.04s; pear reached state after never s: 'Hotel California' paused=False
- music_resume: ok=True msg='ממשיך לנגן: Hotel California – Eagles' tool 0.01s; pear reached state after 0.00 s: 'Hotel California' paused=False
- music_next: ok=True msg='השיר הבא: Bohemian Rhapsody – Queen' tool 1.00s; pear reached state after 0.00 s: 'Bohemian Rhapsody' paused=False
- music_stop: ok=False msg='ביקשתי מהנגן להשהות, אבל הוא עדיין מנגן.' tool 4.09s; pear reached state after never s: 'Bohemian Rhapsody' paused=False

### S08 status tool text matches Pear (playing and paused) - FAIL
- status='מתנגן: Bohemian Rhapsody – Queen' (0.00s) vs pear title='Bohemian Rhapsody' paused=False
- status='מתנגן: Bohemian Rhapsody – Queen' (0.00s) vs pear title='Bohemian Rhapsody' paused=False

### S09 volume 0 and 2 on real Pear; cap enforced (fake player, max 70) - PASS
- real: asked 2: 'עוצמת המוזיקה 2'; pear reports {'state': 0, 'isMuted': False} after 3.0s
- real: asked 0: 'עוצמת המוזיקה 0'; pear reports {'state': 0, 'isMuted': True} after 0.2s
- real, max_volume=3: asked 50 -> 'עוצמת המוזיקה 3 (מוגבל ל-3)', data {'volume': 3}
- fake: asked 100: code=ok msg='עוצמת המוזיקה 70 (מוגבל ל-70)' player volume 70 OK
- fake: asked 71: code=ok msg='עוצמת המוזיקה 70 (מוגבל ל-70)' player volume 70 OK
- fake: asked 70: code=ok msg='עוצמת המוזיקה 70' player volume 70 OK
- fake: asked 50: code=ok msg='עוצמת המוזיקה 50' player volume 50 OK
- fake: asked 0: code=ok msg='עוצמת המוזיקה 0' player volume 0 OK
- fake: asked 150: code=TOOL_BAD_ARGS msg='invalid arguments for music_volume' player volume 0 OK
- fake: asked -1: code=TOOL_BAD_ARGS msg='invalid arguments for music_volume' player volume 0 OK

### S10 rapid play -> next -> pause -> resume -> next (no waits) - FAIL
- play: ok=True 'מנגן: Viva La Vida – Coldplay' 2.5s
- music_next: ok=True "השיר הבא: Can't Hold Us – מקלמור וראיין לואיס" 0.98s -> pear "Can't Hold Us" paused=False
- music_pause: ok=False 'ביקשתי מהנגן להשהות, אבל הוא עדיין מנגן.' 4.07s -> pear "Can't Hold Us" paused=False
- music_resume: ok=True "ממשיך לנגן: Can't Hold Us – מקלמור וראיין לואיס" 0.01s -> pear "Can't Hold Us" paused=False
- music_next: ok=True 'השיר הבא: שני משוגעים – Omer Adam' 0.99s -> pear 'שני משוגעים' paused=False
- settled: pear 'שני משוגעים' paused=False; status 'מתנגן: שני משוגעים – Omer Adam'

### S11 10 different searches back to back (confirm latency each) - PASS
latencies: 2.3, 2.3, 2.2, 2.3, 2.3, 5.8, 2.2, 2.3, 2.3, 2.3
- 'Adele Hello': OK rel=yes 2.34 s -> 'Hello' / 'Adele' | tool: ok=True code=ok msg='מנגן: Hello – Adele' (2.3 s)
- 'Coldplay Yellow': OK rel=yes 2.31 s -> 'Yellow' / 'Coldplay' | tool: ok=True code=ok msg='מנגן: Yellow – Coldplay' (2.3 s)
- 'אריק איינשטיין אוהב להיות בבית': OK rel=yes 2.24 s -> 'אוהב להיות בבית' / 'Arik Einstein ו-Miki Gavrielov' | tool: ok=True code=ok msg='מנגן: אוהב להיות בבית – Arik Einstein ו-Miki Gavrielov' (2.2 s)
- 'Michael Jackson Billie Jean': OK rel=yes 2.31 s -> 'Billie Jean' / 'Michael Jackson' | tool: ok=True code=ok msg='מנגן: Billie Jean – Michael Jackson' (2.3 s)
- 'שלום חנוך': OK rel=yes 2.32 s -> 'בלי לומר מילה' / 'Shalom Hanoch' | tool: ok=True code=ok msg='מנגן: בלי לומר מילה – Shalom Hanoch' (2.3 s)
- 'Daft Punk Get Lucky': OK rel=yes 5.83 s -> 'Get Lucky (Radio Edit - feat. Pharrell Williams and Nile Rodgers)' / 'Daft Punk, Pharrell Williams ו-Nile Rodgers' | tool: ok=True code=ok msg='מנגן: Get Lucky (Radio Edit - feat. Pharrell Williams and Nile Rodgers) – Daft Punk, Pharrell Williams ו-Nile Rodgers' (5.8 s)
- 'עידן רייכל': OK rel=yes 2.22 s -> "Mi'Ma'amakim (Out of the Depths)" / 'Idan Raichel' | tool: ok=True code=ok msg="מנגן: Mi'Ma'amakim (Out of the Depths) – Idan Raichel" (2.2 s)
- 'Eminem Lose Yourself': OK rel=yes 2.29 s -> 'Lose Yourself' / 'Eminem' | tool: ok=True code=ok msg='מנגן: Lose Yourself – Eminem' (2.3 s)
- 'נועה קירל': OK rel=yes 2.29 s -> 'Unicorn' / 'Noa Kirel וכאן - תאגיד השידור הישראלי' | tool: ok=True code=ok msg='מנגן: Unicorn – Noa Kirel וכאן - תאגיד השידור הישראלי' (2.3 s)
- 'ABBA Dancing Queen': OK rel=yes 2.34 s -> 'Dancing Queen' / 'ABBA' | tool: ok=True code=ok msg='מנגן: Dancing Queen – ABBA' (2.3 s)

### S12 nonsense query -> honest 'not found' - PASS
לא מצאתי את "asdkjhqwe zzxxcc".
- tool: ok=False code=MUSIC_NOT_FOUND msg='לא מצאתי את "asdkjhqwe zzxxcc".' data={} (0.4s)
- pear before 'Dancing Queen' -> after 'Dancing Queen'

### S13 pause / next / resume / status when nothing is playing -> graceful - PASS
- fake (nothing playing) music_pause: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- fake (nothing playing) music_next: ok=False code=MUSIC_NOTHING_PLAYING 'שום דבר לא מתנגן כרגע. אפשר לבקש ממני שיר.' OK
- fake (nothing playing) music_resume: ok=False code=MUSIC_NOTHING_PLAYING 'שום דבר לא מתנגן כרגע. אפשר לבקש ממני שיר.' OK
- fake (nothing playing) music_stop: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- fake (nothing playing) music_status: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- real pause-while-paused: ok=True 'השהיתי.'; pear paused=True

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


## Second follow-up (19 Oct 2026): what the fresh-Pear re-run found

After restarting Pear the skill/voice scenarios were re-run once: **22/30 PASS**. Plays confirmed in 1.9-7.4 s (S01-S06 pass; S11 median 2.3 s). Two product problems were visible and fixed in code, with offline tests:

- **`music_pause` claimed "השהיתי" while Pear kept playing** (S07/S08). A pause pressed while a track is still loading can be ignored. Now the pause is verified with the player, pressed once more, and if it still did not happen JARVES says so (`MUSIC_NOT_CONFIRMED`) instead of claiming it.
- **A result that cannot play** (S11 "Daft Punk Get Lucky": the top YouTube result did not start and Pear skipped to an unrelated song). `music_play` now tries up to 3 distinct candidates, each verified, and only then reports it honestly.

**NOT re-verified live after these two fixes:** the re-run was stopped by the OS because the PC ran out of memory (free RAM ~1 GB of 7.5 GB), and afterwards Pear's player was stuck (elapsed 0, ignoring pause), so the voice-driven scenarios V01/V03/V05/V09/V10 and the 15-minute soak are still open. If Pear does not start a song tomorrow: close and reopen it (the menu entry or the desktop shortcut).
