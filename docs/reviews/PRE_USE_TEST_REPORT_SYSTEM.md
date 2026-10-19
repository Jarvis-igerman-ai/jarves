# Pre-use test report: system (silent, headless)

Run: 19 Oct 2026 08:01 on this PC. 13/13 passed. Duration 992 s.

| Test | Result | Detail |
|---|---|---|
| S01 offline pytest suite | PASS | 263 passed, 1 skipped in 65.53s (0:01:05) |
| S02 Godot viewer + face self-tests (headless) | PASS | {'viewer': 'PASS', 'face': 'PASS'} |
| S03 real face survives malformed protocol messages | PASS | 1 passed in 6.94s |
| S04 jarvis doctor (key, ChatGPT, mic, wake model, Pear) | PASS | All checks passed. |
| S05 CLI commands run cleanly (12 commands, offline profile) | PASS | all ok |
| S06 memory written by one process is read by the next (persistence) | PASS |  |
| S07 bad configs are refused with a clear error (5 kinds) | PASS | all rc=2 CONFIG ERROR |
| S08 Core start/stop x5: ready, clean stop, port freed | PASS | ready in [2.3, 1.8, 1.8, 1.8, 1.5] s |
| S10 `jarvis diag` support bundle masks conversation text | PASS | 265 lines, 0 unmasked text fields |
| S11 log rotation is bounded (20 KB x 4 files max) | PASS | 4 files, 60204 bytes |
| S12 ChatGPT-plan text path (real: 5 questions via the user's plan) | PASS | all answered |
| S13 10-minute idle soak with the wake word listening (virtual mic) | PASS | memory 0.0->0.0 MB, peak CPU 14.5%, log 14 KB, wake ok |
| S09 API key never appears in logs, temp state, diag bundle or repo | PASS | scanned 28003 files, key hits 0, diag hit False, repo patterns 0 |

Run it again: `uv --directory core run --no-sync python tools/suite_system.py`.
