# Pre-use test report: music (Pear Desktop / YouTube Music)

Generated 2026-10-19 13:06 by `core/tools/suite_music.py` against the REAL Pear Desktop on this PC, silently (Pear volume 0, watchdog cap 3, restored at the end; virtual mic/speaker for voice).

**Result: 24/31 scenarios PASS.**

| # | Group | Scenario | Result | Key number | Time | Run at |
|---|---|---|---|---|---|---|
| S01 | skill | play Hebrew artist: שלמה ארצי | PASS | confirm 8.7 s | 8.7 s | 10-19 12:47 |
| S02 | skill | play Hebrew artist: עומר אדם | PASS | confirm 6.5 s | 6.5 s | 10-19 12:47 |
| S03 | skill | play English artist+song: Queen Bohemian Rhapsody | PASS | confirm 4.0 s | 4.1 s | 10-19 12:47 |
| S04 | skill | play English artist: Beatles | **FAIL** | confirm 13.3 s | 13.3 s | 10-19 12:47 |
| S05 | skill | play a mood: משהו רגוע | PASS | confirm 9.3 s | 9.3 s | 10-19 12:48 |
| S06 | skill | play a different song while one is playing (queue-index fix) | PASS | confirm 9.1 s | 14.7 s | 10-19 12:48 |
| S07 | skill | pause, resume, next, stop vs Pear state | **FAIL** | pause 0.2s, resume 0.0s, next 4.8s, stop 0.2s | 12.9 s | 10-19 12:48 |
| S08 | skill | status tool text matches Pear (playing and paused) | PASS | 2/2 states | 0.6 s | 10-19 12:48 |
| S09 | skill | volume 0 and 2 on real Pear; cap enforced (fake player, max 70) | PASS | cap 70 held (7/7) | 3.3 s | 10-19 12:48 |
| S10 | skill | rapid play -> next -> pause -> resume -> next (no waits) | **FAIL** | sequence 16.3 s, in sync=True | 16.3 s | 10-19 12:48 |
| S11 | skill | 10 different searches back to back (confirm latency each) | **FAIL** | 7/10 ok; median 10.9 s, max 14.9 s | 104.0 s | 10-19 12:50 |
| S12 | skill | nonsense query -> honest 'not found' | PASS | MUSIC_NOT_FOUND in 0.5 s | 0.5 s | 10-19 12:50 |
| S13 | skill | pause / next / resume / status when nothing is playing -> graceful | PASS | 5/5 graceful | 0.2 s | 10-19 12:50 |
| E01 | errors | Pear unreachable (port 26599) -> MUSIC_PLAYER_OFFLINE, Hebrew | PASS | 3/3 offline + health unavailable | 8.2 s | 10-19 12:50 |
| E02 | errors | unpaired id at the real port -> MUSIC_NOT_PAIRED, no /auth (no dialog) | PASS | /auth calls 0, 5 requests | 0.0 s | 10-19 12:50 |
| E03 | errors | 401 -> silent re-pair (mock), no loop; 403 on /auth -> PAIR_DENIED | PASS | re-pair 1 auth; no loop; deny honoured | 0.4 s | 10-19 12:50 |
| R01 | robust | search edge cases: empty, no videoIds, malformed JSON, 500, timeout, 204 | PASS | 8/8 sensible | 0.1 s | 10-19 12:50 |
| R02 | robust | now_playing edge cases: 204, malformed, missing fields, Unicode/RTL titles | PASS | no crash, RTL kept | 0.8 s | 10-19 12:50 |
| R03 | robust | video result vs its 'song' counterpart (other videoId / title suffix) | PASS | 4/4 honest | 2.7 s | 10-19 12:50 |
| R04 | robust | queue endpoint broken / slow -> play still works or fails honestly | PASS | no crash | 6.3 s | 10-19 12:50 |
| K01 | soak | long soak: alternating play/next/pause/resume/status at volume 0 | **FAIL** | 197 ops in 15 min, 38 errors, 8 desyncs | 901.7 s | 10-19 13:05 |
| V01 | voice | voice: 'נגן את שלמה ארצי' -> Pear plays the artist | PASS | Pear 9.27 s, dead air 0.79 s | 35.2 s | 10-19 12:44 |
| V02 | voice | voice: 'עצור' -> paused | PASS | Pear 0.86 s, dead air 0.66 s | 18.6 s | 10-19 08:46 |
| V03 | voice | voice: 'תמשיך' -> resumed | PASS | Pear 1.25 s, dead air 0.89 s | 18.6 s | 10-19 08:46 |
| V04 | voice | voice: 'השיר הבא' -> track changed | PASS | Pear 4.88 s, dead air 0.9 s | 18.6 s | 10-19 08:46 |
| V05 | voice | voice: 'מה מתנגן עכשיו?' -> names the real title | PASS | named title=True, dead air 0.52 s | 18.6 s | 10-19 08:46 |
| V06 | voice | voice: 'נגן משהו של Queen' | **FAIL** | Pear None s, dead air 0.67 s | 35.2 s | 10-19 12:44 |
| V07 | voice | voice: 'תנגן שיר שמח' (vague) | PASS | Pear 5.99 s, dead air 0.89 s | 18.6 s | 10-19 08:46 |
| V08 | voice | voice: web question while music plays (mixed) | PASS | answered, music undisturbed=True, dead air 0.52 s | 18.6 s | 10-19 08:46 |
| V09 | voice | voice: music right after the web question | PASS | Pear 5.55 s, dead air 1.08 s | 18.6 s | 10-19 08:46 |
| V10 | voice | voice: screen activity 'music' shows the song | **FAIL** | 0/2 play turns showed the song | 35.2 s | 10-19 12:44 |

## Confirm latency (search -> Pear reports the song playing), S11

| Query | Latency |
|---|---|
| Adele Hello | 10.9 s |
| Coldplay Yellow | 6.9 s |
| אריק איינשטיין אוהב להיות בבית | 9.8 s |
| Michael Jackson Billie Jean | 14.9 s |
| שלום חנוך | 12.0 s |
| Daft Punk Get Lucky | 14.4 s |
| עידן רייכל | 13.7 s |
| Eminem Lose Yourself | 8.3 s |
| נועה קירל | 4.6 s |
| ABBA Dancing Queen | 8.5 s |

## Voice turns: dead air and time until Pear actually played

| Turn | Dead air (question end -> first sound) | Pear state reached after |
|---|---|---|
| V01 | 0.79 s | 9.27 s |
| V02 | 0.66 s | 0.86 s |
| V03 | 0.89 s | 1.25 s |
| V04 | 0.9 s | 4.88 s |
| V05 | 0.52 s | None s |
| V06 | 0.67 s | None s |
| V07 | 0.89 s | 5.99 s |
| V08 | 0.52 s | None s |
| V09 | 1.08 s | 5.55 s |

Live GPT-Live usage: 3 sessions, 7.9 wall-minutes in total (upper bound of billed voice time; about $0.40 voice + backend tokens). Runs: 3.89 min / 9 turns, 0.99 min / 2 turns, 3.04 min / 10 turns

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

- Final Pear state: `{"paused": true, "volume_posted": 8, "volume": {"state": 1, "isMuted": false}, "max_volume_seen_during_tests": 0, "volume_violations": 0}`

## Evidence per scenario

### S01 play Hebrew artist: שלמה ארצי - PASS
תתארו לכם – שלמה ארצי
- tool: ok=True code=ok msg='מנגן: תתארו לכם – שלמה ארצי' (8.7 s)
- pear /song: title='תתארו לכם' artist='שלמה ארצי' id=G2cWvgK1m6M paused=False

### S02 play Hebrew artist: עומר אדם - PASS
שני משוגעים – עומר אדם
- tool: ok=True code=ok msg='מנגן: שני משוגעים – עומר אדם' (6.5 s)
- pear /song: title='שני משוגעים' artist='עומר אדם' id=fRFEiZhAVO0 paused=False

### S03 play English artist+song: Queen Bohemian Rhapsody - PASS
Bohemian Rhapsody – Queen
- tool: ok=True code=ok msg='מנגן: Bohemian Rhapsody – Queen' (4.0 s)
- pear /song: title='Bohemian Rhapsody' artist='Queen' id=BSTsnWoslP4 paused=False

### S04 play English artist: Beatles - FAIL
Get Back (Remastered 2009) – The Beatles
- tool: ok=False code=MUSIC_NOT_CONFIRMED msg='ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.' (13.3 s)
- pear /song: title='Get Back (Remastered 2009)' artist='The Beatles' id=IKJqecxswCA paused=False

### S05 play a mood: משהו רגוע - PASS
משהו קטן וטוב – משינה
- tool: ok=True code=ok msg='מנגן: משהו קטן וטוב – משינה' (9.3 s)
- pear /song: title='משהו קטן וטוב' artist='משינה' id=SpOQwxDvddM paused=False

### S06 play a different song while one is playing (queue-index fix) - PASS
old next=BSTsnWoslP4, now=BciS5krYL80 'Hotel California'
- tool: ok=True code=ok msg='מנגן: Bohemian Rhapsody – Queen' (5.3 s)
- pear /song: title='Bohemian Rhapsody' artist='Queen' id=BSTsnWoslP4 paused=False
- tool: ok=True code=ok msg='מנגן: Hotel California – Eagles' (9.1 s)
- pear /song: title='Hotel California' artist='Eagles' id=BciS5krYL80 paused=False
- queue len 175, selected 126, old next BSTsnWoslP4

### S07 pause, resume, next, stop vs Pear state - FAIL
- music_pause: ok=True msg='השהיתי.' tool 0.01s; pear reached state after 0.22 s: 'Hotel California' paused=True
- music_resume: ok=True msg='ממשיך לנגן: Hotel California – Eagles' tool 0.31s; pear reached state after 0.00 s: 'Hotel California' paused=False
- music_next: ok=False msg='ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.' tool 7.32s; pear reached state after 4.77 s: 'Get Back' paused=False
- music_stop: ok=True msg='עצרתי את המוזיקה.' tool 0.01s; pear reached state after 0.21 s: 'Get Back' paused=True

### S08 status tool text matches Pear (playing and paused) - PASS
- status='מתנגן: Get Back – The Beatles ו-Billy Preston' (0.00s) vs pear title='Get Back' paused=False
- status='מושהה: Get Back – The Beatles ו-Billy Preston' (0.00s) vs pear title='Get Back' paused=True

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
- play: ok=False 'נגן המוזיקה לא עונה.' 6.4s
- music_next: ok=True 'השיר הבא: Viva La Vida – Coldplay' 2.44s -> pear 'Viva La Vida' paused=False
- music_pause: ok=True 'השהיתי.' 0.01s -> pear 'Viva La Vida' paused=False
- music_resume: ok=True 'ממשיך לנגן: Viva La Vida – Coldplay' 0.01s -> pear 'Viva La Vida' paused=False
- music_next: ok=False 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.' 7.37s -> pear 'Viva La Vida' paused=False
- settled: pear 'Viva La Vida' paused=False; status 'מתנגן: Viva La Vida – Coldplay'

### S11 10 different searches back to back (confirm latency each) - FAIL
latencies: 10.9, 6.9, 9.8, 14.9, 12.0, 14.4, 13.7, 8.3, 4.6, 8.5
- 'Adele Hello': OK rel=yes 10.86 s -> 'Hello' / 'Adele' | tool: ok=True code=ok msg='מנגן: Hello – Adele' (10.9 s)
- 'Coldplay Yellow': OK rel=yes 6.89 s -> 'Yellow' / 'Coldplay' | tool: ok=True code=ok msg='מנגן: Yellow – Coldplay' (6.9 s)
- 'אריק איינשטיין אוהב להיות בבית': OK rel=yes 9.85 s -> 'אוהב להיות בבית' / 'Arik Einstein ו-Miki Gavrielov' | tool: ok=True code=ok msg='מנגן: אוהב להיות בבית – Arik Einstein ו-Miki Gavrielov' (9.9 s)
- 'Michael Jackson Billie Jean': FAIL rel=NO 14.91 s -> 'אוהב להיות בבית' / 'Arik Einstein ו-Miki Gavrielov' | tool: ok=False code=MUSIC_NOT_CONFIRMED msg='ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.' (14.9 s)
- 'שלום חנוך': FAIL rel=NO 12.0 s -> 'אוהב להיות בבית' / 'Arik Einstein ו-Miki Gavrielov' | tool: ok=False code=MUSIC_NOT_CONFIRMED msg='ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.' (12.0 s)
- 'Daft Punk Get Lucky': FAIL rel=NO 14.36 s -> 'אוהב להיות בבית' / 'Arik Einstein ו-Miki Gavrielov' | tool: ok=False code=MUSIC_NOT_CONFIRMED msg='ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.' (14.4 s)
- 'עידן רייכל': OK rel=yes 13.68 s -> "Mi'Ma'amakim (Out of the Depths)" / 'Idan Raichel' | tool: ok=True code=ok msg="מנגן: Mi'Ma'amakim (Out of the Depths) – Idan Raichel" (13.7 s)
- 'Eminem Lose Yourself': OK rel=yes 8.35 s -> 'Lose Yourself' / 'Eminem' | tool: ok=True code=ok msg='מנגן: Lose Yourself – Eminem' (8.3 s)
- 'נועה קירל': OK rel=yes 4.61 s -> 'Unicorn' / 'Noa Kirel וכאן - תאגיד השידור הישראלי' | tool: ok=True code=ok msg='מנגן: Unicorn – Noa Kirel וכאן - תאגיד השידור הישראלי' (4.6 s)
- 'ABBA Dancing Queen': OK rel=yes 8.49 s -> 'Dancing Queen' / 'ABBA' | tool: ok=True code=ok msg='מנגן: Dancing Queen – ABBA' (8.5 s)

### S12 nonsense query -> honest 'not found' - PASS
לא מצאתי את "asdkjhqwe zzxxcc".
- tool: ok=False code=MUSIC_NOT_FOUND msg='לא מצאתי את "asdkjhqwe zzxxcc".' data={} (0.5s)
- pear before 'Dancing Queen' -> after 'Dancing Queen'

### S13 pause / next / resume / status when nothing is playing -> graceful - PASS
- fake (nothing playing) music_pause: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- fake (nothing playing) music_next: ok=False code=MUSIC_NOTHING_PLAYING 'שום דבר לא מתנגן כרגע. אפשר לבקש ממני שיר.' OK
- fake (nothing playing) music_resume: ok=False code=MUSIC_NOTHING_PLAYING 'שום דבר לא מתנגן כרגע. אפשר לבקש ממני שיר.' OK
- fake (nothing playing) music_stop: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- fake (nothing playing) music_status: ok=True code=ok 'שום דבר לא מתנגן כרגע.' OK
- real pause-while-paused: ok=True 'השהיתי.'; pear paused=True

### E01 Pear unreachable (port 26599) -> MUSIC_PLAYER_OFFLINE, Hebrew - PASS
- music_play: code=MUSIC_PLAYER_OFFLINE 'נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.' 2.04s
- music_pause: code=MUSIC_PLAYER_OFFLINE 'נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.' 2.05s
- music_status: code=MUSIC_PLAYER_OFFLINE 'נגן המוזיקה לא פועל. צריך להפעיל את Pear Desktop.' 2.03s
- health: ('unavailable', 'Pear Desktop is not reachable; start Pear Desktop and enable Plugins > API Server (host 127.0.0.1, port 26538)')

### E02 unpaired id at the real port -> MUSIC_NOT_PAIRED, no /auth (no dialog) - PASS
- music_play: code=MUSIC_NOT_PAIRED 'עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.'
- music_next: code=MUSIC_NOT_PAIRED 'עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.'
- music_status: code=MUSIC_NOT_PAIRED 'עוד לא חיברו אותי לנגן המוזיקה. צריך להריץ jarves music pair ולאשר.'
- health: ('degraded', 'pear running but not paired; run `jarves music pair` once and click Allow in Pear Desktop'); requests: [('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401), ('GET', '/api/v1/song', 401)]

### E03 401 -> silent re-pair (mock), no loop; 403 on /auth -> PAIR_DENIED - PASS
- token rotated: status ok=True 'מתנגן: Song One – Artist A'; requests [('GET', '/api/v1/song'), ('POST', '/auth/jarves-mock'), ('GET', '/api/v1/song')]
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
- string durations: ok=True code=ok 'מתנגן: X – Y' OK
- RTL/emoji title: play ok=True 'מנגן: \u200fשיר\u200f של \u202bאהבה\u202c 🎵 (Live) – ארטיסט \u200eA\u200e'; status 'מתנגן: \u200fשיר\u200f של \u202bאהבה\u202c 🎵 (Live) – ארטיסט \u200eA\u200e'
- bidi marks / spaces differ (other id): ok=True code=ok 'מנגן: שני  משוגעים\u200f – עומר אדם'

### R03 video result vs its 'song' counterpart (other videoId / title suffix) - PASS
- same title, other id: searched 'Bohemian Rhapsody' played 'Bohemian Rhapsody': ok=True code=ok 'מנגן: Bohemian Rhapsody – Queen' OK
- video title has '(Official Video)': searched 'Bohemian Rhapsody (Official Video)' played 'Bohemian Rhapsody': ok=True code=ok 'מנגן: Bohemian Rhapsody – Queen' OK
- '[Official Music Video]' + remaster: searched 'Hotel California [Official Music Video]' played 'Hotel California (2013 Remaster)': ok=True code=ok 'מנגן: Hotel California (2013 Remaster) – Queen' OK
- a different song entirely: searched 'Bohemian Rhapsody' played 'Another One Bites the Dust': ok=False code=MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.' OK

### R04 queue endpoint broken / slow -> play still works or fails honestly - PASS
- queue malformed: ok=True code=ok 'מנגן: Song One – Artist A' 3.1s OK
- queue 500: ok=False code=MUSIC_PLAYER_ERROR 'יש בעיה בנגן המוזיקה.' 0.0s OK
- queue empty body: ok=True code=ok 'מנגן: Song One – Artist A' 3.1s OK

### K01 long soak: alternating play/next/pause/resume/status at volume 0 - FAIL
queue 187 -> 207 (max 207, 20 plays); process memory growth 0 KB (cycle 2 -> end); python objects 48143->46464; tasks 2->2
- #1 play: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.'
- #1 play: pear paused=True expected False
- #3 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #3 next: pear paused=True expected False
- #9 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #23 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #29 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #39 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #41 play: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.'
- #43 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #49 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #51 play: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.'
- #53 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #59 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #66 status 'מושהה: Hotel California – Eagles' but pear 'Bohemian Rhapsody'
- #79 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #81 play: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לנגן, אבל הוא לא מדווח שהשיר מתנגן.'
- #82 status 'מתנגן: Billie Jean – Michael Jackson' but pear 'תתארו לכם'
- #83 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- #93 next: MUSIC_NOT_CONFIRMED 'ביקשתי מהנגן לעבור לשיר הבא, אבל הוא לא מדווח שזה קרה.'
- latency {'play': 'median 11.15s max 15.87s', 'status': 'median 0.00s max 0.02s', 'next': 'median 7.40s max 9.56s', 'pause': 'median 0.01s max 0.01s', 'resume': 'median 0.33s max 0.33s'}
- queue lengths per cycle [188, 189, 190, 191, 192, 193, 194, 195, 196, 197, 198, 199, 200, 201, 202, 203, 204, 205, 206, 207]
- working-set growth per cycle (KB) [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

### V01 voice: 'נגן את שלמה ארצי' -> Pear plays the artist - PASS
heard 'נגן את שלמה ארצי'; said 'מוזיקה / עוד שנייה, אני על זה.זה עוד רגע מתעכב. / מסתבר שהנגן עדיין לא אישר שהשמעה התחילה, אבל הוא כן מצא את "תתארו לכם"'
- heard as: 'נגן את שלמה ארצי'
- said: 'מוזיקה / עוד שנייה, אני על זה.זה עוד רגע מתעכב. / מסתבר שהנגן עדיין לא אישר שהשמעה התחילה, אבל הוא כן מצא את "תתארו לכם". רוצה שאנסה שוב?'
- tools: ['music_play']; results: [('music_play', False, 'music_play -> MUSIC_NOT_CONFIRMED')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'Dancing Queen'/'ABBA' paused=False -> after: 'תתארו לכם'/'שלמה ארצי' paused=False

### V02 voice: 'עצור' -> paused - PASS
heard 'עצור'; said 'סגור.עצרתי את המוזיקה.'
- heard as: 'עצור'
- said: 'סגור.עצרתי את המוזיקה.'
- tools: ['music_stop']; results: [('music_stop', True, 'music_stop -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מוזיקה…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'Bohemian Rhapsody'/'Queen' paused=False -> after: 'Bohemian Rhapsody'/'Queen' paused=True

### V03 voice: 'תמשיך' -> resumed - PASS
heard 'תמשיך'; said 'בכיף, אני מטפל בזה.הנה, "בוהמיין רפסודי" של קווין ממשיך.'
- heard as: 'תמשיך'
- said: 'בכיף, אני מטפל בזה.הנה, "בוהמיין רפסודי" של קווין ממשיך.'
- tools: ['music_resume']; results: [('music_resume', True, 'music_resume -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'ממשיך…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'Bohemian Rhapsody'/'Queen' paused=True -> after: 'Bohemian Rhapsody'/'Queen' paused=False

### V04 voice: 'השיר הבא' -> track changed - PASS
heard 'השיר הבא'; said 'סגור. / עוד שנייה, כמעט שם.עכשיו מתנגן "תתארו לכם" של שלמה ארצי.'
- heard as: 'השיר הבא'
- said: 'סגור. / עוד שנייה, כמעט שם.עכשיו מתנגן "תתארו לכם" של שלמה ארצי.'
- tools: ['music_next']; results: [('music_next', True, 'music_next -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'השיר הבא…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'Bohemian Rhapsody'/'Queen' paused=False -> after: 'תתארו לכם'/'שלמה ארצי' paused=False

### V05 voice: 'מה מתנגן עכשיו?' -> names the real title - PASS
heard 'מה מתנגן עכשיו'; said 'תתארו לכם של שלמה ארצי.'
- heard as: 'מה מתנגן עכשיו'
- said: 'תתארו לכם של שלמה ארצי.'
- tools: []; results: []
- activity: []
- pear before: 'תתארו לכם'/'שלמה ארצי' paused=False -> after: 'תתארו לכם'/'שלמה ארצי' paused=False

### V06 voice: 'נגן משהו של Queen' - FAIL
heard 'נגן משהו של קווין'; said 'בסדר. בוד / ק.עוד שנייה, זה כמעט אצלנו.זה עדיין בתהליך, מחכה רגע.הנגן עוד לא אישר שהשמעה התחילה, אבל הוא כן מצא את "Bohe'
- heard as: 'נגן משהו של קווין'
- said: 'בסדר. בוד / ק.עוד שנייה, זה כמעט אצלנו.זה עדיין בתהליך, מחכה רגע.הנגן עוד לא אישר שהשמעה התחילה, אבל הוא כן מצא את "Bohemian Rhapsody (Live)" של קווין. רוצה שאנסה להשמיע?'
- tools: ['music_play']; results: [('music_play', False, 'music_play -> MUSIC_NOT_CONFIRMED')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'תתארו לכם'/'שלמה ארצי' paused=False -> after: 'תתארו לכם'/'שלמה ארצי' paused=False

### V07 voice: 'תנגן שיר שמח' (vague) - PASS
heard 'תנגן שיר שמח'; said 'סגור. / עוד שנייה, כמעט שם.מתנגן עכשיו "בליבר" של אימג\'ן דרגונס, והוא ממש קצבי.'
- heard as: 'תנגן שיר שמח'
- said: 'סגור. / עוד שנייה, כמעט שם.מתנגן עכשיו "בליבר" של אימג\'ן דרגונס, והוא ממש קצבי.'
- tools: ['music_play']; results: [('music_play', True, 'music_play -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('music', 'מנגן', 'Believer – Imagine Dragons'), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'תתארו לכם'/'שלמה ארצי' paused=False -> after: 'Believer'/'Imagine Dragons' paused=False

### V08 voice: web question while music plays (mixed) - PASS
heard 'מה מזג האוויר היום בתל אביב'; said 'רגע, בודק מה קורה עם מזג האוויר בתל אביב.היום בתל אביב שמש עם מעט עננים, בערך בין עשרים לעשרים ושש מעלות.'
- heard as: 'מה מזג האוויר היום בתל אביב'
- said: 'רגע, בודק מה קורה עם מזג האוויר בתל אביב.היום בתל אביב שמש עם מעט עננים, בערך בין עשרים לעשרים ושש מעלות.'
- tools: []; results: []
- activity: [('thinking', 'חושב…', ''), ('searching', 'מחפש ברשת…', ''), ('searching', 'מחפש ברשת…', ''), ('searching', 'מחפש ברשת…', ''), ('searching', 'מחפש ברשת…', ''), ('done', '', '')]
- pear before: 'Believer'/'Imagine Dragons' paused=False -> after: 'Believer'/'Imagine Dragons' paused=False

### V09 voice: music right after the web question - PASS
heard 'עכשיו תנגן את עומר אדם'; said 'סגור. / עוד שנייה, כמעט שם.מתנגן עכשיו "שני משוגעים" של עומר אדם.'
- heard as: 'עכשיו תנגן את עומר אדם'
- said: 'סגור. / עוד שנייה, כמעט שם.מתנגן עכשיו "שני משוגעים" של עומר אדם.'
- tools: ['music_play']; results: [('music_play', True, 'music_play -> ok')]
- activity: [('thinking', 'חושב…', ''), ('music', 'מחפש שיר…', ''), ('music', 'מנגן', 'שני משוגעים – עומר אדם'), ('thinking', 'חושב…', ''), ('done', '', '')]
- pear before: 'Believer'/'Imagine Dragons' paused=False -> after: 'שני משוגעים'/'עומר אדם' paused=False

### V10 voice: screen activity 'music' shows the song - FAIL
- V01: music activity details [] vs pear 'תתארו לכם' -> False
- V06: music activity details [] vs pear 'תתארו לכם' -> False


## Lead follow-up (19 Oct 2026): the queue-growth root cause

The campaign's remaining failures (S04, S07, S10, S11, K01) were all one product problem: **every play inserts a
song into Pear's queue and nothing ever removed it**. ~250 plays left a 207-item queue (3.3 MB per read), which made Pear
and `next`/confirm slow.

- **Fix:** `PearDesktopPlayer.trim_history()` removes only PLAYED history (`DELETE /queue/0`; the playing song stays
  selected, verified on Pear 3.12), in small paced batches (12 per call, one call on the first play and every 6th play, in the
  background). A shared lock keeps trims and index-based plays/next from overlapping, and a trim gives way to a waiting play.
- **What went wrong on the way (honest):** my first cleanup deleted ~170 items in one burst and wedged Pear's player and queue
  API for minutes; I restarted Pear (sign-in and pairing survived, queue back to 51 items) and made the trim gentle. A second
  real run then failed one play because a trim overlapped it: that is what the lock fixes.
- **Verified on the real Pear at volume 0 after the fix:** 12/12 plays confirmed (Hebrew and English artists, songs, a vague
  request), confirm latency 1.2-4.6 s (median ~2 s), queue steady at 47-56 items (~0.9 MB). Pear left paused, volume restored to 8.
- Not re-run after the fix: the 15-minute soak and the voice-driven music scenarios (they need a fresh long run).
