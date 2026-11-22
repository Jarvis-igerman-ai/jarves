# JAR-650: the Windows installer job is not failing on #1336, #1344, #1387 or #1424

**Finding: there is no failing installer step to fix.** The ticket says the job "Windows installer (build + silent
install smoke)" fails on every PR that touches `auto_update.py` or `release.yml`. The CI records show that it never ran
on those PRs. It was **skipped**, and that cause is already fixed on main by JAR-584 (#1417, merge `5b29bd8`,
2026-11-20 03:29 UTC).

## Evidence (checked 2026-11-21 through the GitHub API)
| PR | Head | Latest `jarves-checks` run | Result | Windows installer job |
|---|---|---|---|---|
| #1424 | `b35580e` | [44716794224](https://github.com/Jarvis-igerman-ai/jarves/actions/runs/44716794224) | success | skipped (`installer=false`) |
| #1344 | `016b94f` | [44577302915](https://github.com/Jarvis-igerman-ai/jarves/actions/runs/44577302915) | success | skipped |
| #1387 | `18e3751` | no failed or cancelled check on the head | success | skipped |
| #1336 | `351c0b8` | only a duplicate "Auto-label policy" run is cancelled; a later one passed | success | skipped |
| #1419 | `4adc5ea` | [44661745303](https://github.com/Jarvis-igerman-ai/jarves/actions/runs/44661745303) | success | n/a |

- The `changes` job log of run 44716794224 prints `installer=false`. That run tested `refs/pull/1424/merge`, built on
  base `eadd904` (02:30 UTC), which is older than the JAR-584 merge. So it used the old `PR_INSTALLER` rule.
- On current main, `echo core/src/jarves/auto_update.py | python3 core/tools/ci_plan.py classify --event pull_request
  --files-from -` prints `installer=true`. `core/tests/test_ci_speed_tools.py` (row for PR #1344) already pins this.
- The JAR-584 PR run [44663079329](https://github.com/Jarvis-igerman-ai/jarves/actions/runs/44663079329) changed
  `ci_plan.py`, so every job ran, including the installer job. It passed on main's tree at that time.
- Rechecked 2026-11-22: `ci_plan.classify(files, event="pull_request")` on each branch's **full** file list
  (`git diff --name-only origin/main...<branch>`) gives `installer=true` for all four. On the #1417 merge-queue run
  [44675453407](https://github.com/Jarvis-igerman-ai/jarves/actions/runs/44675453407) the installer build and all five
  smoke and upgrade jobs are `success`.
- #1419: the cancelled run 44661738860 was replaced by run 44661745303 three seconds later on the **same SHA**, and that
  run passed. Re-running the cancelled run would be a blind rerun of a superseded run, so it was not re-run.

## Why the PRs are not merging
All four PRs (#1336, #1344, #1387, #1424) are `mergeable_state: clean` and have the `no-automerge` label (Tier R:
update path, release workflow, release gate). The steward never merges them. They wait for the independent Opus review
and the owner, not for CI.

## What is needed (not done here)
To see the installer job run on these PRs, each PR's own branch needs a new run on a merge ref that includes `5b29bd8`.
That means the PR's owner updates the branch from main. This ticket may push only to its own branch, so nothing was
changed on those branches. Whether the installer job then passes on each PR is UNVERIFIED.
