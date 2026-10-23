# SW-V3 P0.4 — installed-system QA with virtual microphone / speaker: pass/fail matrix and bug table

Branch `v3-p04-qa` (from `swv2-integrate` @ `e95eb6f`), Windows 11 PC, 20 Oct 2026 07:20-08:40 local. Owner of this
table: the P0.4 QA agent. Dashboard UI and performance findings belong to P0.2 / P0.3 and are only referenced.

## Evidence classes (never mixed up)

| Class | Meaning here |
|---|---|
| **real** | real Core process, real link/dashboard HTTP/CLI/SQLite, real GPT-Live / backend / web search / ChatGPT plan, real openWakeWord |
| **virtual** | virtual microphone (WinRT-synthesised Hebrew/English speech, never played) and virtual speaker (measured, never played); virtual camera = the Core's own `FakeCamera` test pattern; a stand-in face client on the real link |
| **mock** | in-memory transports (`suite_music` errors/robust groups only) |
| **not tested** | physical mic/speaker/camera, installed package, Pi, Google account, Pear Desktop (closed during the run) |

A virtual-microphone run is **not** a test of the physical microphone or speaker.

## Pass/fail matrix

Commands (from the repo root): `uv --directory core run --no-sync python tools/suite_system.py [--v2-only]`,
`... tools/suite_voice.py --only N,...`, `... tools/suite_music.py --groups errors,robust`, `... pytest -q`.

| Area | Check | Result | Key number | Evidence | Commit |
|---|---|---|---|---|---|
| offline tests | pytest (all) | PASS | 1138 passed, 3 skipped (5 min 17 s) | unit/mock | 0600528 |
| system | S02/S03 Godot viewer + face headless, malformed protocol | PASS | viewer PASS, face PASS | real Godot headless | e95eb6f |
| system | S04 `jarvis doctor` on the user's state dir | PASS | all checks | real | e95eb6f |
| system | S05/S06 12 CLI commands, cross-process memory | PASS | all ok | real CLI | e95eb6f |
| system | S07 bad configs refused (5 kinds) | PASS | rc 2 CONFIG ERROR | real CLI | e95eb6f |
| system | S08 Core start/stop x5, port freed | PASS | ready 2.0-2.6 s | real | e95eb6f |
| system | S09 API key never in logs/temp/diag/repo | PASS | 27 736 files, 0 hits | real | e95eb6f |
| system | S10 diag bundle masks conversation text | PASS | 0 unmasked | real | e95eb6f |
| system | S11 log rotation bounded | PASS | 4 files, 60 KB | real | e95eb6f |
| system | S12 ChatGPT-plan text path, 5 questions | PASS | 1.6-10.4 s per answer | real (user's plan) | e95eb6f |
| system | S13 3-min idle soak, wake word listening | PASS | 103->88 MB, CPU peak 15 % | virtual mic | e95eb6f |
| **V2** | V01 privacy strip at idle: truthful state, heartbeat, = /status | PASS | wake_only / disabled / off / quiet; heartbeat 2 in 11 s | real Core + virtual mic | 5f1de61 |
| **V2** | V02 strip mute: muted in < 2 s, every mic closed, unmute -> wake_only | PASS | 0 open mics while muted | real + virtual | 5f1de61 |
| **V2** | V03 features: CLI off -> Core refuses (FEATURE_OFF), dashboard shows it, dashboard on -> runs | **FAIL -> PASS** | was `unknown feature 'notes'` rc 2 (Q01) | real CLI + HTTP | 845b975 |
| **V2** | V04 camera preview: refused while off, on-screen approval (deny/approve), frames + strip `preview`, timeout close, preview switch gate, nothing on disk | PASS | 6 frames 320x240 in 3 s; closed `timeout`; 0 image files | real Core, **virtual camera** | 5f1de61 |
| **V2** | V05 calendar guardrails, Google not connected (no fake Google) | PASS | model `calendar_create_event` -> TOOL_UNKNOWN; connect help GOOGLE_NOT_CONNECTED; calendar_write inactive; CLI rc 1 no traceback; DELETE/cancel/move/ACL refused, create allowed | real | 5f1de61 |
| **V2** | V06 scheduler via dashboard: parse, draft inert, run now, switch off holds runs, due reminder + report + alert | FAIL (Q04) -> PASS | run now started **26.1 s** after the click; after the fix **0.0 s**, due reminder 0.1 s after its time | real HTTP + SQLite | 845b975 |
| **V2** | V07 scheduler across a real Core restart: missed job runs once at start-up, labelled late, earlier job not repeated | PASS | catch_up, late=1, 132.5 s after due | real (2 processes) | 5f1de61 |
| **V2** | V08 V2 Core stops cleanly | PASS | 2.1 s, port free | real | 5f1de61 |
| voice | 1 greeting, 2 small talk, 3 time/date, 4 maths, 5 weather, 8 joke, 11 memory, 12-14 forget approve/deny/timeout, 16 rapid fire, 17 long utterance (40 s), 18 English switch, 26 request budget, 27 face protocol, 31 false wake closes | PASS (15/15) | dead air 0.22-0.5 s; budget AI_BUDGET at 2 requests | real GPT-Live + virtual mic/speaker | e95eb6f |
| voice | 22 wake false positives at threshold 0.15 | FAIL (known) | 4/20 (`hey Travis`, `Jarvis`, `hey Jarvie`, `Hey Charles`) - same as 19 Oct | real openWakeWord, virtual mic | e95eb6f |
| voice | 24 software mute | FAIL then PASS | first run: startup failed, no greeting (Q05) | real + virtual | e95eb6f |
| voice | 25 voice budget (1 min) | FAIL -> PASS | first run: 1 reservation still held right after stop (Q06); after the fix 0 | real + virtual | 0600528 |
| voice | 28 Core start/greeting/stop x5 | FAIL -> PASS | cycle 4: memory.db locked after stop (Q06); after the fix 5/5 clean | real + virtual | 0600528 |
| **voice V2** | 32 privacy strip in a live session (listening+sending, speaking, gated, mute -> no mic audio, then cloud off) | PASS (3rd run) | muted 0.15 s after the click, cloud off 2.94 s; run 1 too strict a criterion (test fixed), run 2 hit Q05 | real + virtual | 845b975 |
| **voice V2** | 33 camera off: honest "what do you see?" | PASS | no camera tool, no false claim, strip disabled | real | 5f1de61 |
| **voice V2** | 34 Google not connected: calendar add never claimed | PASS | `google_connect_help` called, no "הוספתי" | real | 5f1de61 |
| **voice V2** | 35 barge-in with half_duplex = false | PASS | old speech stopped 0.53 s after the interruption; 2+2 answered | real + virtual | 5f1de61 |
| music | E01, E03, R01-R04 (mock transports) | PASS (6/6) | | mock | 5f1de61 |
| music | E02 unpaired id at the real port | BLOCKED | Pear Desktop not running | — | 5f1de61 |
| music | S01-S13 skill, V01-V10 voice, K01 soak (real Pear) | NOT RUN | Pear Desktop closed (user's app; not started by the agent) | — | — |
| install | `jarvis setup --check-addons` from source | PASS | ADDON CHECK: PASS | real | e95eb6f |
| install | packaged installer / clean setup | NOT RUN | JARVES is not installed on this PC; installing changes PATH/autostart: owner gate | — | — |

Live voice spent in this QA: 9.6 + 2.4 + 1.3 + 2.3 = 15.6 min (conservative wall time) ≈ $0.78 plus backend tokens.

## Bug table

| # | Sev | Found by | User impact | Root cause / evidence | Owner | Fix | Regression test | Status |
|---|---|---|---|---|---|---|---|---|
| Q01 | P2 | V03 | `jarvis features off scheduler` (or notes, files, backup, proactive_alerts, appearance, skill_creation, ...) said "unknown feature" (rc 2) and `features list` omitted them, although the dashboard and the Core show them: CLI parity gap (AGENTS "Feature setup and CLI access") | Add-on modules register their switches on import; a fresh CLI process never imported them | cli.py | `_cli_features` loads the add-on modules (`setup_wizard.load_addons`), the same list as the wizard | `test_swv3_p04.py::test_q01_*` (fresh process) | fixed, re-verified in V03 |
| Q04 | P2 | V06 | Dashboard "Run now" / approve / edit took effect only at the Core's next 30 s poll (measured 26.1 s): looks broken | The dashboard opened from the Core built its own `Scheduler`; its `poke()` never reached the Core's loop | app.py, scheduler.py | Core shares its scheduler with its dashboard; `poke()` is thread-safe (`call_soon_threadsafe` from the HTTP thread) | `test_q04_*` (fails without the fix) | fixed, re-verified live (V06: 0.0 s); a dashboard in ANOTHER process (`jarvis dashboard` while the Core runs elsewhere) still waits up to 30 s (by design, cross-process) |
| Q05 | P1 | voice 24, 32 | "Startup failed; the room remains closed" in 2 of 14 live starts: GPT-Live sent nothing after `session.started` for 15 s | Server-side: the greeting response never started (no events at all, then only `session.usage.updated`); same symptom as the 19 Oct suppressed greeting | voice/live.py | After 6 s without greeting audio send ONE silent clock note (what un-stuck it on 19 Oct), keep waiting to 15 s; logged `LIVE_GREETING_NUDGE` | `test_q05_*` (fake server) | accepted (owner 23 Oct 2026, #89: fixed in code, live check later): mitigation in; **live effect UNVERIFIED**: the 13 later starts (incl. 5x suite 28) all greeted without needing the nudge (0 `LIVE_GREETING_NUDGE`); watch for the code in real logs |
| Q06 | P2 | voice 25, 28 | After "Core stopped cleanly" a paid AI-review request was still running: budget reservation held, memory.db locked (rename failed in suite 28 cycle 4), DB writes after the stop | Stopping ends the conversation, the history sink then started the AI review in a daemon thread; `StoreSink.close()` joins it only 3 s (OpenAI timeout 30 s). Regression of SW-V2 WP-H (AI review on by default) | app.py, store.py | Core turns AI reviews off at CORE_STOPPING (like the curator, which already goes rules-only at shutdown); the rule-based diagnosis still runs; the dashboard Review button can run the AI pass later | `test_q06_*` (2) | fixed; suite 25 + 28 PASS after the fix (0 reservations, 5/5 db released). Trade-off: a conversation ended by the shutdown gets no automatic AI review |
| Q02 | P3 | suite_music | Music suite crashed at start when Pear is closed, even for mock-only groups | `Safety.start()` always contacted real Pear | tools/suite_music.py | Mock-only groups never touch Pear; they never rewrite the real-Pear report | run itself | fixed (test code) |
| Q03 | P3 | suite_music E02 | E02 FAIL looked like a product bug while Pear was closed | Scenario needs the real Pear port | tools/suite_music.py | Reports `BLOCKED: Pear Desktop not running` | run itself | fixed (test code) |
| Q07 | P3 | voice 22 | 4/20 false wakes at the user's threshold 0.15 on English near-misses | openWakeWord `hey_jarvis` model limit (unchanged since 19 Oct); Hebrew never triggers; a false wake costs ~12 s of voice (31 PASS) | wake model | No code change; custom wake model / threshold choice is the real fix | suite 22/23 | known, open |
| Q08 | P3 | V06 | Two reminders due in the same scheduler tick are spoken one per alert flush; the second waits for the next tick (up to 30 s) | `AlertEngine.flush` delivers one speech alert at a time | operations.py / alerts.py | Not changed (prevents talking over itself); noted for the alerts owner | V06 alert rows | observation |
| — | — | S12 | Answer "ראש ממשלת בריטניה כרגע הוא אנדי ברנהאם" not verified | web facts are not checked by the suite | — | — | — | UNVERIFIED fact |

Final full system run on 0600528: **20/20 PASS** (S02-S13 + V01-V08), report regenerated. Not bugs after checking:
V06 due reminder 26.3 s late in the first run was caused by the test itself (the scheduler switch was OFF around the
due time); privacy strip `idle`+`connected` before the room is presented is correct (GPT-Live
gets generated silence until then, docs/FACE_RUNTIME.md).

## Coverage gaps (not tested, with the reason)

- Physical microphone, speaker and camera; the installed/packaged app and clean setup on a second PC (owner gate:
  installing changes PATH/autostart); Raspberry Pi; Google account flows (no fake Google by request: only the
  not-connected path); real Pear Desktop (closed); paid background-task research jobs (scheduler `research` action);
  the Dashboard UI itself (P0.2) and startup/perf budgets (P0.3).
