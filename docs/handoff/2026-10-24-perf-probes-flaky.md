# test_perf_probes flake fixed (P3) - 24 Oct 2026

- Asked: after PR #121, `test_perf_probes.py::test_perf_gate_inprocess_with_every_v4_module_registered` failed once on Windows shard 3/3 (features_list_ms 178 ms vs the 150 ms budget) and passed on rerun.
- Cause: the test took a single sample (`repeats=1`), so one scheduler hiccup decided the result.
- Fix (P3, fixed): `repeats=3`; `speedtest.measure_inproc` already reports the median, the same statistic the real perf gate uses. The budget (150 ms) is unchanged, so a real regression (357 ms earlier) still fails. Test-only change.
- Verified: CI only (timing test; no local run). Not verified: that the flake never recurs.
