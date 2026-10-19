# Pre-use test report: system (silent, headless)

Run: 19 Oct 2026 ~18:10 on this PC, on the integrated SW-V2 build with the Issue #16 fixes (WP-E worktree after `git merge swv2-integrate`). 12/12 passed in the suite run, plus S01 run on its own.
The run used `--skip-pytest --soak-min 3`. S01 is the separate full pytest run on the same build. The table is copied from the suite's console output: the generated file was lost to a checkout, so this one is hand-written.

| Test | Result | Detail |
|---|---|---|
| S01 offline pytest suite | PASS | 436 passed, 1 skipped in 180.77s (separate run, same build; after the later music commits 121 music/voice tests re-passed) |
| S02 Godot viewer + face self-tests (headless) | PASS | {'viewer': 'PASS', 'face': 'PASS'} |
| S03 real face survives malformed protocol messages | PASS | 1 passed in 11.48s |
| S04 jarvis doctor (key, ChatGPT, mic, wake model, Pear) | PASS | All checks passed. |
| S05 CLI commands run cleanly (12 commands, offline profile) | PASS | all ok |
| S06 memory written by one process is read by the next (persistence) | PASS |  |
| S07 bad configs are refused with a clear error (5 kinds) | PASS | all rc=2 CONFIG ERROR |
| S08 Core start/stop x5: ready, clean stop, port freed | PASS | ready in [5.4, 3.1, 2.6, 2.8, 3.1] s |
| S10 `jarvis diag` support bundle masks conversation text | PASS | 420 lines, 0 unmasked text fields |
| S11 log rotation is bounded (20 KB x 4 files max) | PASS | 4 files, 60204 bytes |
| S12 ChatGPT-plan text path (real: 5 questions via the user's plan) | PASS | all answered |
| S13 3-minute idle soak with the wake word listening (virtual mic) | PASS | memory 91.7->83.3 MB, peak CPU 19.7%, log 6 KB, wake ok |
| S09 API key never appears in logs, temp state, diag bundle or repo | PASS | scanned 27823 files, key hits 0, diag hit False, repo patterns 0 |

Limit: the idle soak ran 3 minutes, not 10 (RAM on this PC was tight, ~0.9 GB free).

Run it again: `uv --directory core run --no-sync python tools/suite_system.py`.
