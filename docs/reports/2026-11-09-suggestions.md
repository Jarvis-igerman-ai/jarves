Report: improvement suggestions from repo evidence and the factory trial (JAR-67), status proposal (9 Nov 2026). Docs only; nothing implemented, nothing decided.

# Suggestions: 7 ideas with evidence, cost, benefit, tier

Sources: `gh pr list` / `gh run list` on 9 Nov 2026, `core/tests/quarantine.toml`, `docs/reports/2026-11-04-ci-speed.md`,
`docs/reports/2026-11-09-reliability-audit.md`. Run times are `startedAt` to `updatedAt`, read from history, nothing re-run.
Sample is small (about 40 `jarves-checks` runs, 8 to 9 Nov); treat numbers as indicative. Tier: R = never auto-merged, N = ordinary, L = low.

| # | Idea | Tier | Cost | Benefit |
|---|------|------|------|---------|
| 1 | Update-path probes fail closed | R | S | Stops an update restart during an approval or coding session |
| 2 | Fix `bridge.py:129` Windows PermissionError | N | S | Removes a red PR cause on shard 2/4 |
| 3 | Merge backlog: 24 open PRs, 10 older than 5 Nov | process | S | Less rebase and conflict churn |
| 4 | Measure CI queue wait in `ci_health.py` | N | S | Shows if the 25 min PR median is wait or work |
| 5 | Drop the duplicate full PR matrix (merge group re-tests) | R | M | Largest CI saving; needs owner call |
| 6 | Nightly workflow: pin ffmpeg and name check | R | S | Ends red nightlies (3 to 8 Nov) |
| 7 | Doc drift: retire legacy workflow text in CLAUDE.md/AGENTS.md | L | S | Fewer wrong instructions for every new agent |

## 1. Update-path busy probes fail closed (Tier R)
- Evidence: reliability audit finding 1: `auto_update.py:525-553`, four `except Exception: pass` around approvals, chat, orchestrator and quiet-hours probes; a probe error counts as "not busy".
- Cost: S (add a `probe_error` busy reason, log, one test). Benefit: no restart while an approval is pending. Risk: a broken probe delays updates; acceptable.
- Needs Opus review; do not auto-merge.

## 2. `orchestrator/bridge.py:129` PermissionError on Windows (Tier N)
- Evidence: PR #987 `jarves-checks` failed on "Python Core tests (windows-2025 shard 2/4)": `PermissionError: [WinError 5] Access is denied` at `bridge.py:129` (run 8 Nov 07:53Z). Related: #963/#976 kill-file timing flakes.
- Cost: S, but root cause UNVERIFIED (file lock by a second process vs. antivirus vs. test ordering). Benefit: one fewer random red. Debug budget applies.
- Check first whether this belongs to the lifted quarantine in #987 or is a new flake; if it repeats, quarantine with a `showstopper` issue per the rules.

## 3. Open PR backlog (process, no code)
- Evidence: 24 open PRs; 10 created before 5 Nov (e.g. #954, #958, #962, #964, #965, #971, #974). Several are overlapping flaky/quarantine batches (#964, #965, #971, #976, #987).
- Cost: S (owner or steward triage: close superseded, rebase the rest). Benefit: every stale PR is a future conflict and a CI run on reopen.
- No auto action: closing PRs is an owner decision.

## 4. Measure queue wait directly (Tier N)
- Evidence: CI speed report (4 Nov): PR median 24.6 min vs target under 8; heavy jobs start 4 to 9 min late, shards then take about 10 min. This week PR runs range about 1 to 26 min (e.g. 05:57Z 26 min, 06:08Z 21 min).
- Cost: S (job `created_at` to `started_at` in `core/tools/ci_health.py`, one test). Benefit: separates wait from work before anyone changes the matrix. Also the cheapest step before idea 5.

## 5. Skip the full PR matrix when the merge group re-tests (Tier R)
- Evidence: PR runs 12 to 26 min; merge-group runs 1 to 15 min on the same code; 25 jobs per full run (CI speed report option 1).
- Cost: M plus owner decision on what proof a PR needs; `jarves-checks.yml` is Tier R. Benefit: largest runner-minute and wall-time cut. Risk: red main moves from PR time to queue time. Do after idea 4 gives numbers.

## 6. Nightly red since 3 Nov (Tier R)
- Evidence: PR #986 diagnoses nightly red 3 to 8 Nov as missing ffmpeg in `nightly.yml` (#480); `gh run list --workflow jarves-nightly.yml` returns 404 because the file name differs from the run name `jarves-nightly`, so scripts keyed on the file name miss it.
- Cost: S, workflow edit (Tier R, owner review). Benefit: nightly becomes a usable signal again. Merge #986 diagnosis first.

## 7. Doc drift: legacy workflow instructions (Tier L)
- Evidence: `CLAUDE.md` and `AGENTS.md` still tell agents to use the workflow ledger, `workflow_console.py bootstrap`, `next --task`, hooks; `OPERATING_MODEL.md` is marked DEPRECATED (8 Nov) but is still `@`-imported, 226 lines loaded into every session. `docs/NOW.md` last changed 31 Oct. Factory agents must be told in a separate policy to ignore all of it.
- Cost: S (docs-only PR to replace the imports with a 10-line current-process note); owner must approve since these are owner-authored instructions. Benefit: less context per run, no contradictory rules.
- Risk: removing content the owner still wants for rollback. The file stays in the repo, only the import goes.

## Suggested order
1, 4, 6 (small, high value, clear) then 2 (needs diagnosis), 7 and 3 (owner decisions), 5 last (needs idea 4 data).

## Not verified
Counts are from one day of history; flake root causes (idea 2) are unconfirmed; no idea was tried locally.
