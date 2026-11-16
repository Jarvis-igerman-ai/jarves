# Test real-clock audit (JAR-134, 16 Nov 2026)

Scope: `core/tests`. Search: `time.sleep(`, `time.time()`, `datetime.now(`, `date.today(`.
At audit time 41 files call `time.sleep` and 52 read the wall clock (counts include
bounded poll loops, subprocess scripts and perf tests that measure time on purpose).

## Ranking rule
Worst = a FIXED sleep that guesses how long another thread/process/clock tick takes
(slow and flaky on loaded CI), or a date read that can race midnight. Bounded poll
loops with a deadline (`while not cond and time < deadline`) are acceptable and kept.

## Fixed (8)
| Test | Was | Now |
|---|---|---|
| test_hotfix_slice2 bridge apply/revert | `sleep(1.1)` x2 for a new second-resolution hotfix id | `_now_id` fake counter |
| test_web_chat feature switch | `sleep(1.1)` for the 1 s Features refresh | `Features.poll()` on the served instance |
| test_browser_driver deadline job | `sleep(0.2)` + `sleep(2.5)` | events + a FIFO marker job |
| test_browser_driver hung page stop | `sleep(0.3)` hoping the job started | "busy" event |
| test_budget_mp children barrier | `sleep(1.5)` for 8 child imports | per-child ready files, bounded wait |
| test_perf2_swr invalidate race | `sleep(0.3)` for the refresh thread | wait on the in-flight event |
| test_backup live writer | `sleep(0.2)` for the writer to start | "first commit" event |
| test_usage thresholds (2 tests) | `datetime.now()` vs Monitor clock: midnight race | fixed `NOW` + `clock=` injection |

## Left as is (with reason)
- Perf/budget tests (`test_perf_gate`, `test_perf_overhaul_dashboard`, `test_perf_probes`): they measure time.
- Negative "nothing happened" waits (`test_ui_action` 0.3 s, `test_perf_overhaul_dashboard` 0.3 s): need a time window; a hook would need product changes.
- Socket deadline tests (`test_pilot_proxy_review*`): exercise a real network deadline.
- `test_privacy` flag "at" timestamp: product compares against wall-clock setup time; a fake needs a `logs` clock hook (follow-up).
- `test_selfcheck` stop-during-step: the 0.6 s step plus `asyncio.sleep(1.0)`; an event needs care around `Core.stop()` joins (follow-up).
- Token expiry tests using `time.time() +/- N` with large margins: safe.
