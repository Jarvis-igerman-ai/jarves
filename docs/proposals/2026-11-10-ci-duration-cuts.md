# CI duration: jarves-checks stage times and three proposed cuts (JAR-154)

Status: PROPOSAL only. `.github/` is Tier R; nothing here is applied. The owner decides.
Method: `gh run list --workflow jarves-checks --branch main --limit 20` + `gh run view <id> --json jobs`
(read-only), runs 2026-11-09 10:38Z to 21:27Z. 19 runs had job data (1 run had no jobs, 1 was a 2-job stub);
17 of 20 succeeded. Job time = `completedAt - startedAt`; p50/p95 use nearest-rank. Small sample: UNVERIFIED as a long-term trend.

## Observation
Most main runs reuse the PR proof (tests, browser, perf skipped). What remains is the installer chain, which ran in
12 of 18 runs and is the whole critical path. Wall time of a run (first job start to last job end): p50 14.2 min, p95 19.8 min, max 20.8 min.

## Per-job time (minutes, jobs that actually ran)
| Job | n | p50 | p95 | max |
|---|---|---|---|---|
| Installer upgrade and rollback | 12 | 9.5 | 10.2 | 10.2 |
| Windows installer (build + silent install smoke) | 12 | 4.9 | 5.5 | 6.0 |
| Installer smoke (ci-smoke, functional) | 12 | 2.7 | 3.0 | 3.1 |
| Installer smoke (release, functional) | 12 | 2.5 | 2.6 | 2.6 |
| Installer smoke (release, autostart-purge) | 12 | 1.4 | 1.7 | 2.0 |
| Installer smoke (ci-smoke, autostart-purge) | 12 | 1.3 | 1.5 | 1.5 |
| Fast CI and documentation validation | 18 | 0.9 | 1.0 | 1.1 |
| Classify the change | 18 | 0.2 | 0.3 | 0.4 |
| Update shard timing data (linux / windows) | 18 | 0.2 | 0.3 | 0.3 |
| CI result (single required check) | 18 | 0.1 | 0.1 | 0.1 |

Longest steps (p50 s / p95 s): upgrade + rollback matrix 548 / 589; smoke functional (ci-smoke) 135 / 145; build installer 116 / 136;
build pinned WhatsApp bridge 70 / 75; `actions/setup-go` 69 / 75; quick gate in validate 41 / 50.

Critical path ≈ validate 0.9 + installer build 4.9 + max(upgrade 9.5, smoke 2.7) ≈ 14-15 min. The upgrade job is the long pole.

## Proposed cuts
1. **Shard `installer-upgrade` by old-version fixture** (matrix over `packaging/upgrade_fixtures.json` entries, 2-3 shards).
   The 548 s step is a sequential matrix. Expected: job 9.5 → about 3.5-5 min; run wall p50 14.2 → about 9-10 min (-4 to -5 min).
   Cost: +1-2 runner start-ups (about 30 s each). Needs `upgrade_matrix.ps1` to accept a fixture filter (not present today: UNVERIFIED).
2. **Stop rebuilding the WhatsApp bridge and setting up Go inside the installer job.** `setup-go` (69 s) plus the bridge build (70 s)
   duplicate the separate `whatsapp-bridge` job. Cache or download the reproducible bridge binary keyed on its source hash
   and skip `setup-go` on a hit. Expected: installer job 4.9 → about 2.7 min (-2 min wall, same again for every run that builds the installer).
3. **Run only the functional smoke scenarios on push to main.** `autostart-purge` (both profiles, 1.3-1.4 min each) and the duplicate
   `ci-smoke` profile are already proven on the PR/merge-group run of the same tree. Expected: -2 to -3 runner-minutes per main run; wall about 0
   (they run in parallel with upgrade), so this is a cost cut, not a speed cut. Do it after cut 1, when smoke would become the longer parallel branch.

Combined expectation (cuts 1+2): wall p50 about 14 → about 7-8 min. Estimates come from step timings above, not from a trial run.

## Not proposed
Validate (0.9 min) and the classify/timing/result jobs are already small. Test shards and browser did not run on these main pushes,
so their cost on PR runs is not measured here (UNVERIFIED; needs a separate sample of PR runs).
