# JAR-509: QA install of beta 4.0.1-beta.436 refused by `install.lock` (blocked: code not in this repo)

**Verdict: BLOCKED. No JARVES code change.** The factory QA job `job-20261117T230441Z` (beta 4.0.1-beta.436, kind
daily, stage install, exit 2) stopped before the installer ran: its result has `"steps": []` and the message
`another install holds C:\factory-eval\factory2\qa\data\installs\install.lock`. Nothing was installed, so nothing
needed a rollback (`program_rolled_back: false`).

## Evidence
- The message, the lock path and the result fields (`steps`, `program_rolled_back`) come from the factory 2 QA install
  harness under `C:\factory-eval\factory2\qa\`. None of them exist in this repository:
  `git grep -n "another install holds\|install\.lock\|program_rolled_back" origin/main` finds no match
  (checked on `origin/main` `76cd30c`, 2026-11-21). A GitHub code search for the message in the organisation finds none.
- The nearest JARVES lock is `install-latest.lock` in `core/src/jarves/install_latest.py` (different name, different
  folder: `<home>/updates/`), which already treats a lock older than `PENDING_S` as stale. It does not produce this
  message.

## What is needed
The harness that owns `qa\data\installs\install.lock` must be fixed where it lives: either a second QA install job ran
at the same time (then the job should wait or be skipped, not reported as a failed beta), or an earlier job died and
left the lock (then the harness should treat a lock whose PID is gone, or which is older than the longest install, as
stale). Which of the two happened is UNVERIFIED: the lock file contents and the list of QA jobs around
2026-11-17 23:04 UTC are only on the factory PC.

## Question for the owner
Should JAR-509 be moved to the factory 2 repository (QA harness), or does the owner want the QA install harness copied
into this repo first? Until then a re-run of the 4.0.1-beta.436 install, after removing the stale lock by hand when no
install job is running, is the only check of the beta itself.
