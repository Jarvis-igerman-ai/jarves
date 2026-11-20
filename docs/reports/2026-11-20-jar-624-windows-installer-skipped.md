# JAR-624: why the Windows installer job did not pass on #1424, #1387, #1344, #1336

Date: 20 Nov 2026. Evidence read with `gh api` (REST) on the PR head SHAs.

## Finding

The job `Windows installer (build + silent install smoke)` was **skipped** (never started), not
cancelled or failing, on the latest run of all four PRs. `changes` set `installer=false` because the
PR rule (`PR_INSTALLER` in `core/tools/ci_plan.py`) did not yet cover `auto_update.py` or
`.github/workflows/release.yml`.

That rule was fixed by JAR-584 (PR #1417, merged to main 20 Nov 2026 03:29 UTC). Every run above
started before that merge:

| PR | Windows-sensitive file | Last run start (UTC) | Installer job |
|----|------------------------|----------------------|---------------|
| #1424 | `core/src/jarves/auto_update.py` | 20 Nov 03:02 | skipped (run 44664843960) |
| #1387 | `.github/workflows/release.yml` | 20 Nov 01:17 | skipped (run 44652347702) |
| #1344 | `core/src/jarves/auto_update.py` | 19 Nov 17:28 | skipped (run 44577302915) |
| #1336 | `.github/workflows/release.yml` | 20 Nov 01:50 | skipped (run 44656103279) |

Current main classifies each PR's file list with `installer=true` (checked locally with
`ci_plan.classify`). The regression test `core/tests/test_ci_speed_tools.py` already pins #1344 and
#1336 by name. On main the installer job and its smoke/upgrade jobs pass (for example 75faf4dc and
PR #1417 itself).

#1419: the cancelled `Python Core tests` entry belongs to a superseded run. All seven shards are
`success` on head 4adc5ea, so no re-run is needed.

## What is left (not done by this ticket)

A re-run of an old run reuses its old merge commit and workflow, so it would skip again. Each of
the four PRs needs a **new** `pull_request` event so that its merge ref includes current main: a
push to the PR branch by its owner, or a draft then ready-for-review toggle. This ticket may not
touch other agents' branches or PRs, so that step is left to each PR's owner or the merge steward.

UNVERIFIED: that the installer job then passes on each PR. It has not run there yet.
