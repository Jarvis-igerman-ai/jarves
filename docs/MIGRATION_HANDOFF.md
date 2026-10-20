# JARVES — Public Repository Migration Handoff

**As of:** 20 October 2026. **Status:** PARTIALLY MIGRATED, NOT RELEASE-READY.

## Source of truth and owner decision

- **ONLY active writable repository:** [`igalgerman/jarves`](https://github.com/igalgerman/jarves), public, branch `main`.
- Legacy [`igalgerman/JARVIS`](https://github.com/igalgerman/JARVIS) is **DEPRECATED / READ-ONLY** as a development source; it is **not yet GitHub-archived**. Do not push/commit/create issues there. Never synchronize public changes *back* to private.
- This migration is approved; it is **not** approval to merge incomplete V4 features or to start V5/V6 coding. Preserve credential material, real session traces, personal logs, machine-specific research and user/account details outside the public repository. Public branch review does not equal release acceptance.
- The owner approved V5 and V6 as future roadmap directions. [ROADMAP](ROADMAP.md) records V5 Goes Mobile (vision stage 2) and V6 Drive & Act (vision stage 3). Family Intelligence is retained as [F11](FUTURE_IDEAS.md#f11--family-intelligence-deferred-not-v5), *not* V5.

## Work safely moved to public feature branches

These are **open DRAFT PRs**, based on public main. Counts were compared against PR/file lists; file preservation is not proof of integrated behavior.

| PR | Public branch | Files | Content preserved | Must still integrate/verify |
| --- | --- | ---: | --- | --- |
| [#6](https://github.com/igalgerman/jarves/pull/6) | `migration/v4-whatsapp-go-review` | 23 | V4 Go bridge protocol, policy, encrypted storage, fake engine and tests | Real WhatsApp engine, Core bridge connection, production auth, tests, approval and linked-device risk |
| [#7](https://github.com/igalgerman/jarves/pull/7) | `migration/v4-orchestrator-review` | 26 | E Orchestrator modules, Claude/Codex adapters, fixtures, fakes, dashboard JS and guide | Existing Core/CLI/dashboard/config integration, supported session controls, real CLI validation and security |
| [#8](https://github.com/igalgerman/jarves/pull/8) | `migration/v4-selfdiag2-review` | 28 | O diagnostics modules, selective AI review, health UI, canaries, replay and tests | Existing Core/CLI/dashboard/config/CI hooks, privacy controls, thresholds, cost caps and real operation |
| [#9](https://github.com/igalgerman/jarves/pull/9) | `migration/v4-delegation-isolation-review` | 12 | D strict-budget delegation and R01 helper isolation new code/tests | Existing voice/budget/helper/approval integration, OS enforcement, security, CLI/UX, live test with budget |
| [#10](https://github.com/igalgerman/jarves/pull/10) | `migration/v4-dashboard-review` | 3 | K dashboard all-pages and locale helper tools | Complete modified EN/HE Dashboard files, removal of obsolete locales, accessibility and real browser QA |

**Total: 92 added files.** All 5 PRs remain Draft and **none is merged**; no CI/run-on-owner-PC success is being claimed. The private V4 sources are not themselves ready to merge: divergent history and partially overlapping dashboard/CLI/CI files require review.

## Source branches & why they are not blindly merged

- Private baseline `v4-final`: integrated V4 work, but **not a substitute for public `main`**. It carries many modified files and historical local decisions. Record exact ancestry before choosing code changes.
- Private `v4-e-orchestrator`: compared against `v4-final` it adds **26 files** but modifies **12** integration files. Added files moved to PR #7; integration changes remain.
- Private `v4-f-whatsapp-go`: adds **23 bridge files** compared to `v4-final`, moved to PR #6. The unofficial linked-device integration is **not yet proven**; no linking/sending on the owner's main WhatsApp number.
- Private `v4-o-selfdiag2`: adds **28** new files and modifies **19** existing files compared to `v4-final`. Only additions moved to PR #8.
- Private `v4-k-dashboard`: introduces **3** files and modifies/removes others (29 changed files total); new tools moved to PR #10.
- Private `v4-d-delegation` and `v4-r01-defense`: relevant new D/R01 sources/tests moved to PR #9. Integrations in modified files must be reconciled with current public code, never replaced wholesale.
- Already-public V4 B/C/G/M commits exist, but actual CI release state, independent privacy/security review and owner-present acceptance on the release SHA must still be established; do not duplicate already-integrated code.

## Issue migration

- Original private repository has **17 open issues** at inventory time; the public repository initially had none. Their outstanding work has been summarized into the following 5 public issues:
  - [#1 V4 release / live acceptance](https://github.com/igalgerman/jarves/issues/1): historical #14, #15, #16, #17, #24, #25, #28.
  - [#2 Orchestrator](https://github.com/igalgerman/jarves/issues/2): historical #49.
  - [#3 Pi parity](https://github.com/igalgerman/jarves/issues/3): historical #18–23.
  - [#4 Physical design](https://github.com/igalgerman/jarves/issues/4): historical #3, #4, #8.
  - [#5 Repository consolidation](https://github.com/igalgerman/jarves/issues/5): migration ledger; tracks any missed historic requirements.
- Issue **numbers do not retain their old meaning** across repos. Always use canonical `owner/repo#number` or the full URL. Do not close the old private issues or destroy history merely to tidy.

## Required work: priority & handoff to next coding agent

**Priority 0 — protect active writers and data.** On the user's Windows worktrees, identify current agents and their worktrees/remotes **before** any rebases/branch cleanup. Do not assume old `origin` is public; inspect `git remote -v`, `git status`, `git branch -vv`, and running processes. Never delete dirty worktrees or reset user changes. Disable obsolete private push/mirror steps and update CI/docs references *in scoped PRs*; this is not complete remotely.

**Priority 1 — integrate V4 on public only.** Have **one integration owner** compare each private branch to its true base and the public main; reconcile **modified** files with minimal changes. Start from the smallest non-overlapping package; keep one bounded PR/issue scope at a time. Sequence based on dependencies: D/R01 security/delegation, E orchestration, O diagnostics, K dashboard, F WhatsApp; revise only if justified. De-duplicate overlapping app/CLI/i18n/CI edits and check API/feature flags. No bulk copy of `docs/HANDOFF.md` from private into public.

**Priority 2 — validate release honestly.** Run focused tests for each changed package, then cross-package integration and independent security/privacy review, final-sha CI and **real** owner-approved account/voice/device checks. Track passes vs fake/virtual/blocked; obey remaining spending and consent approvals from the V4 work package. Do not connect WhatsApp, spend paid calls, activate camera/mic or run live commands on the user's environment without scoped authorization.

**Priority 3 — clean and retire legacy.** Update docs/links/CI/Issues on public. Ensure all necessary source, tests, docs and decision records are preserved, and any personal data stays private. Archive private GitHub repo only after required code/issues and writer cleanup are verified; archiving itself is not yet done. The connector available in this chat lacks a repository-Archive write operation.

## Decisions / blockers / next reviewer evidence

| Item | Status | Next proof |
| --- | --- | --- |
| Public-only GitHub workflow | **DECIDED; README + AGENTS marked** | Check local remotes and agents obey it |
| V5 Goes Mobile and V6 Drive & Act | **ROADMAP approved; no code authorization** | Complete V4 release before later scoped plan |
| Public preservation of 5 V4 code slices | **DONE as Draft PRs** | Verify per-file source fidelity and security/CI |
| Modified integration files and end-to-end V4 | **NOT DONE** | Diff against public main; integrated PR and tests |
| Owner account / actual WhatsApp / physical mic, phone, Pi | **UNVERIFIED / user consent gate** | Owner-present test or explicitly accepted blocker |
| Private repo GitHub Archive | **NOT DONE** | No active writers, required sources safe, owner archive control |

## Compact report format (agents)

```text
Repo/branch/issue/PR:
Scope (approved?):
What changed (files):
Source of truth / source legacy ref:
Tests: command + result + environment (real/fake/virtual):
Security/data check:
CI on exact commit: URL or NOT RUN:
What is still blocked, by whom:
Commit URL + next single action:
```

**Never claim a draft PR is integrated, a mock is a real device test, or a documented plan is authorized implementation.** Link this file in the short active [HANDOFF](HANDOFF.md) entry. Read [AGENTS.md](../AGENTS.md) and [NOW.md](NOW.md) for operating rules.
