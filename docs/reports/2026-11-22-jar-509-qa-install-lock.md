# JAR-509: QA install of beta 4.0.1-beta.436 failed on `install.lock` (blocked)

**Verdict: blocked. No code change in `jarves`.** The QA install job (`job-20261117T230441Z`, 18 Nov 01:14 Israel,
installer `4.0.1.3064`) stopped with exit 2 before any step ran (`"steps": []`) because
`C:\factory-eval\factory2\qa\data\installs\install.lock` already existed. Nothing was installed, so nothing needed a
rollback ("Rolled back: no" is expected here, not a second failure).

## Evidence
- The message `another install holds ... install.lock`, the `installs\install.lock` file, the
  `program_rolled_back` field and the `job-*.result.json` file are not produced by any code in this repo.
  Reproduce on `origin/main` (`2398189`):
  `git grep -nI -e "another install holds" -e "program_rolled_back" -e "install.lock" origin/main` finds no match.
- The only install lock in `jarves` is `install-latest.lock` (`core/src/jarves/install_latest.py:40`), a different
  file with a stale-lock age guard (`pending_run`). The QA job did not use it.
- So the lock that blocked the job belongs to the Factory 2 QA install harness under `C:\factory-eval\factory2\qa`,
  which lives outside this repo.

## Likely cause (UNVERIFIED)
A previous QA install job ended without freeing `install.lock` (crash, kill or timeout), or two jobs overlapped.
The lock file contents (pid and time) and the previous job's result file were not available here.

## Question for the owner
Where is the Factory 2 QA install harness code kept? The fix (free the lock in a `finally`, and treat a lock whose pid
is gone or which is older than the longest install as stale) has to be made there, not in `jarves`.
Until then, removing the stale `install.lock` by hand after checking no installer is running lets the next job run.
