# JARVES — agent handoff template / copy-paste resume

**Owner direction, 20 October 2026:** The former private repository `igalgerman/JARVIS` is **OBSOLETE / DEPRECATED FOR ALL DEVELOPMENT**. It is a **read-only legacy source** for migration only and must receive **NO pushes, syncs, commits, issues, PRs or updates**. The **only active repository is `igalgerman/jarves` (public)**. Do not confuse repository deprecation with completed data migration: legacy V4 modified files and historical issues are not all reconciled; the old GitHub repository has not yet been archived.

## Copy/paste to Claude Code or Codex

```text
Continue ONLY the JARVES repository migration. Do not begin unrelated feature development or a general product/V4 stabilization campaign. IMPORTANT: the old PRIVATE igalgerman/JARVIS repo is DEPRECATED and NOT relevant for further development. igalgerman/jarves (PUBLIC) is now the ONLY writable repository/source of truth. The required PUBLIC migration has STARTED, but is NOT fully integrated. NEVER push/sync/code/change issues on the old private repo. Read old branches read-only only to recover missing source after privacy/secret review.

First read AGENTS.md and docs/NOW.md, then ONLY docs/MIGRATION_HANDOFF.md, the top of docs/HANDOFF.md and the one public Issue/PR directly relevant to your next scoped task. Inspect local git remote -v, git status, active worktrees and any running Claude/Codex writer; don't clobber dirty state or work on the same files concurrently.

Public migration issues: #1 V4 release/acceptance, #2 Orchestrator, #3 Pi parity, #4 future hardware, #5 consolidation.
Public DRAFT PRs: #6 WhatsApp Go (23 files), #7 Orchestrator (26), #8 Self-Diagnostics 2.0 (28), #9 Delegation/OS isolation (12), #10 Dashboard QA (3). Together 92 ADDED preserved files, not yet complete or merged functionality. Their original modified integration files (app/CLI/voice/dashboard/i18n/CI/config) must be reconciled safely with current PUBLIC main, not overwritten wholesale. Preserve private source research/logs outside public history.

Choose ONE scoped migration item in public only: review and port missing integration diffs, verify private-vs-public file fidelity, reconcile issue tracker/links/remotes, or prepare archival readiness. Do not undertake unrelated V4/V5/V6 product work. Run focused checks only to prove the migrated code is preserved and safe; no account linking or paid calls.

For each completed migration step: commit/push ONLY to a public migration branch; update the migration section at the top of docs/HANDOFF.md, docs/MIGRATION_HANDOFF.md and the associated PUBLIC Issue/PR. Record file source/target, privacy scan, targeted test/CI result, blockers and the next migration step. Do not merge unverified Draft PRs; do not archive the private repo until all necessary material and active writers are accounted for.

Return at most 6 concise lines: outcome; files; tests + context; CI final-SHA link or NOT RUN; blockers; public commit/PR and next step.
```

## Compact end-of-task handoff format

```text
REPO: igalgerman/jarves (only public; private repo obsolete)
SCOPE / PUBLIC ISSUE / PR:
BRANCH / COMMIT URL:
IMPLEMENTED: [exact changed files; old source ref if any]
TESTS: [commands, outcomes, real/fake/virtual]
SECURITY/PRIVACY: [checked / unverified]
CI ON EXACT SHA: [URL or NOT RUN]
BLOCKED / USER DECISIONS: [precise]
NEXT ONE TASK:
```

More detail (migration only): [MIGRATION_HANDOFF.md](MIGRATION_HANDOFF.md). No personal logs or secrets belong in this public repository.
