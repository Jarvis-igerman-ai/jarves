# Project manager: intermittent Windows failure of `test_agent_blocked_and_failed_runs` (23 Oct 2026)

- Seen once: `main` run for PR #96 (`ed38387`), Windows shard 3/3: the "Boom" task ended `failed` with 1 attempt (test expects 2). The CI log has only the assertion, no task reason. It did not recur: runs for #92 and #98 on `main` are green.
- Not reproduced: Linux, 25 loops under CPU load and a 4-thread worktree stress test show no failure. So the exact cause is NOT proven. A `failed` task with 1 attempt can only come from `worktree failed` or `git failed` (a git command error), and parallel tasks run git against one repository.
- Hardening (probable cause, unverified): `gitops.py` now (1) takes a per-repository lock around worktree add/remove/prune and branch create/delete, and (2) retries a git command up to 4 times when it fails on a held `.lock` file (git itself or a Windows antivirus scan). Other git errors are never retried.
- The test now prints `(status, attempts, reason)` for each task when it fails, so a recurrence names its cause.
- Tests: `core/tests/test_projectmgr_git.py` (retry succeeds, retry gives up, other errors not retried, 4 parallel tasks create and replace worktrees) and `test_projectmgr.py` (56 pass).
- If it recurs: read the printed reason first; do not retry or skip the test.
