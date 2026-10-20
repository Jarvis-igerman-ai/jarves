# Pre-use test report: system (silent, headless)

Run: 20 Oct 2026 08:40 on this PC. 20/20 passed. Duration 809 s.

| Test | Result | Detail |
|---|---|---|
| S02 Godot viewer + face self-tests (headless) | PASS | {'viewer': 'PASS', 'face': 'PASS'} |
| S03 real face survives malformed protocol messages | PASS | 2 passed in 13.41s |
| S04 jarvis doctor (key, ChatGPT, mic, wake model, Pear) | PASS | All checks passed. |
| S05 CLI commands run cleanly (12 commands, offline profile) | PASS | all ok |
| S06 memory written by one process is read by the next (persistence) | PASS |  |
| S07 bad configs are refused with a clear error (5 kinds) | PASS | all rc=2 CONFIG ERROR |
| S08 Core start/stop x5: ready, clean stop, port freed | PASS | ready in [2.6, 2.0, 2.0, 2.0, 2.0] s |
| S10 `jarvis diag` support bundle masks conversation text | PASS | 500 lines, 0 unmasked text fields |
| S11 log rotation is bounded (20 KB x 4 files max) | PASS | 4 files, 60204 bytes |
| S12 ChatGPT-plan text path (real: 5 questions via the user's plan) | PASS | all answered |
| S13 3-minute idle soak with the wake word listening (virtual mic) | PASS | memory 102.9->70.1 MB, peak CPU 14.9%, log 12 KB, wake ok |
| V01 privacy strip at idle: truthful state, heartbeat, same as /status | PASS | first msg True; mic wake_only, camera disabled, cloud {'on': False, 'state': 'off'}, speaker quiet; 3 msgs in 11 s idle |
| V02 privacy strip mute: muted shown within 2 s, every mic closed, unmute -> wake_only | PASS | muted shown True, open virtual mics while muted 0, back True |
| V03 features: CLI off -> Core refuses the tool (FEATURE_OFF); dashboard shows it; dashboard on -> tool runs; unknown id refused | PASS | tool note_add; cli off rc 0, core saw off True, call -> FEATURE_OFF; dashboard row enabled False by cli; dashboard on 200, core saw on True, call -> TOOL_BAD_ARGS; unknown id rc 2 |
| V04 camera preview (virtual camera): refused while off, approval on screen, frames + strip 'preview', timeout close, switch gates, nothing on disk | PASS | preview while camera off -> CAMERA_DISABLED; enable: denied -> on False (asked True, requested True); approved -> on True active True ; preview: open True, strip 'preview' True, frames 6 (320x240), cl |
| V05 calendar guardrails, Google not connected: no calendar tool, honest connect help, calendar_write stays inactive, CLI fails cleanly, delete/cancel/ACL refused | PASS | model calendar_create_event -> TOOL_UNKNOWN; google_connect_help -> ok False GOOGLE_NOT_CONNECTED; calendar_write default enabled False active False; cli on calendar_write rc 1 -> enabled False active |
| V06 scheduler via dashboard: parse, draft inert until approved, run_now, scheduler switch off holds runs, due reminder runs on time with report + alert | PASS | parse 'כל יום ב-8' -> 200 0 8 * * *; draft runs before approval 0; approved active/active/active; run_now done True started 0.0 s after the click; scheduler off: run_now ran 0 time(s) in 35 s; on agai |
| V07 scheduler across a Core restart: job due while JARVES was off runs once at start-up, labelled late; the earlier job is not repeated | PASS | privacy.state at reconnect True; missed job ran True trigger catch_up late 1 (131.9 s after due); earlier job runs 1; job {'status': 'done', 'last_outcome': 'done'}; report late [1] |
| V08 V2 Core stops cleanly after the feature run (CORE_STOPPED, port freed) | PASS | CORE_STOPPED True after 2.1 s, port free True |
| S09 API key never appears in logs, temp state, diag bundle or repo | PASS | scanned 27979 files, key hits 0, diag hit False, repo patterns 0 |

Run it again: `uv --directory core run --no-sync python tools/suite_system.py`.
