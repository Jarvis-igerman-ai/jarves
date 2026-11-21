# JAR-651: why "Windows installer (build + silent install smoke)" did not pass on #1336, #1344, #1387, #1424

**Finding: the job is not broken on current main. On these four PRs it was never started (skipped), and the
PRs are held by the Tier R `no-automerge` label, not by a red installer job.** This change adds no code.

## Evidence (read 2026-11-21)
- Latest `jarves-checks` run of each PR is green, with the installer job **skipped**:
  #1336 run 44656103279, #1344 run 44577302915, #1387 run 44652347702, #1424 run 44716794224.
  The `changes` log of 44716794224 prints `installer=false` although the PR changes `core/src/jarves/auto_update.py`.
- Cause: those runs tested a base without JAR-584. The rule that starts the installer on update and release
  paths (`PR_INSTALLER` in `core/tools/ci_plan.py`, commits 35a5c8a and 700ad61) reached main in #1417 (merge
  5b29bd8, 2026-11-20 03:29 UTC). #1424's recorded base `eadd904` (02:30 UTC) does not contain 35a5c8a
  (`git merge-base --is-ancestor 35a5c8a eadd904` fails).
- On current main the planner starts the installer for all four file lists:
  `python3 core/tools/ci_plan.py classify --event pull_request --files-from <PR files>` prints `installer=true`
  for each. The regression rows already exist in `core/tests/test_ci_speed_tools.py` (#1344 and #1336 paths).
- The job itself passes on the current main tree: merge-group run 44742446883 (#1490) has the installer build,
  all four smoke jobs, and upgrade and rollback all `success`.
- All four PRs report `mergeable_state=clean` and carry `no-automerge` (Tier R: release workflow, update
  write path). Under the operating model the steward never merges them; each one waits for an Opus sign-off.

## Same-file pairs
- #1344 and #1424 both make `auto_update.core_busy` fail closed when the approvals probe raises (#1100). They
  are duplicates: only one should merge.
- #1336 (#544, a tag-ref guard on the publish step) and #1387 (#605, owner-signed tag check) both edit
  `.github/workflows/release.yml` but fix different issues. They are not duplicates, and closing either one
  would drop a fix.

## What is left (outside this ticket's permissions)
1. Refresh the heads of #1424 and #1344 onto main (each PR's own owner or fixer, no merge-main push by others).
   The new run will build and smoke-test the installer. That result is UNVERIFIED here.
2. The Tier R reviewer chooses which of #1344 or #1424 to keep and closes the other.
3. `stall:check_broken` and `pr_not_merging` come from the factory, not this repo. They stay open until steps 1
   and 2 are done. UNVERIFIED.
