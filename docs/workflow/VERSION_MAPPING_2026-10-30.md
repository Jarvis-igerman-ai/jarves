# Version scope mapping — 30 October 2026

Baseline: `a966a2350cec496d27ca9ab4c86f0747bb5e2e35`. This file explains the scope definition in `core/src/jarves/workflow/version_plan.json`. It is **not a completion report**. No current status is copied into the baseline; live issue/PR, artifact and device evidence must be reconciled separately.

## Coverage

| Version | Requirements | Scope covered |
|---|---:|---|
| V4.5.1 | 15 | Tracking #505, the release-agent plan and lean-process delivery prerequisites. |
| V5 | 27 | Mobile packages A–I; Goals, Adaptive Memory, Proactive Intelligence and Development Manager; project manager, owner defaults, Tailscale, acceptance, operational gaps and release. |
| V6 | 19 | All 17 numbered work items, plus optional phone location and optional paid/live traffic. |
| V7 | 33 | Every explicit slice: A0–A7, B1–B6, R1–R2, D1–D4, G1–G7, M1–M5 and optional X1. |

**Total: 94 requirements; 18 have no granular issue mapping yet.** This covers the selected canonical scope documents, not every issue or later feature ever added to the repository. Newly discovered unmatched issues must stay visible as unmapped work; they must not silently expand a version or disappear from the board. Optional requirements must not inflate mandatory release denominators.

## Sources and interpretation

- V5 mobile contract: `docs/JARVES_SW_V5_WORK_PACKAGE.md`; broader baseline and gaps: `docs/archive/plans/V5_INVENTORY_AND_PLAN_2026-10-24.md`, `docs/plans/V5_PILLARS.md` and the V5 sections of `docs/ROADMAP.md`. Real-phone rows: `docs/reviews/SWV5_ACCEPTANCE.md`.
- V6 numbered scope and pillar acceptance: `docs/archive/plans/V6_INVENTORY_AND_PLAN_2026-10-25.md`, with the six pillars retained from `docs/ROADMAP.md`.
- V7 exact slice table: `docs/plans/V7_WORK_PACKAGE_2026-10-26.md`. Every issue mapping and source acceptance condition from that table is retained.
- V4.5.1: public tracking issue #505, `docs/plans/AGENT_2_RELEASE_PROMPT_2026-10-27.md` and `docs/plans/LEAN_PROCESS_V2_2026-10-27.md`. Related PR #614 is stored as `related_pr_numbers`, never as an issue. CodeQL alerts 46/47 are not GitHub issue identities.

## What the data must not imply

- A closed linked issue does not prove the requirement was implemented, merged, installed, tested on a device or released. Even a merged PR is only implementation evidence.
- Mapping a phone-goal-page issue to the Goals requirement does not mean that issue covers every Goals acceptance criterion. Criteria have stable IDs and require their own evidence.
- `required_validation` records the strongest relevant evidence class for the requirement as a whole. Automated subcriteria may be completed independently; device-gated work remains pending rather than being skipped.
- `depends_on`, `owner_gate` and `scope` are planning metadata, not permission grants or executable work orders. The scheduler must not automatically turn all imported requirements into coding-agent jobs.
- `source_paths` identify scope, not runtime instructions. Old source statements about authorization, release gates or the current state are not made active by importing this baseline. The current user authorization and the actual release policy remain separate.

## Conflicts that need reconciliation

1. ROADMAP calls V7 proposal-only, but the V7 package records explicit execution approval on 25 Oct. Preserve the historical record; do not copy the older proposal label into live completion state.
2. The V7 table makes A0 depend on V5 acceptance; the execution order says A0 is documentation that may start now. Other passages gate A1+ or only A2+/B4. This baseline preserves the explicit A0 dependency and the local table dependencies; it does not resolve authorization or open device gates automatically. The manager must record the resolved dependency policy before auto-scheduling Android work.
3. V5/V6 inventory plans are archived snapshots whose wording says no code exists or PRs cannot start. Current code and linked issue histories have since advanced; these statements are not present-day completion facts.
4. V6 checklist and older plans mention nightly/bug-table release gates that RELEASING and release_gate.py retire. The baseline uses “current release gate” in release criteria rather than reinstating the retired checks.
5. V5 archive gap G2 names the current organization while still describing a stale repository name. Verify actual constants and tests; do not treat that sentence as evidence of a current defect.
6. V5 gap G20 says no tracking issues exist, but #181–#194 now identify specific gaps. This is historical scaffolding, not a new product requirement to recreate duplicate issues.
7. V5 gaps G18/G19 move hands-free phone voice and live coding-session push/steer out of V5. They are not silently dropped: hands-free is tracked in V6; supported harness/session control is in V7. No always-on phone wake or steering-wheel replacement claim is introduced.
8. “4.5” is a product milestone mapped in NOW to tag v4.0.1, while 4.5.1 is a follow-up planning label. Do not infer the installed build number or a release tag from a version-plan ID.

## Granular issue links still missing

These rows are deliberate reconciliation work. The underlying scope is known; the issue/PR/evidence linkage has not been proven. Do not replace the missing link with a fabricated issue, an unrelated sequential number or a shared acceptance issue.

| Requirement | Scope |
|---|---|
| `V451-WAKE` | החלת מודל wake לאחר בדיקת בעלים |
| `V451-SKILL-REDACTION` | הסתרת סודות בטיוטות Skills |
| `V451-MERGE-PROOF` | שימוש חוזר בראיית CI ותור merge רזה |
| `V451-INSTALL-LATEST` | התקנה ובדיקה של beta בפקודה אחת |
| `V451-RELEASE-RUN` | שחרור מוכן ומגובה ראיות |
| `V451-WATCHDOG` | משגיח חיצוני לתור ולבריאות השחרור |
| `V5-PAIRING` | צימוד טלפון, QR, token והרשאות מצומצמות |
| `V5-MOBILE-API` | API מאומת לטלפון |
| `V5-CORE-WIRING` | חיבור הטלפון ל־Core ולכלי השליטה |
| `V5-ALWAYS-ON` | שרת הטלפון עולה עם ה־Core כשהיכולת דולקת |
| `V5-PROJECT-MANAGER` | ניהול משימות תוכנה וסוכנים ב־worktrees |
| `V5-TAILSCALE` | Wi-Fi ביתי ו־Tailscale לפי בחירת בעלים |
| `V5-PHONE-APPROVALS` | אישורים מטלפון כבויים כברירת מחדל |
| `V5-PRIVACY-DEFAULTS` | ברירות מחדל לפרטיות ולהאצלה |
| `V5-LIVE-AGENTS` | הדגמת Claude/Codex אמיתיים |
| `V5-LIVE-GOAL` | תוכנית מטרה עם Skill אמיתי |
| `V5-PHONE-PERF` | מדדי שימוש אמיתיים בטלפון |
| `V6-LIVE-TRAFFIC` | traffic או routing בתשלום — אופציונלי |

## Remaining audit before a trustworthy “done” view

1. Read live issue and merged-PR evidence at a known sync time, including unmatched later work. Keep source freshness and failures visible.
2. Correlate each criterion with exact tests, installed artifact/build, target and device evidence. Unknown remains unknown.
3. Resolve the 18 missing granular mappings and Android dependency ambiguity without creating duplicate tracker engines.
4. Confirm release scope and optional/deferred exclusions with the current owner decisions. Answers are retained; an absent owner blocks only the dependent task.
5. Mark version completion only after its mandatory criteria, actual release evidence and required owner acceptance satisfy the current gates.

Validation performed for this baseline: JSON parses; 94 IDs are unique; all dependency IDs resolve; all 33 V7 source-table slices are represented; every source path exists. No claim of runtime, device or release verification is made.
