# Early PR overlap preflight — 24 October 2026

## User-authorized bounded speed/conflict improvement

The owner requested faster PR integration and fewer conflicts, without weakening the two required checks. This small slice adds a read-only pre-edit collision preflight for Claude Code/Codex and a focused test suite. It **does not** alter branch protection, merge policy, full-test selection, release/sign-off, or enable broad V4.5 implementation.

## Evidence-driven target

In an 8-PR recent merged sample (#110, #107, #80, #108, #109, #106, #105, #104), central files were edited repeatedly: `dashboard.py` appeared in 5, `app.py` and `cli.py` in 4, and `brain.py` in 3. Distinct feature work touching these files concurrently is a foreseeable merge-conflict/retest hotspot. Existing split catalogs, `pages.d`, and `changelog.d` already reduce other shared-file contention; preserve them.

## New workflow

Before work on likely shared files:
```shell
python core/tools/pr_overlap.py --files core/src/jarves/dashboard.py core/src/jarves/app.py
```
Before marking a PR ready (from the current worktree, with `origin/main` available):
```shell
python core/tools/pr_overlap.py
```

- 0: no exact file overlap in the current open-PR snapshot; no guarantee of semantic non-conflict
- 1: shared files are being edited; coordinate owner, pick another scoped task or serialize those edits
- 2: GitHub/pr list unavailable or incomplete; never misrepresent a failed scan as clear
- Script reads at most 30 open PRs, four concurrent read-only API calls; no paid LLM, no CI or remote mutation
- Scope includes staged, unstaged and untracked edits and deleted paths; an explicit planned-path mode works before any edits
- Guidance is in both AGENTS.md (authoritative for Claude/Codex) and docs/GITHUB_FLOW.md; no new manager/orchestrator

## Verification and acceptance

- Offline tests: path normalization, exact-file comparison, self-PR exclusion, pagination, missing/incomplete GitHub evidence, exit codes and open-PR limit.
- Hosted CI/CodeQL on the exact PR head; post-merge `main` CI and merge checks must be green before claiming complete.
- Expected outcome: fewer **avoidable** overlapping-file PRs and expensive conflict resolution, not a measured 70% reduction yet. Track conflicts per 100 comparable PRs against a fresh post-23-Oct baseline.
