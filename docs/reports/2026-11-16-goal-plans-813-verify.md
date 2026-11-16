# #813 goal_plans cancel race: verified on main (JAR-130)

Checked 16 Nov 2026 against `origin/main` 9ea225b.

## Question

Issue #813: `goal_plans` skip did a read-then-write, and runner step writes could
overwrite a cancel. The backlog lists it as fixed on main (evidence pack A, T17).

## Finding

Both writes now use compare-and-set (CAS) against the stored state. No race was left to fix.

| Part | Commit | Test (`core/tests/test_goal_plans_cancel_race.py`) |
|---|---|---|
| Skip and runner writes use CAS | 2096c9d | `test_runner_step_write_after_a_cancel_never_runs_the_skipped_step` |
| Cancel stays cancelled after a restart, a step start or a stale cancel | b611961 (JAR-235) | `test_recover_racing_a_cancel_keeps_cancelled`, `test_cancel_on_a_stale_view_never_overwrites_a_finished_plan` |
| Cancel accepts any cancellable state | a2cf5aa | `test_cancel_on_a_stale_open_view_still_cancels_a_plan_that_moved_on` |
| A skip on a stale view re-reads the state | 26ae1ed | `test_skip_on_a_stale_open_view_uses_the_current_state`, `test_skip_on_a_stale_view_after_a_cancel_keeps_cancelled` |

## Proof

From `core/`: `python -m pytest -q -o pythonpath=src tests/test_goal_plans_cancel_race.py tests/test_v5_goal_plans.py tests/test_changelog_parts.py` gave 48 passed.

## Not covered

- I did not re-run these tests against code from before the fix (UNVERIFIED).
- #813 part 2 is not checked here. That is a "may already have happened" warning in the Gate approval path, and it is Tier R.
- Installed-product behaviour is UNVERIFIED.
