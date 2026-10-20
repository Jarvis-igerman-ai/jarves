# SW-V3 campaign PASS C (voice): bug table (#17, branch `v3-campaign-voice`)

| ID | Sev | Scenario | Finding (evidence) | Status |
|---|---|---|---|---|
| CV-01 | P2 | 29 log_privacy | `log.privacy = true` still wrote the learned facts in clear text: `CURATOR_DONE ... "facts": ["שם המשתמש: דניאל", "המשתמש גר ברחובות"]` (curator.py). | FIXED: facts omitted when privacy is on; test `test_curator_facts_are_not_logged_in_privacy_mode` |
| CV-02 | P2 | 14 forget_timeout (+38/39/42/43) | While an approval waits, JARVES says "צריך את האישור שלך על המסך" although a spoken "כן"/"לא" is accepted (approvals voice channel); the owner is not told he can answer by voice. | FIXED: `CONFIRM_NOTE_VOICE` used when the request accepts a spoken answer (`confirm_pending(prompt, voice=a.voice_ok)`); tests in `test_campaign_voice_fixes.py` |
| CV-03 | P2 | harness (suite_voice 12-14) | The "silent" suite runs with `use_real_audio=True`, so every approval opened the REAL always-on-top popup with a bell on the owner's desktop. | FIXED: `--test-invoke hold` (invisible real window that never answers) used by suite_voice.py and suite_v3_voice.py |
| CV-04 | P3 | 22/23 wake false positives | 4/20 false wakes at 0.15 ("hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles"), 3/20 at 0.35. Same as the 19 Oct baseline; a false wake closes after ~12-15 s (#31 PASS). | OPEN (model limit; custom wake model from the owner's ~20 samples) |
| CV-05 | P3 | 18 english_switch | English question heard correctly ("What is the capital city of Australia") but answered in Hebrew ("קנברה."). live.py unchanged vs main; passed 19 Oct: model nondeterminism. | OPEN (retest result below) |
| CV-06 | P3 | 15 long_conversation | `LIVE_FALSE_ACTION_CLAIM`: "רשמתי שנועה גרה בחיפה" with no memory tool call (the fact was only in the session context). | OPEN (prompt-level; detector caught it) |
| CV-07 | P3 | 45 wake_variants | Hebrew-script phrases by the Hebrew WinRT voice: "היי ג'רוויס" 0/6, "הי ג'ארביס" 2/6 (an English "hey jarvis" by the same Hebrew voice 6/6). "ok jarvis" / "Okay Jarvis" also wake (6/6) at 0.15. | OPEN (synthetic-voice limit; needs the owner's real recordings) |
| CV-08 | P3 | 4 maths | Plain arithmetic is delegated to the backend and the screen shows "searching" for it. | OPEN (UX) |
