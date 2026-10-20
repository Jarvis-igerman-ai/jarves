# JARVES — legacy private repository migration status

**Date:** 20 October 2026  
**Owner decision:** **`igalgerman/jarves` (public) is the ONLY active writable source of truth** for code, docs, CI, issue tracking, release tags and forward work. **`igalgerman/JARVIS` (private) is DEPRECATED, read-only historical material until GitHub Archive.** Do not sync commits, open new Issues or push to the private repo. This file does not authorize V5/V6 coding or physical purchasing.

> **Migration != merge != release.** The public `main` intentionally still lacks some V4 code. Nothing in the draft PRs below is declared integrated, release-ready or tested merely because its files were copied.

## Source code recovered into public draft PRs

| Draft PR | Private source branch | Transferred into public branch | Still needed before merge |
| --- | --- | --- | --- |
| [#6 — WhatsApp Go bridge](https://github.com/igalgerman/jarves/pull/6) | `v4-f-whatsapp-go` | 23 new Go protocol/bridge/policy/storage files and fake tests | No verified real WhatsApp engine/linking; independent security, Go tests, Core wiring, owner consent |
| [#7 — Claude/Codex Orchestrator](https://github.com/igalgerman/jarves/pull/7) | `v4-e-orchestrator` | 26 newly added orchestrator modules, JS/guide and protocol fake/tests | Original 12 modified integration files **not** blindly copied; reconcile app/CLI/dashboard/permissions/agent protocol and verify real sessions |
| [#8 — Self-Diagnostics 2.0](https://github.com/igalgerman/jarves/pull/8) | `v4-o-selfdiag2` | 28 new diagnostic modules, dashboard/guide, fixtures and tests | Original 19 modified hooks/CI/runtime files not blindly copied; security, user-data redaction, approval gates, tests |
| [#9 — Delegation & helper isolation](https://github.com/igalgerman/jarves/pull/9) | `v4-final` | 12 source/guide/fake/tests absent from public `main` | Reconcile changes to existing runtime/approvals/config, validate strict cost cap and OS sandbox reality |
| [#10 — Dashboard QA](https://github.com/igalgerman/jarves/pull/10) | `v4-k-dashboard` | 3 new all-page and locale tooling files | 26 modified/deleted dashboard files require selective integration and real browser/RTL/CI checks |

**Total: 92 files recovered into five draft review branches.** Only files known to be new/absent from public main were copied; shared changed files remain intentionally *unmigrated* pending a competent three-way review. Branch PRs are based on public `main`; they do not automatically contain one another. Some may fail until integration wiring is completed. **No PR was merged, no account linked and no live paid test run as part of this docs/code transfer.**

The source migration applied basic checks for key patterns, recognizable private user paths and email; this is not a substitute for full secret scanning. No private `docs/HANDOFF.md`, personal conversation transcript, user account data or private runtime logs were knowingly published. Test fixtures may contain **synthetic planted credential patterns**, as designed for security tests. Independent public-side audit remains mandatory.

## Open issue migration (all 17 tracked; grouped to avoid duplicate noise)

| Public issue | Original private issue numbers | Scope |
| --- | --- | --- |
| [#1 — V4 release and real acceptance](https://github.com/igalgerman/jarves/issues/1) | 14, 15, 16, 17, 24, 25, 28 | Review/security/privacy/client-delegation, desktop release, real dashboard/Google/camera/voice QA |
| [#2 — Agent Orchestrator](https://github.com/igalgerman/jarves/issues/2) | 49 | Existing and managed Claude Code/Codex session control, real compatibility/testing |
| [#3 — Raspberry Pi parity](https://github.com/igalgerman/jarves/issues/3) | 18, 19, 20, 21, 22, 23 | Deferred Pi/Linux ARM64 feature parity, **not** verified on physical Pi |
| [#4 — Physical design/readiness](https://github.com/igalgerman/jarves/issues/4) | 3, 4, 8 | Deferred FreeCAD/Blender/Godot design and physical assembly/printing/purchasing gates |

These four consolidated public issues preserve the outstanding work at a **public-safe summary** level. The original private issues, comments and detailed history remain readable to authorized collaborators in the historical private repository; they have **not** been deleted or closed/transferred using GitHub's native issue-transfer feature. Follow-up tasks should be tracked as scoped subtasks on the **public** Issues only.

[Public issue #5 — repository consolidation](https://github.com/igalgerman/jarves/issues/5) owns the remaining migration/verification process.

## Outstanding migration gates before archiving private

1. Compare V4 integration branch `v4-final` and all remaining V4 heads against public `main`; especially reconcile modified source lines from D/E/F/K/O/R01, **not** just newly added files. Check `v4-wake-train`, `v4-p01-verify`, `v4-idle-writes`, and any V4 research not safely represented in public documentation. Confirm any required private-only design source assets/binaries are preserved without making confidential files public.
2. Integrate reviewed public PRs and check focused tests, CI run on final SHA, owner live gates, and actual supported service/device paths. Branch content migration alone does not satisfy acceptance.
3. Update stale public README/CONTEXT_MAP/HANDOFF/NOW/review links that still reference deprecated private repo; keep private notes private, and place safe status summaries into public docs. Do not blindly publish old private HANDOFF.
4. Verify any automation, Claude/Codex worktree, CI, documentation sync job and local Git remote are pointed at public `igalgerman/jarves`, not private. Freeze writers while switching; no simultaneous integration owners.
5. Recheck branch/issue coverage and no unique needed source, keys or materials would become inaccessible on GitHub Archive. The archive preserves history, but prevents further writes. **Archiving is intentionally not claimed as done**: no archive capability was available in the connected GitHub actions, and V4 integration is incomplete. Only owner may use GitHub Settings → General → Danger Zone → Archive once these gates pass.

## Roadmap continuity

- [PRODUCT_VISION](PRODUCT_VISION.md): six product stages.
- [ROADMAP](ROADMAP.md): **V5** completes stage 2 (PC-hosted mobile access + intelligence); owner-selected plan, not implemented.
- [ROADMAP](ROADMAP.md): **V6 — Drive & Act** completes stage 3 (Android Auto/phone car access) **with six approved pillars**: Driving Companion, Journey Intelligence, Drive Actions, Voice Engine 2.0, Contextual Briefings, Skills Platform 2.0. Owner-approved direction saved on 20 October 2026; **not active coding yet**.
- [FUTURE_IDEAS](FUTURE_IDEAS.md#f11--family-intelligence-deferred-not-v5): Family Intelligence parked, not discarded.

**Stop/go:** Complete approved V4 and its recovery/integration, then scope/authorize V5 separately, then V6. Keep `docs/NOW.md` as the execution contract; the roadmap and this migration inventory are not blanket implementation permission.
