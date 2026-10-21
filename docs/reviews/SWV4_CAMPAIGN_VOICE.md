# SW-V4 campaign voice (#17): pass C (20-21 Oct) and pass C2 (21 Oct, credits topped up)

**Pass C2 result (latest valid run of each of the 92 scenarios): 89 PASS, 2 FAIL (22, 23: wake false positives, P3 model
limit, 2/20 vs V3 4/20, unchanged), 1 BLOCKED (62: needs the read-only admin key OD-05, skipped on purpose).** All the
scenarios the empty credits had blocked now ran: 39 retest PASS (V4-VC-3 confirmed live), 51-61 (10 PASS), 63-70 (8 PASS),
71-78 (8 PASS, fake bridge only), 79-84 (6 PASS, 79/80/84 on a re-run), 100-104 (5 PASS), plus 3/24/35 re-runs.
**Live spend of C2: about $2.8** (97.1 - 47.7 = 49.4 min of live voice x $0.05 = $2.47 upper bound, plus about $0.3 of
backend tokens and web search); the account had credit (no `credit_balance_exhausted`). The owner's ChatGPT token was only
**copied** for scenario 56 and never refreshed (the harness no longer refreshes the real account). Key never printed.
Bugs found and fixed in C2: V4-VC-12 (product, P2), H-C2-1 (harness), V4-VC-13 (product wording, P3), V4-VC-14
(product wording, P3), plus 3 harness timing defects in 67-69 / 75 / 77. All in [bug table](SWV4_CAMPAIGN_BUG_TABLE_VOICE.md).

## Pass C (20 Oct) summary, kept for history

**Result: 50 PASS, 3 FAIL, 39 BLOCKED of 92 scenarios** (latest valid run of each). FAILs: 22/23 wake false positives
(P3, model limit, better than V3), 39 helper approval (V4-VC-3, fixed, live retest blocked).
**BLOCKER: the owner's OpenAI account ran out of credits at 21:08 UTC 20 Oct** (`credit_balance_exhausted` on every
GPT-Live start). Every live scenario after that (delegation 51-62, orchestrator 63-70, WhatsApp 71-78, recall 79-84
incl. the 80 re-run, self-diagnostics/approval/WhatsApp 100-104, and the live retests of the fixes) is BLOCKED until
credits are added. Scenarios 63-70 and 100-107 are written and ready (`core/tools/suite_v4_campaign_c_new.py`).
**Live spend of this pass: at most $2.45** of the $8 slice (live voice wall time 44.6 min x $0.05 = $2.23 upper bound,
which also counts the failed connects after the credits ran out; backend 117 calls, 2.23 M input tokens of which
80 % cached, priority price x2 <= $0.20; 7 web searches; 3 output-audio transcriptions ~$0.01). Key never printed.
Bugs: 5 P2 fixed (1 harness + 4 product; V4-VC-4 also retested live), 0 P0/P1, 9 P3 open. A probe on 21 Oct 00:14 UTC (after merging integration head fd937bc) found the credits still empty; that blocked probe overwrote scenario 3's raw-report row (the valid 20 Oct run was PASS) — [bug table](SWV4_CAMPAIGN_BUG_TABLE_VOICE.md).

## Method
`core/tools/suite_v4_campaign_c.py` = every existing scenario (suite_voice 1-35, suite_v3_voice 36-50, delegation
51-62, WhatsApp 71-78, recall 79-84) + the new campaign scenarios (63-70, 100-107) in ONE store / raw report
([swv4_campaign_voice/SUITE_RAW_REPORT.md](swv4_campaign_voice/SUITE_RAW_REPORT.md)). Same harness as V3: real Core,
real GPT-Live (`gpt-live-1` -> `gpt-6-luna`, priority), virtual mic (WinRT speech, never played), virtual speaker
(measured, never played), fake face, real openWakeWord / trained head in the Core wake loop, invisible approval popups,
`webbrowser.open` and process spawns recorded. Every home is the TEST profile with only what a scenario needs switched
on. One Core and one audio session at a time. Fakes: Claude Code host + fake `claude`/`codex` (tests/fakes),
WhatsApp bridge (tools/fake_whatsapp_bridge.py), local Responses servers (tests/openai_fakes.py).

## Results

| # | Scenario | Result | Evidence / key number |
|---|---|---|---|
| 1 | greeting | PASS | first sound 0.52 s after the face connects, greeting 2.2 s |
| 2 | smalltalk | PASS | 3/3, dead air avg 0.45 s |
| 3 | time_date | PASS | C2 re-run: hour + day right, no delegation (the C row was overwritten by a credit-blocked probe) |
| 4 | maths | PASS | 3/4 (`calculate`); a later retest ran into the credit stop (BLOCKED) |
| 5-7 | web: weather / news / USD | PASS | backend 3.2 / 7.0 / 7.1 s, screen "searching" |
| 8-10 | emotions | PASS | laugh / sad+cry / love |
| 11 | memory_recall | PASS | after V4-VC-1 (first run: harness could not read tool names) |
| 12-14 | forget approve / deny / timeout | PASS | deleted / kept / kept; spoken hint 2.3 s during the wait |
| 15 | long conversation 26 turns | PASS | 26/26 answered, 9/10 correct, 0 disconnects, context kept, voice 348 s vs wall 338 s |
| 16 | rapid fire | PASS | 6/6 in order, dead air 0.5-1.43 s |
| 17 | long utterance 40.6 s | PASS | Eilat, dead air 0.5 s |
| 18 | english switch | PASS | "Canberra" in English, Hebrew back in Hebrew (V3 FAIL fixed by CV-05 holds) |
| 19 | noise 40 s | PASS | 0 s spoken, 0 delegations |
| 20 | idle disconnect | PASS | ended 14.8 s after the last sound |
| 21 | wake word x3 | PASS | 3/3, scores 1.0, connect 2.8-3.4 s |
| 22 | wake false positives | **FAIL** (P3) | 2/20 at 0.15 (V3: 4/20) — CV-04 |
| 23 | wake thresholds | **FAIL** (P3) | detect 10/10 at 0.15 and 0.35; false 2/20 both (V3: 4/20, 3/20) |
| 24 | software mute / unmute | PASS | C2 re-run: live ended, mics closed, muted wake lost, unmute wake + answer |
| 25-26 | voice / request budget | PASS | VOICE_BUDGET after 63 s; AI_BUDGET after 2 requests; 0 reservations left |
| 27 | face protocol | PASS | 30 states, bad [] |
| 28 | start/stop x5 | PASS | 5/5 clean |
| 29 | log privacy | PASS | 18 content words checked, 0 leaked; key in 0 of 9 homes |
| 30 | idle soak 6 min | PASS | 388 s, 23 WAKE_HEALTH, 0 false wakes, then wake + answer |
| 31 | false wake closes | PASS | closed 14.8 s after a false wake |
| 32 | privacy strip live | PASS | mic muted 0.06 s, cloud off 2.84 s (V3 0.18 / 2.94) |
| 33 | camera-off honesty | PASS | honest, 0 camera tools, strip camera "disabled" |
| 34 | calendar not connected | PASS | no claim, `google_connect_help` |
| 35 | barge-in (half_duplex off) | PASS | C2 re-run: old speech stopped **0.51 s** after the interruption, "4" answered, barge_ins 1 |
| 36 | timer, face closed | PASS | TIMER_RANG 2.2 s after due, "הטיימר צלצל." |
| 37 | alarm, no session | PASS | rang 0.6 s after due, Core speech "שעון מעורר: השכמה" |
| 38 | lists + clear approval | PASS | "לא" denied via voice 3.07 s, "כן" approved via voice 3.91 s; popup+face+voice; dialogs closed |
| 39 | helper command approval | PASS (C2) | V4-VC-3 retest: `shell_propose`, approved by voice, audit 1 (ran 1), user named |
| 40 | browser local page | PASS | open + read ok, "אבטיח" |
| 41 | briefing without Google | PASS | weather + "היומן והמייל לא זמינים" |
| 42 | WhatsApp / Telegram handoff | PASS | one spoken yes, 1 wa.me link (recorded), Telegram honest |
| 43 | profile switch | PASS | retest: "לא" denied, spoken "כן" refused, screen approve spawned `dana run --background --show` (first run lost the 2nd request: harness timing H-43) |
| 44 | self-check by voice | PASS | SELFCHECK_OK, 10.3 s summary; it reported the real V4-VC-5 crash |
| 45 | wake variants | PASS | Hey Jarvis normal/quiet 8/8; Hi/OK 24/24; Hebrew-voice "היי ג'רוויס" 0/12 (as V3) |
| 46 | names pronunciation | PASS | output-audio transcript "אני ג'ארוויס ... נועה, אביטל, צחי" 3/3 |
| 47 | greeting delay | PASS | 0.51 / 0.52 / 0.50 s |
| 48 | redelegate probe | PASS | 2 hand-overs, remember + forget, approved via voice |
| 49 | told, not asked | PASS | 3/3 answered, memory 0 -> 0, no claim |
| 50 | names after prefix | PASS | "שלום לנועה, לאביטל ולצחי" 2/2 (V3: partly; CV-16 now confirmed) |
| 51-61 | client delegation | 10 PASS, 1 fixed | 51-55, 57-61 PASS (spent $0.01-0.04 each, 0 open reservations); 56 (plan route) FAILED first on **V4-VC-12** (`BackendTrace.finish` TypeError: duplicate `model`), fixed, then PASS: route chatgpt_plan, 1 plan response, API USD 0 |
| 62 | delegation cost reconciliation | BLOCKED | needs the read-only admin key (OD-05); skipped by instruction |
| 63-70 | orchestrator by voice | 8 PASS | 63 list, 64 status, 65 ask, 66 tell (spoken yes approves), 67 start (spoken yes refused, screen approves, beta session exists), 68 stop, 69 risky permission (medium + high: spoken yes refused, spoken no denies, worker got deny), 70 digest. 67-69 first failed on harness defects (shared Core, no session to stop, spoken no said while JARVES was still talking): fixed |
| 71-78 | WhatsApp (fake bridge only, no real linking) | 8 PASS | inbox, chat, summarize, injection (no action), long text (not read aloud, screen offered; asked in English), read-mode reply (draft only), send-mode reply (compose + send, exact text), mode by voice (stays read). First run 2/8: **H-C2-1 harness**, the WhatsApp rule joins the live session at its start but the suite switched the feature on afterwards: now the live session is restarted after the switch |
| 79-84 | recall (incl. the 80 re-run) | 6 PASS | 81, 82, 83 PASS first time; 79 / 80 / 84 failed once on synthetic-ASR clips ("מה יוסי", "about the" dropped; 4/5) and passed on re-run: 79 5/5 p95 0.82 s, 80 5/5 p95 1.48 s, 84 RECALL_NOTHING after deletion |
| 100-102 | self-diagnostics | 3 PASS | **V4-VC-13**: "מה מצבך?" was answered as small talk (no tool). Fixed: persona delegates own-health questions + a health line in the backend instructions. 100 `health_summary` 7.4 s Hebrew, 101 English 5.2 s, 102 `health_ai_review_summary` honest "has not run" |
| 103 | approval wording (kind + first name, one language, English request) | PASS | spoken_in en "Send a WhatsApp message to ...?", no number / text, spoken no denies |
| 104 | WhatsApp "Did anyone message me?" + send tools absent in read mode | PASS | after the V4-VC-9 harness fix and an English phrase in BACKEND_RULE (the backend picked `whatsapp_status`): `whatsapp_inbox`, four messages by name, no text leaked, outbox 0 |
| 105 | popup keyboard rules (real Tk window, own process, invisible, bell muted) | PASS | Enter before arming: nothing; Enter armed medium: approve; Enter / KP_Enter on HIGH with the right code typed: nothing; Escape: deny (high + medium) |
| 106 | pronunciation dictionary default | PASS | `DEFAULT_ENTRIES {}`, the fresh home's `workspace/PRONUNCIATIONS.md` parses to {}, spoken text unchanged |
| 107 | wake: owner's custom head (a COPY) vs built-in, Hey/Hi/OK Jarvis x 3 volumes x clean+noise + 20 near-misses | PASS | head `hey_jarvis_copy.npz@0.95`: Hey 10/12, Hi 12/12, OK 6/6, Okay 6/6, false 1/20; built-in @0.15: same detections, false 2/20 (both through the wake ASR-confirm stage; synthetic voices only) |
| — | assisted dialing (H) | BLOCKED | not in the integrated tree (no dialing / Phone Link code) |

### Latency vs V3 ([SWV3_CAMPAIGN_VOICE.md](SWV3_CAMPAIGN_VOICE.md), [SWV3_P03_REPORT.md](SWV3_P03_REPORT.md))

| Measure | V4 (this pass) | V3 |
|---|---|---|
| Dead air (question end -> first sound), all 83 measured turns | median 0.54 s, p90 1.38 s, **p95 1.44 s** (budget ≤ 1.5 s: met), max 1.65 s | typical 0.1-0.9 s; P03 real-log end-of-speech -> audio median 1.78 s, p90 2.52 s |
| Dead air, small talk / long conversation / rapid fire | median 0.50 s (n 35); small-talk avg 0.45 s | avg 0.14-0.30 s |
| First audio of the greeting after the face connects | 0.50-0.52 s (= `greeting_delay_s` 0.5) | 0.51-0.53 s |
| Wake -> session connected | 2.8-3.4 s | 2.5-3.5 s |
| Barge-in: old speech stopped | 0.6 s | 0.7 s (0.5 s after CV-09) |
| Spoken approval word -> decision | 3.07-3.91 s | 3.4-3.6 s |
| Privacy strip: mute -> no mic audio / cloud off | 0.06 s / 2.84 s | 0.18 s / 2.94 s |
| Web lookup backend | 3.2-7.1 s | 2.5-7.8 s |

Small-talk dead air is about 0.2-0.3 s higher than V3's average; p95 stays inside the 1.5 s budget. No dead-air
outlier above 1.65 s in 83 turns.

## Log review (every WARN/ERROR of all campaign homes)

| Code | Count | Class | Reason |
|---|---|---|---|
| LIVE_ERROR `credit_balance_exhausted`, LIVE_CONNECTION_LOST, LIVE_START_FAILED, LIVE_BACKEND_STALL/RETRY_FAILED, LOOKUP_ATTEMPT_FAILED, LIVE_RETRY_BACKEND_DONE | 28 / 25 / 25 / 12 | expected (external) | account credits ran out at 21:08 UTC; V4-VC-4 makes it end honestly |
| LIVE_ERROR "session is closing" | 2 | noise | V4-VC-6 (P3) |
| LIVE_STOP_FAILED, LIVE_CLOSE_UNCONFIRMED | 1 / 1 | bug | V4-VC-5 (fixed) |
| CRASH (closed database in a background task) | 1 | bug | V4-VC-5 (fixed) |
| LIVE_ANSWER_UNDELIVERED | 6 | bug | V4-VC-2 (fixed) |
| TOOL_UNTRUSTED_TURN | 1 | bug | V4-VC-3 (fixed); wording V4-VC-7 (P3) |
| LIVE_EXTRA_REJECTED `context_injection_incomplete` | 3 | expected | a note being injected while the harness stopped the Core |
| AI_BUDGET | 3 | expected | scenario 26 by design |
| TOOL_RESULT `PROFILE_SWITCH_NOT_APPROVED` (warn) | 2 | expected | scenario 43 denials |

Counts: expected 9 codes, bug 6 codes (all fixed or listed), noise 1. Compared with V3: `LINK_FACE_DISCONNECTED` is
no longer a warning; `LIVE_GREETING_NUDGE` and `LIVE_FALSE_ACTION_CLAIM` did not occur.

## Fixes on `test/v4-campaign-c` (focused tests, one pytest at a time)
- V4-VC-1 harness: tool / result / event / end-reason parsing from the V4 log fields (6 tool scripts).
- V4-VC-2 `voice/live.py` `answer_said`: short answers delivered once — `test_voice_fixes_cv.py`, `test_swv3_p01.py` 43 passed.
- V4-VC-3 `skills/__init__.py`: an identical approved / tainted repeat in the same turn is replayed, never re-run or
  re-asked; a new user turn ends it — `test_helper_approvals.py` 65 passed, with `test_review_c.py`,
  `test_whatsapp_injection.py`, `test_swv3_p01.py`: 125 passed.
- V4-VC-4 / V4-VC-5 `voice/live.py`: credits error fatal with a billing action; bounded transport exit + guarded
  settle — `test_voice_faults.py` 44 passed. `test_log_message_lint.py` 19 passed.
- New tools: `suite_v4_campaign_c.py`, `suite_v4_campaign_c_new.py`, `popup_keys_check.py`.

## Fixes in pass C2 on `test/v4-campaign-c2`
- V4-VC-12 `brain.py` `BackendTrace.finish`: the plan route (ChatGPT) passed `model` twice (constructor id + response
  model) -> `TypeError` -> every delegated turn on the plan route failed AFTER the answer had arrived (scenario 56). Merged.
- V4-VC-13 `voice/live.py` persona + `diag/service.py` instruction: own-health questions are delegated (`health_summary`).
- V4-VC-14 `whatsapp_bridge.py` BACKEND_RULE: "did anyone message me" -> `whatsapp_inbox`, not `whatsapp_status`.
- H-C2-1 harness: `restart_live` after switching WhatsApp on; 67-69 own Core, a started beta session for 68, silence before the
  spoken "לא"; 56 never refreshes the real ChatGPT token; 75 English; 77 restart after enabling send.
- Wake 22/23 (CV-04): unchanged P3, same two near-miss phrases ("Jarvis", "hey Jarvie, no") pass the ASR confirm.

## Not verified / next steps (C2)
1. 62 (cost reconciliation) needs the read-only admin key (OD-05). Dialing (H) is not in the integrated tree.
2. Real microphone / speaker / room, the owner's real voice for the custom wake head, the visible popup.
3. 79 / 80 / 84 / 101 are model / ASR sensitive (one miss in some runs): worth a second look in a soak, not a blocker.

## (superseded) Not verified / next steps of pass C
1. **Owner: add OpenAI credits** (platform.openai.com billing), then run, in this order (about $4-5 live, within the
   remaining slice): `python tools/suite_v4_campaign_c.py --only 4,24,35,39,38 --max-minutes 10` (retests of
   V4-VC-2/3), `--only 51-61 --max-minutes 18`, `--only 63-70,100-104 --max-minutes 16`, `--only 71-84 --max-minutes 20`.
2. 62 needs the read-only admin key (OD-05). Dialing (H) is not implemented in the integrated tree.
3. Real microphone / speaker / room, the owner's real voice for the custom wake head, the visible popup.
