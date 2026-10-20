# SW-V4 package M: performance budgets, CI hygiene, nightly (issue #17)

Branch `v4-m-perf-ci` (off `origin/main` 5a98f13). Model: Sonnet 5.5. Date: 20 Oct 2026.

## What was built

| Area | Change | Files |
|---|---|---|
| Budgets | Machine-readable budgets with baseline, strict owner ceiling and loose CI ceiling; human table and update procedure | `core/perf_budgets.toml`, `docs/reviews/SWV4_PERF_BUDGETS.md` |
| Gate | `perf_gate.py`: median of `bench_startup.py --scenario combined` runs + in-process timings (features_list and probe path with an expired slow probe, dashboard first load and page APIs) + optional real-log stages; exit 1 on any breach; `--propose`, `--from-json`, `--results` | `core/tools/perf_gate.py` |
| Gate tests | well-formed budgets; passes on baseline; each metric fails alone when slowed (34 cases); exit codes; a really slowed `Features.list` fails the in-process measurement | `core/tests/test_perf_gate.py` |
| CI path filters | `changes` job classifies the diff (docs-only = `docs/**`, root `*.md`, LICENSE, .gitignore). Docs-only PRs skip core, e2e, installer, perf, cad, blender, godot; skipped counts as green for the merge queue. `main` pushes, `v*`/`win-v*` tags and manual runs always run everything | `.github/workflows/jarves-checks.yml` |
| Perf job | Windows 2025, `perf_gate.py --class ci` (2 runs, 60 s idle), uploads `perf-results` | same |
| Concurrency, timeouts | cancel-in-progress per workflow and ref (existed); every job in every workflow has `timeout-minutes` (core stays 40) | all workflows |
| Caches | uv (setup-uv), Godot/Inno Setup tool cache (core, installer), plus new caches for the Godot zip (godot, e2e), Blender and the FreeCAD AppImage (checksum still verified each run) | `jarves-checks.yml` |
| Nightly | `nightly.yml`, 02:00 UTC + manual: full suite on Ubuntu and Windows (120 min), flake detector (3x full suite on Linux, no reruns), perf gate (3 runs), redaction audit tests, synthetic canaries hook | `.github/workflows/nightly.yml` |
| Diagnostics | `ci_diagnostics.py` redacts (jarves `logs.redact`, JSON `logs.scrub` privacy on, emails, user paths, phone numbers), re-scans the result and discards everything if a secret-looking string survives; uploaded only on failure (7-14 days) | `core/tools/ci_diagnostics.py`, `tests/test_ci_diagnostics.py` |
| Package O hook | `synthetic_canaries.py`: registry `CANARIES`, JSON report, exit 1 on failure; reports "no canaries registered yet (package O)" now | `core/tools/synthetic_canaries.py` |
| CI minutes | `ci_minutes.py`: monthly Markdown table of job minutes per workflow and OS via `gh api` (for HANDOFF; not run here, no network) | `core/tools/ci_minutes.py` |
| Shared fakes | `FakeStream`, `FakeResponses`, `TextResponses` (the camera variant) moved to `core/tests/openai_fakes.py`; `test_camera.py` and `test_tasks.py` import them (the camera one under its old name). Behaviour identical | `core/tests/openai_fakes.py` |
| Pipeline tests | timeouts everywhere, cancel, path-filter wiring per job, perf job, caches, diagnostics upload, serial suite, nightly shape, canary runner, ci_minutes | `core/tests/test_ci_pipeline.py` |

## Decision: no pytest-xdist (suite stays serial)

Sharding was considered and rejected for now. Reasons: the suite has about 1600 tests, many start a Core with real
loopback ports, real Godot/Inno/Edge processes and a shared tool cache directory (`jarves-test-tools`), so parallel
workers would race on downloads and compete for ports and the 2-core Windows runner; the plan's flake policy forbids
reruns, so a parallelism-induced flake would be a P2 with no proof of reliability available offline here (this machine is
RAM-limited and cannot run the full suite 3x). The Windows core job has a 40 min timeout and runs only on code changes;
the docs-only skip and caches give the saving without the risk. Revisit with a measured 3x nightly comparison
(`pytest -n 2` on Linux only) once the nightly has run green for a week. `test_ci_pipeline.py` pins the serial setup so
changing it is a conscious choice.

## Tests run (this machine, focused, one pytest at a time)

`tests/test_perf_gate.py` 44 passed, `tests/test_ci_diagnostics.py` 3, `tests/test_ci_pipeline.py` 11 plus
`tests/test_ci_workflows.py` 4 (all workflow YAML loads), `tests/test_camera.py` and `tests/test_tasks.py` (shared fakes)
passed: 103 passed in 18 s in the last combined run, 0 skipped. The full suite was not run here (RAM; the CI core job
does it).

## Measured here (busy PC, 2 runs, idle 30 s, owner class): not a baseline

private 96.8 MB, working set 120.8 MB, threads 14, idle CPU 0.51 %, idle writes 27 KB/min (30 s window), reads
590 KB/min, Core ready 3.1 s, spawn to ready 8.2 s, stop 2.2 s, features_list 5.0 ms, probe path 0.9 ms, dashboard first
load 78 ms, slowest page API 74 ms. Five agents were running, so the strict owner ceilings on CPU, writes and start times
were exceeded; this is the reason for the separate loose CI class and a 60 s idle window.

## Not done / open

- The first real CI numbers for the Windows runner (the `ci_max` values are estimates: baseline x2-5). Tighten after the
  first nightlies.
- Acceptance items that need the owner PC or a week of runs: wake-to-first-audio and face show/hide 5x (A, AM-P1),
  idle-read explanation (AM-P2), 5 green nights, measured docs-only vs code minutes (use `ci_minutes.py` after the push).
- Ubuntu/Windows matrix for dashboard (Edge/Chromium), go-bridge jobs and a `perf` job per OS belong with the packages that
  add them (K, F); `changes` already has the pattern.
- Branch protection / automerge: skipped jobs are accepted by `automerge.yml` (conclusion `skipped`), unchanged.
