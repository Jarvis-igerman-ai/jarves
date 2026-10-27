# 24 October 2026 — merge speed follow-up (owner-authorized bounded batch)

## Baseline
- PR #118 (tests/tools, no PR installer): 8.3 min open→merge, PR CI 8.0 min.
- Its merged-main full CI #40095556503 re-ran the full test matrix for 9.8 min because the existing code-tree installer artifact did not exist. Its slowest Windows shard took 528s vs approximately 360s for other Windows shards.
- This does not indicate a failed CI: required checks, CodeQL, and release candidate passed.

## Change and safety
1. A second **green full-tests-only** proof artifact uploaded only by the successful required PR check. It is different from and cannot substitute the signed/verified Windows installer artifact.
2. `main` may reuse that exact-tree PR proof to avoid rerunning duplicate tests, but must still build, install, upgrade/rollback smoke-test and publish a real installer. No proof / in-progress / failed / foreign → full matrix.
3. Exact green PR proof feeds the `main` shard-duration cache so test planning does not regress.
4. CI timing summaries identify Windows/Linux skew and slow files, to guide next targeted improvements instead of multiplying concurrent runners.

## Manual checks following integration
1. Check this workflow-changing PR has **green required CI and CodeQL**. It will run the full test matrix, because changes to core/tools/ci_plan.py and jarves-checks.yml are high-risk.
2. Verify `main` after merge has green CI, CodeQL and installer/package.
3. Open a **real tests-only** PR modifying `core/tests/test_*.py`, confirm it is classified `code=true, full=true, installer=false`; proof artifact exists in its successful `ci-ok` run. After merger verify new `main` logs show `reuse_tests_only=true`, core/browser/etc skipped by proven exact-tree PR success, and installer build + all functional/upgrade smoke checks pass.
4. Time PR→merge and merge→green `main` against the baselines. Do not claim savings or skip coverage based only on a unit mock.

## Notes
- Does not change branch protection, required checks, auto-merge permissions, release signing, CodeQL, or actual test assertions.
- Latest measured results and any unavailable real-device checks should remain truthful and visible; no sign-off of V4.5 as a whole.
