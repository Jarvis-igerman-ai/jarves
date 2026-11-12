# Test speed measurement (JAR-53)

Date 2026-11-09. Test-only; no `.github` or product change.

## Baseline (full core suite, `pytest --durations=40`)
- 7428 passed, 36 failed, 6 errors, 9 skipped in 10117 s (2h48). The machine was shared with many other
  agents' pytest runs, so wall time is NOT comparable to CI (jarves-checks p95 ~29 min, UNVERIFIED: not pulled).
- The 36 failures / 6 errors (browser driver, camera, wizard browser, mobile e2e, skill creation, wake core)
  were not investigated; many need a real browser/display/camera and likely fail under this load or environment.

## Slowest tests (seconds, under load)
| s | test |
|---|---|
| 868 | test_wake_hardneg::test_train_phrase_reports_the_baseline_and_the_comparison_table |
| 774 | test_wake_hardneg::test_hard_negatives_are_rendered_for_training_unless_switched_off |
| 673 | test_wake_prod::test_same_seed_and_samples_give_the_same_model |
| 432 | test_v6_drive_wire::test_existing_registry_tests_pass_with_drive_off_gate[test_helper_approvals.py] |
| 376 / 375 / 361 | three more test_wake_hardneg trainings |
| 164 / 145 / 134 / 128 | test_wake_prod trainings |
Wake-word training tests (`test_wake_hardneg`, `test_wake_prod`, `test_wake_train`) dominate: ~4000 s of the
top-40 list. `test_v6_drive_wire` re-runs whole other test files in subprocesses.

## Change in this PR
`test_wake_hardneg.py`: one module-scoped no-baseline training run is shared by
`test_train_phrase_reports_the_baseline...` and `test_595_the_baseline_is_the_live_custom_head...`
(the second only needs a trained head file; seeds are frozen so results are identical).

## Before / after (test_wake_hardneg.py alone, run concurrently, same load)
- before: 13 passed in 833.8 s
- after: 13 passed in 774.6 s (~7% / 59 s). Noisy; saving is one of ~7 trainings.

## Proposals not done (need owner/Tier decision)
1. Share trained fixtures across `test_wake_prod.py` (6 trainings) the same way: bigger win, riskier.
2. Shrink training size (samples/epochs) in these hermetic tests via the existing fake embedder.
3. Mark the 5 wake-training tests `slow` and run them only in the affected-tests path (touches CI selection; Tier R-adjacent).
4. `test_v6_drive_wire` subprocess re-runs: run once instead of per file.
