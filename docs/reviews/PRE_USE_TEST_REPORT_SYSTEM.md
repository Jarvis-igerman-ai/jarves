# Pre-use test report: system (silent, headless)

Run: 19 Oct 2026 07:42 on this PC. 11/11 passed. Duration 320 s.

| Test | Result | Detail |
|---|---|---|
| S02 Godot viewer + face self-tests (headless) | PASS | {'viewer': 'PASS', 'face': 'PASS'} |
| S03 real face survives malformed protocol messages | PASS | 1 passed in 7.28s |
| S04 jarvis doctor (key, ChatGPT, mic, wake model, Pear) | PASS | All checks passed. |
| S05 CLI commands run cleanly (12 commands, offline profile) | PASS | all ok |
| S06 memory written by one process is read by the next (persistence) | PASS |  |
| S07 bad configs are refused with a clear error (5 kinds) | PASS | all rc=2 CONFIG ERROR |
| S08 Core start/stop x5: ready, clean stop, port freed | PASS | ready in [3.3, 2.3, 2.8, 1.8, 1.8] s |
| S10 `jarvis diag` support bundle masks conversation text | PASS | 232 lines, 0 unmasked text fields |
| S11 log rotation is bounded (20 KB x 4 files max) | PASS | 4 files, 60204 bytes |
| S12 ChatGPT-plan text path (real: 5 questions via the user's plan) | PASS | all answered |
| S09 API key never appears in logs, temp state, diag bundle or repo | PASS | scanned 28782 files, key hits 0, diag hit False, repo patterns 0 |

Run it again: `uv --directory core run --no-sync python tools/suite_system.py`.
