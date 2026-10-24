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
5. **Quarantine (owner rule 24 Oct 2026: a flaky test is a show-stopper, #160).** A test that fails and then passes on
   the same commit is paused at once: one entry in `core/tests/quarantine.toml` (`id = "<file>::<test>"`, `issue`,
   `since`, `reason`; an entry without an issue fails collection). `tests/conftest.py` gives it the `quarantine`
   marker; the blocking Core and browser runs use `-m "not quarantine"` (deselected, never skipped: the zero-skips
   rule is unchanged); the non-blocking "Quarantined tests" step (Core shard `1/3` on both OSes) still runs exactly
   the ledger's files on every run, so it stays visible. Every entry has an open issue labelled `showstopper` + `P1`;
   fix the test, delete the entry and close the issue in one PR. `core/tools/flake_scan.py` (nightly job
   "Flake scan") finds failures whose same commit later passed (a re-run or another run on that SHA) in the last 7
   days and opens or updates one such issue per test with the ledger line; `test_flake_scan.py` fails when an entry
   names a test that no longer exists.
