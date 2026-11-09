Report: SPEED r4 (JAR-101), status current (9 Nov 2026). Docs only; no test changed.

# Slowest tests on main 89832588 (Windows, local, serial, python 3.x miniforge)

Method: `pytest tests -x --durations=25` stopped at the first local failure (browser test, env) after 1034 passed in
610 s (about the first 12 % of the suite, alphabetical), then a targeted run of the files the committed shard cache
(`durations-*.json`, stale: 22 Oct) lists as slowest: 176 passed, 2 env failures (`pystray`, real front end), 291 s.

| Rank | s | Test | Open PR on file |
|---|---|---|---|
| 1 | 60.9 | test_wake_train::test_pipeline_trains_and_separates_the_phrase | none |
| 2 | 60.1 | test_agents_managed::test_autopilot_grant_needs_the_typed_code | #954 |
| 3 | 57.7 | test_wake_train::test_cli_train_and_apply | none |
| 4 | 22.8 | test_agents_claude::test_permission_request_goes_to_the_owner[approve-git push] | #1000 (JAR-80) |
| 5 | 22.0 | test_agents_managed::test_grant_never_covers_tool_approvals | #954 |
| 6 | 14.4 | test_delegation_client::test_strict_cap_never_exceeded_under_50_random_overlapping_schedules | none |
| 7 | 11.2 | test_agents_system::test_idle_bridge_and_registry_cost | none |
| 8 | 10.9 | test_agents_managed::test_one_writer_per_folder_and_new_worktree_offer | #954 |
| 9 | 9.3 | test_agents_system::test_voice_tools_cli_and_dashboard_see_the_same_sessions | none |
| 10 | 9.0 | test_runtime::test_dashboard_runtime_api_live_and_stopped | none |

Ranks 2, 4, 5, 8 are in files of open PRs (#954, #1000): left alone. The full suite was not profiled to the end, so a
slower test later in the alphabet is possible (UNVERIFIED).

# Candidates tried or ruled out (no change shipped)

- wake_train pipeline (61 s, cProfile): real compute (train_head 13 s, noise/background streams 21 s, evaluate 11 s,
  calibrate 11 s), not the render grid. Attempt 1, smaller grid (1 rate, 2 pitches): 68 s, no gain. Attempt 2, also
  `copies=1`, `hard_negatives=False`, one pitch: 43 s but the quality assertions FAIL. Debug budget used; reverted.
  The real fix is in product code (stream building / `correlate` 8.8 s), not Tier L.
- idle bridge cost (11 s): 10 s is the measurement window; halving it makes the 0.2 % CPU bound (20 ms) fall to 10 ms,
  below Windows' 15.6 ms `process_time` tick, so it would flake. Not changed.
- strict cap property test (14 s): the acceptance says 50 schedules; reducing it weakens the acceptance. Not changed.

Before/after: none (no speedup shipped). Next: profile the rest of the suite on CI with junit (`ci_plan.py
durations-merge` output) and refresh the stale `durations-*.json`; speed up wake_train streams in product code.
