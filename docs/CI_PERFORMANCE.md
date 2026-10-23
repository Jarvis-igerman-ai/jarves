# CI performance and the fast release path

Goal: the next release is cheap. Owner stretch goal: **merge to published installer in about 5 minutes**, and a PR green in
under 10 minutes. This page has the measurements, the design, what is implemented, and what honestly blocks 5 minutes.

## Before / after

Measured = taken from `gh api .../actions/runs/<id>/jobs` for the runs named. Estimated = derived from measured parts; the
first CI run after the 4.0.0 freeze replaces every "est." with a measurement (record it in `docs/HANDOFF.md`).

| Step | Before (4.0.0 main, run 39676015294, measured) | PR #44 sharding (run 39669040218, measured) | This change |
| --- | --- | --- | --- |
| Windows Core tests | 1726 s serial (28.8 min) | 366 / 437 / 637 s shards, +105 s and +235 s runner queue (wall 14.7 min) | 3 shards balanced by measured seconds, slowest file first, xdist x2 in each: est. 4-6 min |
| Linux Core tests | 851 s (14.2 min) | 771 s | 3 isolated runners, serial within each; est. 5-6 min, pending CI |
| Installer build + smoke | 451 s, started only AFTER the tests | same, still after the tests | 400-450 s (measured on 3 runs), **in parallel with the tests** |
| PR wall clock (tests + installer) | 36.3 min (1726 + 451 s) | about 22 min (14.7 + 7.5) | **est. 8-9 min** (installer is the long pole) |
| Push to `main` after the merge | 37.1 min (21:06:15 to 21:43:24): repeats tests and installer | same | **about 30 s**: finds the PR proof, skips everything |
| `jarves-package` (candidate + signing) | 39 s (job) + trigger latency | same | est. 60-90 s (download of the 198 MB installer is the new cost, it was already local to the run before) |
| **Merge to a published candidate/beta** | **about 38 min** | about 38 min | **est. 2-3 min** |
| Duplicate runs per commit | a merged commit ran the full matrix twice (PR, then main) and superseded main runs ran to cancellation | same | one proof per tree; main run is a 30 s lookup when the tree matches |

Queue time on the hosted runners was 1-4 s in every measured run except when 3 shards plus the other Windows jobs competed
(105 s and 235 s). Moving the installer next to the tests adds one more concurrent Windows job; the repository is public, so
the 20-concurrent-job limit is not close (a PR now uses 7 Windows jobs at peak).

## The design

```
PR head (pull_request, merge ref)                      merge (automerge)              main push
 changes + fast validation                                                                        changes (30 s)
  |-- core: 3 Windows + 3 serial Linux shards (planned)  \                                 tree == a proven PR tree?
  |-- browser, e2e, perf, bridge, cad, blender, godot  >  ci-ok (single required check)    yes: every job skipped
  '-- installer: build + silent-install smoke ---------/                                 no : full matrix (fallback)
        uploads jarves-installer-<tree>                                                  |
                                                                                         v
                                                              jarves-package (workflow_run, or `gh workflow run package.yml`)
                                                               resolve artifact by tree -> verify SHA256 + tree -> stamp
                                                               -> signed candidate -> beta (if enabled, off by default)
```

1. **Parallel critical path.** `installer` needs only `changes` (checkout, tool cache). The tests and the installer run
   side by side; publishing (`jarves-package`) starts only from a `workflow_run` whose conclusion is `success`, i.e. after
   **both** are green (a green installer next to red tests never publishes, because `resolve` also requires the producing run
   to be green).
2. **Content-addressed proof.** `core/tools/ci_reuse.py tree` computes the *tree*: a sha1 over `git ls-tree -r` of the
   checkout without documentation (`docs/`, root `*.md`, `LICENSE`, `.gitignore`). The installer artifact is named
   `jarves-installer-<tree>`. On a PR run the checkout is the merge of the PR head and its base; when automerge merges it
   (`--merge`, only when the PR is not behind main) the merge commit has the **same tree**. The `main` push therefore finds
   the proof (`resolve`: not expired, from a *green* `jarves-checks` run of **this** repository, not a fork) and skips all
   jobs. A docs-only change merges with the same tree as before and reuses the previous proof, too.
   Fallbacks, all safe: a different tree (someone pushed to main in between, a direct push, a release tag, a manual run) runs
   the full matrix including the installer; an expired artifact (PR 7 days, main 30 days) does the same.
3. **Version stamping without a rebuild.** Before: the installer filename and `build-info.json` carry
   `X.Y.Z.<run number>` baked in at build time, which forced a rebuild per commit. Now: the version is the run number of the
   build that was *tested* and is kept as is. This stays strictly increasing because automerge only merges a PR after a run
   on top of the current `main` (a PR that falls behind is updated and gets a new, higher run number before it merges). The
   metadata that must describe the *published* commit is stamped at **manifest level** by `packaging/release.py stamp`:
   `commit` = the commit on main, `built_from_commit` = the commit the bytes were built from, `channel` = main, `tree`. The
   `.exe` is never touched, so its SHA256 (checked by `verify`) is the one the smoke test ran. Nothing signs the exe itself
   today (the manifest is the Ed25519-signed object), so no re-signing of the binary is needed.
4. **Path filters** (`core/tools/ci_plan.py`, unit-tested): docs, `CLAUDE.md`, root `*.md` run nothing; guides-only
   (`core/src/jarves/guides/data/*.toml`) run only the Linux Core shards (the Windows shard jobs start, see the gate step, and
   finish in seconds so required check names still report); the Go bridge only for `bridge/`, `packaging/bridge_pin.json`
   or its build script; CAD for `cad/`; Blender for `blender/`, `cad/`, `godot/`; Godot for `godot/`, `blender/`; the
   installer, perf and e2e for `core/src`, packaging, Godot and the dependency lock. Changing the workflow file, the CI tools
   or `tests/conftest.py` runs everything. Main pushes, tags and manual runs always run everything unless the tree is proven.
5. **Test sharding by measured duration.** `ci_plan.py` plans 3 Windows shards and 3 Linux shards from
   `durations.json` (seconds per test file), falling back to test counts for unmeasured files. The plan is computed once
   in `changes` and handed to every shard (`--shard-files`, slowest file first), so shards cannot disagree. After every full
   run, and after every `main` push that reused a PR proof (from THAT PR run's junit files), the `durations` job merges the
   junit files of all shards into a cache entry (`jarves-durations-v2-<os>-<run>`) that the next `changes` job restores:
   **rebalancing is automatic, no commit, no token** (see "Follow-up: timing refresh after reuse"). Seed or refresh by hand:
   `python core/tools/ci_plan.py durations-merge --junit <one OS artifact directory> --into core/tests/durations-windows.json`.
   Inside a shard, `pytest-xdist` (pinned `pytest-xdist==3.8.0` through `uv run --with`, not in the lock) runs files in
   parallel workers with `--dist loadfile`: one file never splits across workers, and the suite already uses temp homes and
   `free_port()` per test. Workers: 1 (serial) on Linux (3 workers broke 20 process-limit/update-flow tests on ubuntu-24.04), 2 on Windows; repository variable `CI_XDIST_WORKERS` overrides, `1` turns it off.
   This replaces the old "serial on purpose" rule; the safety net is item 7.
6. **Caching.** uv (lock hash), the Go module and build cache (`setup-go`), Playwright browsers (Linux), the Godot and Inno
   Setup downloads (`jarves-test-tools`), the installer build tools (Python, Godot templates, Inno Setup:
   `jarves-build-tools`), FreeCAD, Blender, the Godot zip, and the measured durations. pip wheels are covered by the uv cache.
7. **Flakes.** The shard step re-runs **only the failed test files, once**, and only when every failure matches a known
   transient pattern (WinError 10048/10053/10054/10061, address in use, connection reset/refused, "condition not reached",
   timeouts) and there are at most 6 failing files (`core/tools/ci_flake.py`). A real assertion failure is never retried.
   Every retry that turned green is written to `flake-report.json` in the `junit-<n>` artifact, so flakes stay visible. The
   zero-skips check still reads the whole output.
8. **No duplicate runs.** `pull_request` runs the checks; `push` is limited to `main` and tags, so a branch push never
   starts a second run for the same commit. Superseded runs of the same PR or ref are cancelled (`concurrency` group is the
   PR number). Drafts are not special-cased; automerge skips them. `ci-ok` is the one stable required check.
9. **Dependabot.** `dependabot-automerge.yml` labels patch and minor updates `automerge`
   (`core/tools/dependabot_gate.py`); major updates and anything it cannot parse get a comment and wait for a human. The queue
   then merges only on a green `jarves-checks`. Kill switch: repository variable `DEPENDABOT_AUTOMERGE=false`.

## Merge to published in about 5 minutes: honest minimum

After the merge the only work is: automerge push event, `changes` (checkout + two API calls, about 25 s), the
`workflow_run` hand-off (about 5-15 s), then `jarves-package`: checkout, resolve, **download of the installer (about 198 MB)**,
`sha256` verify, stamp, sign, upload the candidate artifact (another 198 MB, uncompressed on purpose), and for a beta the
upload to the releases repository. Estimated 2-3 minutes in total, so **5 minutes from merge to a published beta is
reachable**; it is not measured yet. Biggest items, in order: the two 198 MB transfers (about 20-40 s each, depends on the
runner region), the candidate artifact upload (can be skipped when only publishing; the beta job downloads `beta-input`
instead), and Windows/Ubuntu runner start (1-4 s measured when idle).

What blocks "5 minutes including the tests" is not the merge side but the **pre-merge** side: the PR cannot be green before
the installer is built and smoke-tested, about 7-7.5 minutes (Godot export, embedded Python, Inno LZMA compression, silent
install/upgrade/uninstall smoke, upgrade matrix). The honest minimum for a PR is therefore about 8 minutes, not 5. Ways to go
lower, none done yet: cache the staged payload per content hash of `core/`, `godot/` and `packaging/` so most PRs only
re-compress; use Inno `lzma2/fast` for CI builds only (bigger exe, faster build, but then the proven exe is not the
published exe, which breaks the "same bytes" rule, so only for the smoke); split the smoke into the install path (blocking)
and the upgrade matrix (parallel job). Each of these needs measuring on a runner.

Other limits: the artifact must exist (PR 7 days, main 30 days); the PR has to be tested on top of the current main (a merge
behind main re-runs the PR checks first, about 8 minutes); a runner outage or a contended Windows pool can add minutes.

## Required status checks (branch protection) and job names

Recommended: require only **`CI result (single required check)`** (job `ci-ok`, green when every other job passed or was
legitimately skipped, red on failure or cancellation). The per-job names below are stable and still valid to require:

| Job id | Name |
| --- | --- |
| changes | Classify the change (docs-only skips heavy jobs; reuse of the PR proof) |
| validate | Fast CI and documentation validation |
| core | Python Core tests (ubuntu-24.04 shard 1/3), (ubuntu-24.04 shard 2/3), (ubuntu-24.04 shard 3/3), (windows-2025 shard 1/3), (windows-2025 shard 2/3), (windows-2025 shard 3/3) |
| browser | Real-browser tests (ubuntu-24.04), Real-browser tests (windows-2025) |
| installer | Windows installer (build + silent install smoke) |
| whatsapp-bridge | WhatsApp bridge (Go vet/test + reproducible build) |
| perf | Performance gate (Windows) |
| e2e | Core-Godot E2E (real processes) |
| cad, blender, godot | cad, blender, godot |
| ci-ok | CI result (single required check) |

A skipped job reports as passed. Do not require `Update the shard timing data` (job `durations`; main and manual runs only).
Because a merge commit whose tree is proven skips every job, the push run on `main` is green in about 30 s.

## Zero skips

Unchanged: the sessionfinish hook in `tests/conftest.py` and the grep in each pytest step fail the run on any skipped or
xfailed test. Filtering is by *deselection* (file plan, OS markers), never by skipping.

## Tools

| Tool | Purpose |
| --- | --- |
| `core/tools/ci_plan.py` | classify a diff, plan shards from measured durations, merge junit timings |
| `core/tools/ci_flake.py` | pick the known-flake failed files to retry, write the flake report |
| `core/tools/ci_reuse.py` | content tree, find the proven installer artifact |
| `core/tools/release_status.py` | one screen: main CI, installer artifact, candidate, beta/stable, blockers |
| `core/tools/dependabot_gate.py` | patch/minor vs major |
| `packaging/release.py verify / stamp / candidate` | provenance by commit or tree, manifest-level stamping |

Unit tests: `core/tests/test_ci_speed_tools.py` (offline, no skips).

## Follow-up: isolated Linux shards and early validation (22 Oct 2026)

Stacked on PR #44. Linux uses three separate hosted runners with one pytest worker each by default, so its tests do not
share a process quota, ports or working directory across shards. Both platforms execute the complete non-browser file
set exactly once; the fast gate checks coverage before the heavy jobs start. The existing CI_XDIST_WORKERS override is
unchanged. The installer still runs concurrently with the Core shards after validation.

The fast validation job runs workflow contracts, CI helper tests, shard coverage and documentation links using the
locked base dependencies without optional extras. Every expensive job depends on its success, and ci-ok includes its
result. It also runs for docs-only changes: an invalid documentation map must fail even when runtime jobs are deselected.
The two failures inherited from PR #44 (missing document-map entries and a stale cancellation assertion) are corrected.

The setup CLI unit test substitutes the connection runner and prerequisite preparation while asserting that the real
CLI/wizard invokes the connector. Real connector behavior remains covered by test_connect.py; the unit test no longer
waits for an absent Pear Desktop or installs optional software. Local Windows result: 3.80 s for this test; prior hosted
Windows result: 173.33 s (different machines, so this is not a controlled speedup measurement).

Local focused validation: 78 passed in 32.16 s, zero skips. Full hosted-run timings and results belong in HANDOFF.md.
First full hosted result (commit ed14a9f, run 39733332477): green in 9m46s. Linux shards took 4m33s / 2m04s / 4m41s;
Windows shards 5m09s / 3m24s / 9m22s; installer 7m46s; fast validation 13s. Windows shard 3 needed one known-transient
socket-timeout retry of test_perf_probes.py (5 tests passed on retry, one initially failed). CodeQL also passed.
These are observed timings for one run, not a guaranteed budget. The implementation was subsequently incorporated into PR #44 at c00a773; PR #57 retains the evidence/documentation
follow-up. Its final commit runs fast validation; non-documentation content matches the full-green ed14a9f commit. Branch protection should use ci-ok; any explicitly
required old Linux shard 1/1 check must be updated when this stacked change reaches main.

## Follow-up: measured platform plans and performance-test isolation (22 Oct 2026)

The previous full run (39733332477) exposed the next bottleneck: Windows shards took 309 / 204 / 562 seconds while Linux
finished in 281 seconds. The planner's expected times were 311 / 311 / 310 on both OSes because it had no measured cache;
it was using test counts, not actual seconds. Timing refresh also excluded PRs, so repeated PR runs could not learn.

- Committed seeds, core/tests/durations-linux.json and durations-windows.json, contain file totals from that run's JUnit
  artifacts: junit-0..2 are Linux, junit-3..5 Windows. They contain test filenames and seconds only, no raw diagnostics.
- ci_plan.py weights each OS independently. New or unmeasured files retain the count-based fallback; legacy callers can
  still supply one map. The plan covers each non-browser file exactly once per OS. Linux stays serial per runner.
- A green full Core matrix now refreshes separate versioned caches on main/manual runs AND same-repository PRs. The timing
  jobs download only their OS's artifacts, blend new samples with the seed/restored history, then save separately.
  GitHub scopes PR caches to the merge ref: these help later runs of the same PR, not other branches or main. New branches
  start with committed seeds (or an accessible main cache); fork PRs do not publish timing caches. Reused/docs/guides-only
  runs do not overwrite full-suite timings. The timing jobs remain optional for ci-ok and never publish installers.
- test_perf_probes.py's feature-probe regression no longer starts dashboard HTTP measurements whose result it never
  asserted. It still times the real feature list and probe paths with every V4 module registered. The separate
  test_perf_gate.py regression and the actual CI performance job retain real dashboard measurements and budgets.

Local focused check: 61 tests passed in 30.84 seconds, zero skips; isolated feature-probe regression 1.73 seconds.
This is not yet a hosted timing claim. PR #57 records the final commit's hosted result and comparison. Browser readiness,
installer dependency-stage caching and remaining slow Core-start fixtures are deferred; no product behavior changed.

Cache scope checked 22 Oct 2026 against [GitHub's dependency caching reference](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache).

## Follow-up: timing refresh after reuse (23 Oct 2026)

Measured on the 24 jarves-checks runs since 22 Oct 14:00 (job timings from the Actions API): a green PR run takes
10.3-14.1 minutes and its critical path is always ONE Python shard that runs about twice as long as its siblings. Median
job times (green runs): Linux shards 280 / 584 / 289 s, Windows shards 394 / 340 / 520 s. Run 39979947350 (PR #82):
Linux shard 2/3 ran 515 s against a planned 269 s; Windows shard 3/3 606 s against the same plan as its 360 s and 398 s
siblings.

Root cause: the plan used the committed seed of 22 Oct for every PR. The `durations` job only ran when the Core shards ran
in the same run, but since the reuse design (above) the push to `main` after a normal merge skips every job, so `main`'s
cache was never written; PR caches are scoped to the PR's merge ref and never reach other PRs. 30 of 174 non-browser
test files had no measurement and were weighted by test count, among them `test_wake_prod.py` (about 190 s on Linux,
planned as about 2 s), `test_mobile_phone_e2e.py` (real browser), `test_web_chat.py` and `test_self_manage.py`.

Fix: `changes` exports the proof run id (`proof_run`) when a `main` push reuses a PR proof, and `durations` downloads
the junit artifacts of THAT run (`actions/download-artifact` with `run-id`, job permission `actions: read`) and saves
`main`'s cache, which every new PR restores. `durations` is `continue-on-error` (a timing hiccup must never turn a run red:
a red run blocks `jarves-package` and automerge) and stays outside `ci-ok`. The `changes` job summary now prints the
expected seconds per shard and the unmeasured files (`ci_plan.py summary`), so a stale plan is visible in every run.

Expected effect (not measured yet; record the first runs after the first merge in HANDOFF): the same work spread evenly is
about 385 s per Linux shard and about 455 s per Windows shard, so a PR run of about 9-9.5 minutes instead of 11-13. The
first merge after this change fills `main`'s cache; PRs opened before that still plan from the seed.

Not changed here (next candidates, each measured on its own): Windows xdist workers (2 on a 4-vCPU runner), a fourth
shard per OS (more concurrent jobs: two PRs at once already queue against the 20-job limit, 47-196 s queue measured), and
moving `test_mobile_phone_e2e.py` (a real-browser file inside the Core shards, no browser-job retry) to the browser job.

## Follow-up: Windows workers and own-file collection (23 Oct 2026, #89)

- Windows workers stay 2. Measured on PR #96 with 3: Windows shards 413 / 359 / 453 s, no faster than 2 workers with a
  balanced plan (PR #90's last run: 476 / 365 / 347 s), and `test_projectmgr::test_parallel_tasks_merge_and_dependent_gets_handoff`
  (parallel git worktrees) failed under the extra load. The balanced plan is what made the shards even: the same run had
  Linux shards of 339 / 348 / 354 s (before the timing fix: 254 / 552 / 296 s).
- Each shard passes ONLY its planned files to pytest. Before, every shard collected all 177 test files and deselected two
  thirds. Measured locally, cold cache as on a fresh runner: 5.8-6.3 s instead of 9.8-10.4 s per Linux shard, the same
  879 tests selected (shard 3/3); Windows imports are slower, so the saving there is likely larger. `--shard-files`
  still orders the files slowest first, and an empty plan for a shard now fails the job instead of running everything.
- Not done: merging the 4 installer smoke jobs into 2 (`smoke_windows.ps1 -Scenario full` would run both scenarios in one
  job): it frees 2 runner slots but ends close to the 9-minute critical path, so it could make every PR slower.
