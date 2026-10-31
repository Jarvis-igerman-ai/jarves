# SW-V3 campaign PASS C (voice): bug table (#17, branch `v3-campaign-voice`)

| ID | Sev | Scenario | Finding (evidence) | Status |
|---|---|---|---|---|
| CV-01 | P2 | 29 log_privacy | `log.privacy = true` still logged learned facts in clear text: `CURATOR_DONE ... "facts": ["שם המשתמש: דניאל", "המשתמש גר ברחובות"]`. | FIXED (curator.py); test; #29 PASS on retest |
| CV-02 | P2 | 14 (+38/39/42) | While an approval waits JARVES said "צריך את האישור שלך על המסך" although a spoken "כן"/"לא" is accepted. | FIXED: `CONFIRM_NOTE_VOICE` when the request accepts voice; tests; heard "תגיד כן או לא, או אשר על המסך" |
| CV-03 | P2 | harness 12-14 | Silent suites run with `use_real_audio=True` and opened the REAL always-on-top popup (with a bell) on the owner's desktop. | FIXED: `--test-invoke hold` (invisible real window, never answers) in suite_voice + suite_v3_voice |
| CV-10 | P2 | 38 | After a "לא", the next "clear the list" got "רגע. צריך אישור במסך, תאשר ואני ארוקן אותה" with NO tool call: nothing was pending, the following "כן" went nowhere (silence). | FIXED: live instructions — always delegate; the system asks; never ask for approval yourself. #38 PASS x2 |
| CV-11 | P2 | 41 | Briefing with Google off spoke weather + "אין תזכורות" and skipped calendar/mail silently despite the tool's rule. | FIXED: unavailable parts lead the result text. PASS: "היומן והמייל לא זמינים כי גוגל..." |
| CV-12 | P2 | 43 | "תעבור לפרופיל של דנה": the Hebrew name failed `valid_name` -> "no such profile", and JARVES offered `jarvis profile create dana` though `dana` exists. | FIXED: `spoken_match` (unique consonant match, never guesses on ambiguity) + names listed on failure; test |
| CV-13 | P2 | 43 | Profile switch is documented "on-screen approval, never a spoken yes alone", but the plain confirm accepted voice. | FIXED: `voice=False`; spoken "כן" refused, face Approve works |
| CV-T1 | test | test_profiles | `test_voice_switch_needs_on_screen_approval` failed on v3-final: the approvals 30 s denied-cooldown blocks its immediate re-ask. | FIXED (note_user_turn = the owner's new turn; same fix also landed on v3-final) |
| CV-T2 | harness | 3, 27, 39, 42, 43, 40 | suite_voice rejected "שתיים עשרה" and the valid activity kind `reading`; the V3 approval flow answered in a speech gap, did not model the WhatsApp two-step yes, clicked the screen too late; a spoken "localhost" became "oraclehost". | FIXED in the harness; all retested PASS |
| CV-15 | P2 | 14 | 1 of 4 forget runs: JARVES said "רגע..." and never delegated (no backend request); "אני מחכה למחיקה" when asked later. No watchdog covers "acknowledged but never delegated". | OPEN (intermittent model behaviour; candidate: a short "said רגע, no delegation within N s" nudge) |
| CV-04 | P3 | 22/23 | False wakes 4/20 at 0.15 ("hey Travis", "Jarvis", "hey Jarvie, no", "Hey Charles"), 3/20 at 0.35; = 19 Oct baseline; false wake closes after ~15 s (#31). | OPEN (custom wake model from the owner's samples) |
| CV-05 | P3 | 18 | English question heard correctly, answered in Hebrew ("קנברה."), 3/3 runs (passed 19 Oct on the owner's real profile). A stronger prompt rule did not change it and was reverted. | OPEN |
| CV-06 | P3 | 15 | `LIVE_FALSE_ACTION_CLAIM`: "רשמתי שנועה גרה בחיפה" with no memory tool call. | OPEN (detector caught it) |
| CV-07 | P3 | 45 | Hebrew-script wake phrases by the Hebrew WinRT voice: "היי ג'רוויס" 0/6, "הי ג'ארביס" 2/6; "ok/Okay Jarvis" also wake 12/12 at 0.15. | OPEN (synthetic voice; needs the owner's recordings) |
| CV-08 | P3 | 4 | Plain arithmetic is delegated and the screen shows "searching". | OPEN (UX) |
| CV-09 | P3 | 35 | After barge-in "2+2" was delegated, answered "ארבע", then stale progress notes ("עדיין בודק... עוד לא קיבלתי תשובה") and the answer again; `barge_ins` stat stayed 0. | OPEN |
| CV-14 | P3 | 42 | WhatsApp: two spoken yeses (read-back, then approval) and the "opened the chat" sentence 9 s after the second yes. | OPEN (UX: one confirmation would do) |
| CV-16 | P3 | 46 | Names after the ל prefix written/spoken as "לביטל" / "ליצחי" (ל+אביטל, ל+צחי). Own name and נועה correct in the audio transcript. | OPEN |
| CV-17 | P3 | all | Each delegation sends ~17-20k input tokens (2.69 M over 136 calls): the largest backend cost driver. | OPEN (prompt/tool-list size) |
