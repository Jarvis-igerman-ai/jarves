# CI determinism

Goal: CI results depend on the code, not on the network, the clock or runner load.

1. **No real network in tests.** `core/tools/netguard.py` (installed by `core/tests/conftest.py` and by
   `core/tools/all_pages_check.py`) makes any connection to a non-loopback address raise `NetworkBlocked`, naming the
   calling file. Loopback and unix sockets are allowed. `@pytest.mark.network` lifts the guard for one test; the list
   is intentionally empty. Fix a failure with an injected fake (`UpdateChecker(http=...)`, `httpx.MockTransport`).
2. **`JARVES_TEST_OFFLINE=1`** (set by conftest and the real-browser tool): `updates._httpx_get` answers from a fixed
   offline stub (no GitHub releases), so the Updates page renders the same content everywhere. New HTTP clients in
   features probes, usage or Google code must honour it or take an injected transport.
3. **Waits are event-driven.** Use `tests/conftest.until` (generous 45 s, fails only on a real failure), not fixed
   sleeps; absolute time budgets belong only in the perf gate. Random data uses a seeded `random.Random`.
4. **Flake handling.** CI shards use `core/tools/ci_flake.py` (known transient patterns): only the failed files are rerun once. `core/tools/pytest_ci.py` does the same for any failure and is for local runs.
   Passing on retry is recorded in `flake-report.json` (test, first failure, passed_on_retry; uploaded as the
   `flake-report-*` artifact) and the run is green but reported; failing twice stays red.
   `core/tools/flake_report.py` aggregates reports into `docs/reviews/FLAKES.md` (counts by test).
5. **Quarantine.** A test that flakes twice in 7 days gets `@pytest.mark.quarantine` plus a GitHub issue (opened via the
   issues pipeline, one per test, linked in FLAKES.md). Quarantined tests leave the blocking run but still run in the
   separate non-blocking "Quarantined tests" step. They are never skipped: the zero-skips rule is unchanged.
