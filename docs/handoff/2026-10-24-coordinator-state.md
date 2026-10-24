# Coordinator state and next steps — 24 Oct 2026 (evening, Israel time)

Written by the coordinator session so the next agent can start without the chat history. Facts below were read from GitHub and the owner PC on 24 Oct 2026.

## State
- Stable is still **4.0.0.153** (unsigned by a manual test, by owner decision). The beta channel is at **4.0.1-beta.33 (4.0.1.349)**, published automatically on each green `main`. Main's version is 4.0.1.
- Owner PC: updated to 4.0.1.349 (beta.33) on 24 Oct with the in-app updater (`jarvis update install --yes --silent`). Backup before it: `%LOCALAPPDATA%\jarves\backups\jarves-backup-20261024-180217-pre-migration.zip`. `jarvis doctor`: all checks passed. Core was not started on the owner's data. Rollback: `jarvis update --rollback --yes`. The installed command is `%LOCALAPPDATA%\Programs\JARVES\jarvis.cmd` (the `jarvis` on PATH in a terminal can still be the V3 checkout `C:\work\jarvis`).
- Owner rule: install the newest beta on the owner PC automatically and report; the owner reverts if there is a problem.

## Open pull requests
- **#121** "JARVES advisor with central Performance Health GitHub tracker" (branch `feat/perf45-advisor-autonomous-issues-20261024`, 13 files): BLOCKED. The `validate` job of "Fast CI and documentation validation" failed (run 40110880934), so the core, browser, installer and other jobs were skipped. Not reviewed yet. Next: read the failing `validate` log, fix, then check the diff (risk, tests, guide/docs rules, no secrets) and merge on its own (CLAUDE.md pull request flow). It touches V4.5 scope, so check it against `docs/plans/V4_5_STABILIZATION_SECURITY_QA.md`.

## Open issues (all on igalgerman/jarves)
- Work: **#122** V4.5 real voice latency and device acceptance; **#89** CI/merge/release friction; **#77** conversation controls (mic mute, appearance, CLI parity); **#71** lazy tool exposure (-30% prompt size, 4.0.2); **#61** self-awareness + improvement tickets; **#60** sign-off wizard; **#58** installer does all configuration + one setup wizard.
- Plans/proposals: **#50** V7 proposal. Consolidated legacy gates, close only with evidence: **#1**, **#2**, **#3**, **#4**, **#5**.

## Owner decisions that still apply
- No big integration batches: check each green PR, merge on its own, never while a run on main is in progress; low-risk classes (docs, tests, guides, Dependabot patch/minor) merge by `automerge` label.
- One wizard in the dashboard (`#/setup`) = set up + test + learn per topic. First run = baseline of everything since 1.0.0; later releases = diff + short smoke. The first signed stable is 4.0.1 or later. Every stable release needs the owner's sign-off.
- Cheaper models: Haiku for status/CI/audits, Sonnet for code, Opus only for plans and security. Few agents; agents rely on CI for full-suite evidence. Times to the owner in Israel time.
- Only `igalgerman/jarves` is writable. `igalgerman/JARVIS` is deprecated; never push there.

## Needs the owner (not done, cannot be done by an agent)
- Start Core 4.0.1.349 on his data when he wants; check that his custom wake model (threshold 0.95) is still applied after the migration (`doctor` shows the default hey_jarvis at 0.5).
- Run the wizard baseline; check the installer Setup window and the Hebrew console fix by eye.
- Real Android phone V5 tests (phone connected over adb), WhatsApp link (read-only first), optional admin key.
- Branch protection on `main` is not set yet; set it once the required check name (`CI result (single required check)`) is stable.

## Cleanup still open
- About 50 old agent worktrees `C:\work\jarves-*` / `jarvis-*`: list them to the owner, then remove only merged ones. New worktrees go in `C:\work\wt\<branch>`.

## Not verified in this note
- Nothing was built or tested; this is a status document. The PR #121 failure cause is not yet read.
