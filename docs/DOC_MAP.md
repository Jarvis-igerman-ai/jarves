# DOC_MAP — the single index: where to find what

Entry point for every document. Last aligned: 24 Oct 2026 for V4.5 planning (older version-status snapshots below remain historical until implementation). [README](README.md) (user view) and [CONTEXT_MAP](CONTEXT_MAP.md) (agent view, open only what the task needs) point here instead of keeping their own full lists. **Browse by topic:** [README](README.md) (curated subject navigation). The exhaustive registry stays here; existing document paths remain stable to preserve agents and links. A test (`core/tests/test_docs_links.py`) fails if a file under `docs/` is missing from this map or a relative link breaks: **add a row when you add a document**.

Status words: **current** (maintained), **historical** (kept as evidence/context, a banner at the top says what replaced it), **superseded** (replaced by the named doc), **proposal** (not authorized).
Owner doc = the one document that wins on a conflict.

## 1. Where am I? Per version

| Version | Status (updated 24 Oct 2026 for V4/V4.5; other rows may be earlier snapshots) | Read in this order |
|---|---|---|
| **V3** | Released (3.0.0.x installers). Historical. | [V3 package](JARVES_SW_V3_WORK_PACKAGE.md), [integration report](reviews/SWV3_INTEGRATION_REPORT.md) |
| **V4** | **Feature development CLOSED (owner decision 24 Oct).** 4.0.0 delivered and subsequent 4.0.1 changes exist; the remaining real-device, security, release and acceptance gaps are tracked in V4.5. Do **not** infer stable sign-off or close outstanding Issue acceptance. | [ROADMAP](ROADMAP.md) (V4 section), [release notes](RELEASE_NOTES_4.0.md), [V4 package](JARVES_SW_V4_WORK_PACKAGE.md) (plan and test scripts), [checklist](reviews/SWV4_RELEASE_CHECKLIST.md), [owner decisions](plans/PRIORITIES.md), [user guide](USER_GUIDE.md) |
| **V4.5** | **Owner-approved 24 Oct; bounded implementation authorized in NOW**: close V4 quality/acceptance gaps, security, recurring scans, camera UX, **early fast PR/merge/conflict reduction alongside security**, guided QA + voice Skill. No release authorized. | [V4.5 work package](plans/V4_5_STABILIZATION_SECURITY_QA.md), [ROADMAP](ROADMAP.md), [NOW](NOW.md). Canonical interactive QA issue: [#60](https://github.com/Jarvis-igerman-ai/jarves/issues/60) |
| **V5** | **Partially developed**: phone access, goals, memory, proactive, continuity, dev manager and project manager have merged (`implemented_unverified`); inventory in [V5 plan](plans/V5_INVENTORY_AND_PLAN_2026-10-24.md). Real Android and other user/device acceptance remains open; older open-PR listings in ROADMAP are historical. | [ROADMAP](ROADMAP.md) (V5 section), [V5 package](JARVES_SW_V5_WORK_PACKAGE.md), [acceptance sheet](reviews/SWV5_ACCEPTANCE.md), [security review](reviews/SWV5_SECURITY_REVIEW.md) |
| **V6** | Approved roadmap only (six pillars), gated on V5. No code. | [ROADMAP](ROADMAP.md) (V6 section), [PRODUCT_VISION](PRODUCT_VISION.md) stage 3, [V6 inventory and plan](plans/V6_INVENTORY_AND_PLAN_2026-10-25.md), [Android Auto feasibility](plans/V6_ANDROID_AUTO_FEASIBILITY_2026-10-25.md) |
| **V7** | Owner-approved 25 Oct 2026 for planning and execution; work package written, slices queued behind V4.5/V5/V6 gates (issue #50). | [V7 work package](plans/V7_WORK_PACKAGE_2026-10-26.md), [V7 proposal](plans/V7_PROPOSAL_2026-10-21.md) |

Execution authority: [NOW](NOW.md) (the only active brief) with [AGENTS](../AGENTS.md). Status log: one note per task in [handoff/](handoff/README.md) (newest by file name); [HANDOFF](HANDOFF.md) keeps the history up to 23 Oct 2026 and the decision queue. Planning and status per version: [ROADMAP](ROADMAP.md).

Current dashboard review: [23 Oct findings inventory and recheck gates](reviews/DASHBOARD_FINDINGS_23OCT2026.md) (current; owner: Python Core/runtime diagnostics).

Phone acceptance result (25 Oct): [phone sheet result](reviews/PHONE_SHEET_RESULT_2026-10-25.md).
Self-diagnosis plan (25 Oct): [self-diagnosis plan](plans/SELF_DIAGNOSIS_PLAN_2026-10-25.md).

Smart chat blocks and task triage design (26 Oct, #364 #365): [design](plans/SMART_CHAT_AND_TASK_TRIAGE_DESIGN_2026-10-26.md). Dashboard issues triage (26 Oct): [triage](handoff/2026-10-26-dashboard-issues-triage.md).

## 2. Testing and manual testing

| What | Where | Who runs it |
|---|---|---|
| Automatic suites (unit, integration, docs links, guides, Hebrew texts, CLI parity) | `core/tests`; CI workflow `.github/workflows/jarves-checks.yml` (Linux and Windows, CAD, Godot). Targeted tests locally, the full suite only in CI | Agents locally (targeted), CI for the full evidence |
| Performance gate | `core/perf_budgets.toml`, `core/tools/perf_gate.py`, nightly workflow; reports [M](reviews/SWV4_M_PERF_CI.md), [budgets](reviews/SWV4_PERF_BUDGETS.md), [perf](reviews/SWV4_PERF.md), [idle writes](reviews/SWV4_IDLE_WRITES.md) | CI on every push, nightly; agents on perf changes |
| Voice suites (silent, virtual mic/speaker, real GPT-Live inside the budget) | `core/tools/silent_live.py`, `suite_voice.py`, `suite_music.py`; campaign reports [V3](reviews/SWV3_CAMPAIGN_VOICE.md), [V4](reviews/SWV4_CAMPAIGN_VOICE.md); bug tables [V3](reviews/SWV3_CAMPAIGN_BUG_TABLE_VOICE.md), [V4](reviews/SWV4_CAMPAIGN_BUG_TABLE_VOICE.md) | Agents (live spend capped, OD-01 $25 total) |
| Feature-by-feature and dashboard campaign | [V3 features](reviews/SWV3_CAMPAIGN_FEATURES.md), [V4 features](reviews/SWV4_CAMPAIGN_FEATURES.md), [V3 dashboard](reviews/SWV3_CAMPAIGN_DASHBOARD.md); method: [FULL_TEST_PLAN](plans/FULL_TEST_PLAN.md) (passes A-F) | Agents on a test profile |
| Self-test and speed test | `jarvis selftest`, `jarvis speedtest`; findings are triaged for each release (see [CLAUDE.md](../CLAUDE.md)) | Agents and owner |
| Emulated phone test (headless browser as the phone, V5) | `core/tests/test_mobile_phone_e2e.py` (merged, #51/#96; CI browser job); the HTTP harness is `core/tests/test_mobile_*.py` | CI |
| Manual sheets per version | V3: [FULL_TEST_PLAN](plans/FULL_TEST_PLAN.md) pass E. V4: [V4 package, owner script (section 7)](JARVES_SW_V4_WORK_PACKAGE.md#7-testing--manual-owner-script) and agent script (section 8), [INSTALL_TEST](INSTALL_TEST.md), `docs/reviews/MANUAL_TEST_4.0.0.md` (PR #46, not merged). V5: [SWV5_ACCEPTANCE](reviews/SWV5_ACCEPTANCE.md) (real Android phone) | Owner |
| Future dynamic manual QA / spoken guidance | [V4.5 work package](plans/V4_5_STABILIZATION_SECURITY_QA.md#workstream-f--living-html-manual-qa-workspace--conversational-qa-skill-p1-60-canonical) reuses #60, `release_signoff.py` and one test registry; **planned**, not a second current sign-off route | Owner + JARVES after explicit implementation authorization |
| Release sign-off | `jarvis release signoff --version X --row ID --result pass\|fail\|blocked`, then `--finish`; described in [RELEASING](RELEASING.md) ("Stable release, step by step"); per version a checklist `reviews/RELEASE_CHECKLIST_<version>.md` and a sheet `reviews/MANUAL_TEST_<version>.md` (4.0.0 used [SWV4_RELEASE_CHECKLIST](reviews/SWV4_RELEASE_CHECKLIST.md)) | Owner records rows, agents prepare the evidence |
| Bug triage before a release (4.1 and 5.0 on) | `docs/reviews/*BUG_TABLE*.md`, open issues on Jarvis-igerman-ai/jarves, selftest/speedtest findings; no open P0-P2 | Release agent; owner decides P3 |

## 3. Release and repository process

| Document | Purpose | Version | Status | Owner doc |
|---|---|---|---|---|
| [RELEASE_HOSTING](RELEASE_HOSTING.md) | How signed installers and `latest.json` are published to the releases repo and how clients update | V2 on | current (the source repo is now `Jarvis-igerman-ai/jarves`; `RELEASING.md` and `CI_PERFORMANCE.md` join with PR #44) | itself |
| [GITHUB_FLOW](GITHUB_FLOW.md) | Branch, PR, `automerge` label, post-merge package | general | current | itself |
| [RELEASE_NOTES_4.5](RELEASE_NOTES_4.5.md) | What 4.5 (v4.0.1) changed for users since 4.0.0, upgrade notes and known issues | 4.0.1 | current | itself; [CHANGELOG](../CHANGELOG.md) is the short per-version list |
| [RELEASE_NOTES_4.0](RELEASE_NOTES_4.0.md) | What V4 changed for users | V4 | current | itself; [CHANGELOG](../CHANGELOG.md) is the short per-version list |
| [SWV4_RELEASE_CHECKLIST](reviews/SWV4_RELEASE_CHECKLIST.md) | 4.0.0 checklist state and evidence | V4 | current | [V4 package section 10](JARVES_SW_V4_WORK_PACKAGE.md#10-release-checklist) defines the rows |
| [RELEASE_CHECKLIST_4.0.1](reviews/RELEASE_CHECKLIST_4.0.1.md) | 4.0.1 stable release checklist (read by the release gate) | 4.0.1 | current | [RELEASING](RELEASING.md) |
| [SIGN_OFF_4.0.1](release/SIGN_OFF_4.0.1.md) | 4.0.1 owner sign-off sheet (what is auto-checked, manual rows with times, one signature, gate dry run) | 4.0.1 | current | [RELEASING](RELEASING.md) |
| [EVIDENCE_4.0.1](release/EVIDENCE_4.0.1.md) | 4.0.1 final evidence: bug-table and checklist statuses with proof | 4.0.1 | current | [SIGN_OFF_4.0.1](release/SIGN_OFF_4.0.1.md) |
| [OWNER_REVIEW_4.0.1](release/OWNER_REVIEW_4.0.1.md) | 4.0.1 owner review summary before sign-off | 4.0.1 | current | [SIGN_OFF_4.0.1](release/SIGN_OFF_4.0.1.md) |
| [RELEASE_CHECKLIST_5.0.0](reviews/RELEASE_CHECKLIST_5.0.0.md) | 5.0.0 stable release checklist, draft (read by the release gate) | 5.0 | draft | [RELEASING](RELEASING.md) |
| [ENGINEERING_REVIEW_GATES](ENGINEERING_REVIEW_GATES.md) | Standing review and challenge gates | general | current | itself |
| [CI_PERFORMANCE](CI_PERFORMANCE.md) | CI speed, shards and the fast release path | general | current | [RELEASING](RELEASING.md) |
| [Performance Advisor](performance_advisor.md) | Local evidence-based JARVES speed recommendations and one opt-in rolling GitHub issue (all confirmed metrics) | quality/operations | under validation | [CI_PERFORMANCE](CI_PERFORMANCE.md) |
| [RELEASING](RELEASING.md) | How a release is built, signed and published | general | current | [CI_PERFORMANCE](CI_PERFORMANCE.md) |
| [CI_DETERMINISM](CI_DETERMINISM.md) | Network guard, offline stub, flake retry and quarantine for deterministic CI | general | current | [CI_PERFORMANCE](CI_PERFORMANCE.md) |
| [PREPUSH](PREPUSH.md) | The local gate `python core/tools/prepush.py` (same quick checks as CI), the optional pre-push hook, and the PR failure-summary comment | general | current | [CI_PERFORMANCE](CI_PERFORMANCE.md) |
| [handoff/2026-10-23-jarvis-talk](handoff/2026-10-23-jarvis-talk.md) | Handoff: `jarvis talk` live conversation without the wake word (CLI, dashboard, tray) | voice | current | [HANDOFF](HANDOFF.md) |
| [handoff/2026-10-27-session-45-release](handoff/2026-10-27-session-45-release.md) | Handoff after the JARVES 4.5 (v4.0.1) release: shipped, state, held PRs, issues, next steps, resume commands | 4.5 | current | [HANDOFF](HANDOFF.md) |
| [AGENT_HANDOFF_TEMPLATE](AGENT_HANDOFF_TEMPLATE.md) | Copy-paste resume prompt for a new agent | general | current | [AGENTS](../AGENTS.md) |
| [handoff/README](handoff/README.md) | Handoff notes, one file per task (`docs/handoff/<date>-<topic>.md`, newest by file name); the notes are covered by this row | general | current | itself |
| [reports/README](reports/README.md) | New reports, reviews and audits, one dated file each (`docs/reports/<date>-<topic>.md`); the reports are covered by this row, so adding one does not edit this file | general | current | itself |
| [MIGRATION_STATUS](MIGRATION_STATUS.md), [MIGRATION_HANDOFF](MIGRATION_HANDOFF.md) | Private-to-public repository migration ledger | general | current until the private repo is archived | MIGRATION_STATUS |

## 4. Product, plans, work packages

| Document | Purpose | Version | Status | Owner doc |
|---|---|---|---|---|
| [NOW](NOW.md) | The one active brief (V4.5 bounded authorization, 24 Oct 2026; the previous text is in reviews/NOW_SWV1_18OCT2026) | general | current | itself |
| [ROADMAP](ROADMAP.md) | V4 to V7 status, definitions of done, dependencies | V4-V7 | current | itself |
| [PRODUCT_VISION](PRODUCT_VISION.md) | Six product stages (north star) | general | current | itself |
| [PROJECT_PRINCIPLES](PROJECT_PRINCIPLES.md) | Durable principles, printability, Israel sourcing, owner decisions | general | current | itself |
| [plans/TESTING_PLAN_2026-10-26](plans/TESTING_PLAN_2026-10-26.md) | Testing plan: automatic tests with local commands and CI jobs, owner-only tests, flake policy, release gates per version, test gaps | all | current (26 Oct 2026) | itself |
| [plans/V4_5_STABILIZATION_SECURITY_QA](plans/V4_5_STABILIZATION_SECURITY_QA.md) | V4.5 single plan: issues/evidence closure, CodeQL/security cadence, camera UX, code integration and dynamic HTML manual QA + voice Skill. Reuse #60/#58/#61 and existing release gate. | V4.5 | **proposal only**, no implementation authorized | itself for scope; NOW for execution; ROADMAP for status |
| [plans/V4_5_ANYDO_MCP_INTEGRATION](plans/V4_5_ANYDO_MCP_INTEGRATION.md) | V4.5 P2 optional Any.do official MCP Skill: OAuth, read/create/reminders, human-approved update feasibility (#140) | V4.5 | proposal, NOT implementation-authorized | [V4.5 work package](plans/V4_5_STABILIZATION_SECURITY_QA.md) + NOW |
| [plans/ORG_MIGRATION_MERGE_QUEUE](plans/ORG_MIGRATION_MERGE_QUEUE.md) | Move the source repo into a free GitHub org for the native merge queue: inventory (URLs, tokens, secrets, ruleset, merge_group), steps, rollback | V4.5 (E3, #160) | **plan only**, awaits owner approval and org name | itself; #160 |
| [plans/BROWSER_PILOT_REAL_DRIVER_2026-10-27](plans/BROWSER_PILOT_REAL_DRIVER_2026-10-27.md) | What a real browser driver for the pilot must do before it is wired: proxy pinning, WebSocket/WebRTC/service worker, ports, tests and order | V7 B1 (#566, #574) | design note | itself; #574 |
| [plans/RELEASE_ENVIRONMENT_544_2026-10-27](plans/RELEASE_ENVIRONMENT_544_2026-10-27.md) | Plan for the protected `stable-release` environment of the release workflow: gate and publish jobs, tag re-check, owner order of steps, tests | 4.5 release (#544) | **plan only**, applies after the code freeze and the owner's environment setup | itself; #544 |
| [plans/PROCESS_RETRO_AND_AGENT_PLAN_2026-10-27](plans/PROCESS_RETRO_AND_AGENT_PLAN_2026-10-27.md) | Retro of 24-27 Oct and the 3-agent operating plan: risk-tiered review, automation list, coordinator rules | after 4.5 | **plan only**, awaits owner OK | itself |
| [plans/AGENT_2_RELEASE_PROMPT_2026-10-27](plans/AGENT_2_RELEASE_PROMPT_2026-10-27.md) | Operating prompt for the release agent (agent 2): 4.5.1 -> 5.0 -> 6.0 -> 7.0 | 4.5-7.0 | operating prompt, not a plan | [plans/PROCESS_RETRO_AND_AGENT_PLAN_2026-10-27](plans/PROCESS_RETRO_AND_AGENT_PLAN_2026-10-27.md) |
| [plans/PROJECT_MANAGER](plans/PROJECT_MANAGER.md) | Software project manager: JARVES splits a project into small tasks run by short-lived coding agents (Claude Code, Codex, OpenCode) in parallel worktrees; design after Opus review | V5 (4.0.1 beta) | built, real-agent runs unverified | `core/tests/test_projectmgr.py` |
| [plans/SW_FACTORY](plans/SW_FACTORY.md) | Software factory: flexible per-task stages (plan/work/test/review/merge/release, bug-fix), any actor (JARVES/owner/agent/list/sub-agents), debate between agents, GitHub optional off/read/write | V5 (4.0.1 beta) | built, cloud agents deferred, real-agent runs unverified | `core/tests/test_projectmgr.py` |
| [plans/PRIORITIES](plans/PRIORITIES.md) | Owner decisions log (V4 approval, WhatsApp, 21 Oct decisions) | V4-V5 | current, append-only | itself |
| [plans/RELEASE_AND_TEST_SYSTEM](plans/RELEASE_AND_TEST_SYSTEM.md) | Release and test system plan: alpha/beta/stable, `next` batches, 12 shards, scenario runner, sandbox, owner sheet < 15 min | 4.1-V5 | plan for owner review (increments on PRs #44/#57) | [RELEASING](RELEASING.md), [CI_PERFORMANCE](CI_PERFORMANCE.md) |
| [SW_V1_WORK_PACKAGE](SW_V1_WORK_PACKAGE.md) | First assistant, CI, music | V1 | historical | [CHANGELOG](../CHANGELOG.md) |
| [JARVES_SW_V2_IMPLEMENTATION_SPEC](JARVES_SW_V2_IMPLEMENTATION_SPEC.md) | Milestones M1-M6 spec | V2 | historical | CHANGELOG |
| [JARVES_SW_V3_WORK_PACKAGE](JARVES_SW_V3_WORK_PACKAGE.md) | V3 prioritized package | V3 | historical (released) | CHANGELOG |
| [JARVES_SW_V4_WORK_PACKAGE](JARVES_SW_V4_WORK_PACKAGE.md) | V4 packages A-O, test scripts (sections 6-9), release checklist, owner decisions | V4 | current for plan and test scripts; delivery state lives in ROADMAP and HANDOFF | ROADMAP for status |
| [JARVES_SW_V5_WORK_PACKAGE](JARVES_SW_V5_WORK_PACKAGE.md) | V5 phone-access package (security model, PWA) | V5 | current; the other V5 pillars are in ROADMAP | ROADMAP |
| [plans/V7_PROPOSAL_2026-10-21](plans/V7_PROPOSAL_2026-10-21.md) | V7 research proposal | V7 | proposal | ROADMAP |
| [plans/PHONE_APP_REDESIGN_2026-10-26](plans/PHONE_APP_REDESIGN_2026-10-26.md) | Phone app redesign (#511, #509): ChatGPT-like chat as home, side drawer, design tokens, RTL, 390 px audits, real-phone test | V5 phone | slice 1 in progress | Owner order 26 Oct |
| [plans/ANDROID_DAILY_COMPANION_ADR_2026-10-26](plans/ANDROID_DAILY_COMPANION_ADR_2026-10-26.md) | ADR (A0, #388): PC-hosted Core first, PWA plus thin native shell, offline basics, FGS/mic limits, push without a cloud server, permissions, security | V7 (track A) | proposed, docs only | V7 work package (26 Oct) |
| [api/companion_v1](api/companion_v1.md) | Companion API v1 contract (A1, #389): phone API routes, scopes, errors, versioning (JSON twin: api/companion_v1.json) | V7 (track A) | implemented, unverified | ADR A0 |
| [plans/R1_OPENCLAW_AND_ALTERNATIVES_2026-10-26](plans/R1_OPENCLAW_AND_ALTERNATIVES_2026-10-26.md) | Research (R1, #402): OpenClaw, nanobot, IronClaw; adopt/adapt/reject table, Python Core stays | V7 (track R) | research note, docs only | V7 work package (26 Oct) |
| [plans/R2_BUZZ_BLOCK_INVESTIGATION_2026-10-26](plans/R2_BUZZ_BLOCK_INVESTIGATION_2026-10-26.md) | Research (R2, #403): Buzz by Block, licence, maturity, patterns, risks; reject adoption, adapt patterns | V7 (track R) | research note, docs only | V7 work package (26 Oct) |
| [plans/V7_WORK_PACKAGE_2026-10-26](plans/V7_WORK_PACKAGE_2026-10-26.md) | V7 work package: tracks, slices and issues, gates, caps, owner needs, hard stops | V7 | approved plan, execution gated | NOW |
| [plans/WEBTOP_PARENT_STUDENT](plans/WEBTOP_PARENT_STUDENT.md) | Guardian/student Webtop — read-only homework, messages and possible timetable | F11 / V7.x candidate | proposal, not authorized | FUTURE_IDEAS |
| [plans/WEBTOP_TEACHER_WORKSPACE](plans/WEBTOP_TEACHER_WORKSPACE.md) | Separate teacher/staff Webtop — reports, schedule, AI use and school privacy gates | separate future candidate | proposal, not authorized | FUTURE_IDEAS |
| [plans/WEBTOP_SCHOOL_SKILL_PROPOSAL](plans/WEBTOP_SCHOOL_SKILL_PROPOSAL.md) | Compatibility stub for prior combined School Skill plan | historical | superseded by two above | two above |
| [FUTURE_IDEAS](FUTURE_IDEAS.md) | Deferred backlog (F01-F11) | general | backlog | ROADMAP |
| [plans/FEATURE_SUGGESTIONS](plans/FEATURE_SUGGESTIONS.md) | Ranked feature ideas | general | backlog | FUTURE_IDEAS |
| [plans/SW_NEXT_STEPS](plans/SW_NEXT_STEPS.md) | Old "next steps" after V3 | V3 | superseded by ROADMAP and the V4 package | ROADMAP |
| [plans/FULL_TEST_PLAN](plans/FULL_TEST_PLAN.md) | Full manual test campaign method (V3) | V3-V4 | historical, method reused | V4 package sections 6-9 |
| [plans/MULTI_DEVICE_REMOTE](plans/MULTI_DEVICE_REMOTE.md) | Satellites and Tailscale remote design | V5-V7 | design; the Tailscale part was decided on 21 Oct (PRIORITIES) | ROADMAP |
| [plans/WHATSAPP_REAL_ACCESS](plans/WHATSAPP_REAL_ACCESS.md) | WhatsApp options and bridge design | V4 | background, built | [SWV4_F_WHATSAPP](reviews/SWV4_F_WHATSAPP.md) |
| [plans/GOOGLE_CALENDAR_GMAIL_SKILLS](plans/GOOGLE_CALENDAR_GMAIL_SKILLS.md) | Google skills plan | V2-V3 | historical (built) | [GOOGLE_SETUP](GOOGLE_SETUP.md) |
| [plans/CAMERA_PLAN](plans/CAMERA_PLAN.md) | Camera plan phases | V2-V3 | historical plan | [CAMERA_SETUP](CAMERA_SETUP.md) |
| [plans/INSTAGRAM_CHANNEL](plans/INSTAGRAM_CHANNEL.md) | Instagram channel design | V3 | historical (live verification open) | features table |
| [plans/CODE_SIGNING_FREE](plans/CODE_SIGNING_FREE.md) | Free code-signing options | V3-V4 | open; owner chose unsigned for now (OD-08) | PRIORITIES |
| [plans/VIRTUAL_COMPUTER_JUSTIFICATION](plans/VIRTUAL_COMPUTER_JUSTIFICATION.md) | Whether a virtual computer is needed | V4 | open question | V4 package |
| [JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS](JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md) | User controls, safe shutdown, debug logging requirements | V1-V4 | current requirements | itself |

## 5. Using and building JARVES

| Document | Purpose | Version | Status | Owner doc |
|---|---|---|---|---|
| [USER_GUIDE](USER_GUIDE.md), [USER_GUIDE.he](USER_GUIDE.he.md) | End-user guide, English and Hebrew (same sections, tested) | V4 | current | in-app guides in `core/src/jarves/guides/data/` |
| [INSTALL_TEST](INSTALL_TEST.md) | Windows installer owner test | V4 | current | itself |
| [GOOGLE_SETUP](GOOGLE_SETUP.md) | Google connection | V3 | current | itself |
| [CAMERA_SETUP](CAMERA_SETUP.md) | Camera and privacy rules | V3 | current | itself |
| [APPEARANCE](APPEARANCE.md) | Face, 2D/3D avatar | V2 | current | itself |
| [SKILL_CREATION](SKILL_CREATION.md) | Drafted skills, grants, sandbox | V3 | current | itself |
| [OPENAI_ADMIN_KEY](OPENAI_ADMIN_KEY.md) | Optional read-only usage key | V4 | current | itself |
| [ARCHITECTURE](ARCHITECTURE.md) | Layers and model pipeline | general | current | itself |
| [DEVICE_CONTRACT](DEVICE_CONTRACT.md) | Simulated vs physical device contract | general | current | itself |
| [FACE_RUNTIME](FACE_RUNTIME.md) | Same face renderer on desktop and device | general | current | itself |
| [core/README](../core/README.md) | Run and debug the Core, CLI reference | general | current | itself |
| [README](../README.md), [README_HE](../README_HE.md), [CHANGELOG](../CHANGELOG.md), [AGENTS](../AGENTS.md), [CLAUDE](../CLAUDE.md) | Repo entry, Hebrew entry, per-version changes, agent rules | general | current | AGENTS for rules |
| [bridge/whatsapp/PROTOCOL](../bridge/whatsapp/PROTOCOL.md), [bridge README](../bridge/whatsapp/README.md) | WhatsApp bridge protocol and build | V4 | current | itself |
| [cad/ASSUMPTIONS](../cad/ASSUMPTIONS.md), [godot/README](../godot/README.md), [print/QUOTE_PACKAGE](../print/QUOTE_PACKAGE.md) | CAD, Godot and print notes | physical | historical until hardware resumes | ROADMAP |
| [assembly/ASSEMBLY_GUIDE](assembly/ASSEMBLY_GUIDE.md) | R5b assembly guide (not print approved) | physical | historical | reviews S1 |

## 6. Archive (not read by default)

[archive/DECISIONS](archive/DECISIONS.md), [archive/HARDWARE](archive/HARDWARE.md), [archive/MECHANICAL](archive/MECHANICAL.md), [archive/OPEN_ITEMS](archive/OPEN_ITEMS.md), [archive/PRODUCT](archive/PRODUCT.md), [archive/ROADMAP](archive/ROADMAP.md) (superseded by [ROADMAP](ROADMAP.md)), [archive/SOURCES](archive/SOURCES.md). All historical.

## 7. Evidence reports (docs/reviews, never deleted)

Reports and bug tables are evidence of a past run, not instructions. A package's report owns that package's results.

| Group | Files |
|---|---|
| Hardware and design (physical) | [ISRAEL_HARDWARE_SOURCING](reviews/JARVES_ISRAEL_HARDWARE_SOURCING_2026-10-17.md), [PEBBLE_PARTS_INVENTORY](reviews/PEBBLE_PARTS_INVENTORY.md), [S1_DESIGN_REVIEW](reviews/S1_DESIGN_REVIEW.md), [S1_T03R1](reviews/S1_T03R1_ENGINEERING_REVIEW.md), [S1_T03R2](reviews/S1_T03R2_PEBBLE_VISUAL.md), [S1_T03R3](reviews/S1_T03R3_ASSEMBLY_REVIEW.md), [S1_T03R4 final](reviews/S1_T03R4_FINAL_RECOMMENDATION.md), [S1_T03R4 simple](reviews/S1_T03R4_SIMPLE_BUILD_PROPOSAL.md), [S1_T03R5B](reviews/S1_T03R5B_IMPLEMENTATION_REVIEW.md), [S1_T03R5 architecture](reviews/S1_T03R5_ARCHITECTURE_REVIEW.md), [S1_T03R5 decision](reviews/S1_T03R5_DESIGN_DECISION.md) |
| V1 | [SW_V1_REVIEW](reviews/SW_V1_REVIEW.md), [SW_V1_TECH_DECISION](reviews/SW_V1_TECH_DECISION.md) (stack and costs, still the technology reference) |
| Pre-use tests (V1-V2) | [music](reviews/PRE_USE_TEST_REPORT_MUSIC.md), [system](reviews/PRE_USE_TEST_REPORT_SYSTEM.md), [voice](reviews/PRE_USE_TEST_REPORT_VOICE.md) |
| V2 packages | [CONTEXT_TRACE](reviews/SWV2_CONTEXT_TRACE.md), [M2](reviews/SWV2_M2.md), [PRIVACY_STRIP](reviews/SWV2_PRIVACY_STRIP.md), [REVIEW_FIXES_SCHED_INSTALLER](reviews/SWV2_REVIEW_FIXES_SCHED_INSTALLER.md), [SECURITY](reviews/SWV2_SECURITY.md), [WORKSPACE_MEMORY](reviews/SWV2_WORKSPACE_MEMORY.md), [WP-A](reviews/SWV2_WP-A.md), [WP-B](reviews/SWV2_WP-B.md), [WP-C](reviews/SWV2_WP-C.md), [WP-E](reviews/SWV2_WP-E.md), [WP-F](reviews/SWV2_WP-F.md), [WP-G](reviews/SWV2_WP-G.md), [WP-H](reviews/SWV2_WP-H.md), [WP-I](reviews/SWV2_WP-I.md), [WP-J](reviews/SWV2_WP-J.md), [WP-L](reviews/SWV2_WP-L.md), [WP-M1](reviews/SWV2_WP-M1.md), [WP-M3](reviews/SWV2_WP-M3.md), [WP-M4](reviews/SWV2_WP-M4.md), [WP-M5](reviews/SWV2_WP-M5.md), [WP-M6](reviews/SWV2_WP-M6.md), [WP-N](reviews/SWV2_WP-N.md), [WP-O](reviews/SWV2_WP-O.md), [WPK](reviews/SWV2_WPK.md) |
| V3 packages and campaign | [APPROVALS](reviews/SWV3_APPROVALS_REPORT.md), [CAMPAIGN_BUG_TABLE](reviews/SWV3_CAMPAIGN_BUG_TABLE.md), [BUG_TABLE_DASH](reviews/SWV3_CAMPAIGN_BUG_TABLE_DASH.md), [BUG_TABLE_VOICE](reviews/SWV3_CAMPAIGN_BUG_TABLE_VOICE.md), [DASHBOARD](reviews/SWV3_CAMPAIGN_DASHBOARD.md), [FEATURES](reviews/SWV3_CAMPAIGN_FEATURES.md), [VOICE](reviews/SWV3_CAMPAIGN_VOICE.md), [CONNECT](reviews/SWV3_CONNECT_REPORT.md), [INSTALL_ADDONS_AUDIT](reviews/SWV3_INSTALL_ADDONS_AUDIT.md), [INTEGRATION](reviews/SWV3_INTEGRATION_REPORT.md), [P01_BUG_TABLE](reviews/SWV3_P01_BUG_TABLE.md), [P01](reviews/SWV3_P01_REPORT.md), [P02C](reviews/SWV3_P02C_REPORT.md), [P02](reviews/SWV3_P02_REPORT.md), [P03](reviews/SWV3_P03_REPORT.md), [P04_BUG_TABLE](reviews/SWV3_P04_BUG_TABLE.md), [PROFILES](reviews/SWV3_PROFILES_REPORT.md), [SELFCHECK](reviews/SWV3_SELFCHECK_REPORT.md) |
| V4 packages | [B privacy](reviews/SWV4_B_PRIVACY.md), [B sinks](reviews/SWV4_B_SINKS.md), [C review](reviews/SWV4_C_REVIEW.md), [D delegation](reviews/SWV4_D_DELEGATION.md), [E orchestrator](reviews/SWV4_E_ORCHESTRATOR.md), [E research](reviews/SWV4_E_ORCHESTRATOR_RESEARCH.md), [F WhatsApp](reviews/SWV4_F_WHATSAPP.md), [G recall](reviews/SWV4_G_RECALL.md), [H dialing](reviews/SWV4_H_DIALING.md), [HL docs note](reviews/SWV4_HL_DOCS_NOTE.md), [K dashboard](reviews/SWV4_K_DASHBOARD.md), [L installer](reviews/SWV4_L_INSTALLER.md), [M perf CI](reviews/SWV4_M_PERF_CI.md), [O self-diagnostics](reviews/SWV4_O_SELFDIAG2.md), [R-01 defense](reviews/SWV4_R01_DEFENSE.md), [idle writes](reviews/SWV4_IDLE_WRITES.md), [perf](reviews/SWV4_PERF.md), [perf budgets](reviews/SWV4_PERF_BUDGETS.md), [wake train](reviews/SWV4_WAKE_TRAIN.md) |
| V4 campaign | [bug table](reviews/SWV4_CAMPAIGN_BUG_TABLE.md), [voice bug table](reviews/SWV4_CAMPAIGN_BUG_TABLE_VOICE.md), [features](reviews/SWV4_CAMPAIGN_FEATURES.md), [voice](reviews/SWV4_CAMPAIGN_VOICE.md) |
| V5 | [acceptance sheet](reviews/SWV5_ACCEPTANCE.md), [security review](reviews/SWV5_SECURITY_REVIEW.md), [V5 pillars security review](reviews/V5_SECURITY_REVIEW_PILLARS_2026-10-25.md), [5.0 bug table](reviews/BUG_TABLE_V50_2026-10-25.md) |
| V6 | [V6 security and privacy review](reviews/V6_SECURITY_REVIEW_2026-10-25.md), [6.0 bug table](reviews/BUG_TABLE_V60_2026-10-25.md) |
| Raw suite output | [V3 voice suite raw](reviews/swv3_campaign_voice/SUITE_RAW_REPORT.md), [V4 voice suite raw](reviews/swv4_campaign_voice/SUITE_RAW_REPORT.md) |
| Previous NOW brief (SW-V1 era, 18-24 Oct 2026, unchanged) | [NOW_SWV1_18OCT2026](reviews/NOW_SWV1_18OCT2026.md) |
| Screenshots | `docs/reviews/swv2_*`, `swv3_*` image folders (evidence only) |

## 8. Added by the 4.0.1 integration batch

| Document | Purpose | Version | Status | Owner doc |
|---|---|---|---|---|
| [BUGFIX_LANE](BUGFIX_LANE.md) | Bugfix lane: from a problem JARVES finds in himself to a fixed release | 4.1+ | current | [reviews/BUG_TABLE_4_1](reviews/BUG_TABLE_4_1.md) |
| [GLOSSARY_HE](GLOSSARY_HE.md) | Hebrew glossary: one Hebrew term per concept in guides and the dashboard | general | current | [USER_GUIDE](USER_GUIDE.md) |
| [MIGRATION_AUDIT](MIGRATION_AUDIT.md) | Legacy repository migration audit (20 Oct 2026) | general | historical | [reviews/SWV4_P01_VERIFY](reviews/SWV4_P01_VERIFY.md) |
| [PHONE_ACCESS_TAILSCALE](PHONE_ACCESS_TAILSCALE.md) ([HE](PHONE_ACCESS_TAILSCALE_HE.md)) | Phone access over Tailscale, setup guide | V5 | current | [plans/V5_PILLARS](plans/V5_PILLARS.md) |
| [plans/V5_PILLARS](plans/V5_PILLARS.md) | V5 pillars 2 and 3: Goals and Adaptive Memory (software slice) | V5 | current | [ROADMAP](ROADMAP.md) |
| [plans/WHATSAPP_GREENAPI_FLOWAPI_EVAL](plans/WHATSAPP_GREENAPI_FLOWAPI_EVAL.md) | GREEN-API and Flow API evaluation for WhatsApp (research only) | V5+ | research | [plans/WHATSAPP_REAL_ACCESS](plans/WHATSAPP_REAL_ACCESS.md) |
| [reviews/BUG_TABLE_4_1](reviews/BUG_TABLE_4_1.md) | Bug table 4.1, first sweep | 4.0.1 | current | [BUGFIX_LANE](BUGFIX_LANE.md) |
| [reviews/BUG_TABLE_V45_2026-10-24](reviews/BUG_TABLE_V45_2026-10-24.md) | Bug table V4.5 stabilization, triage of 24 Oct (read by release_gate.py) | 4.0.1 | current | [RELEASE_CHECKLIST_4.0.1](reviews/RELEASE_CHECKLIST_4.0.1.md) |
| [reviews/MANUAL_TEST_4.0.0](reviews/MANUAL_TEST_4.0.0.md) | Manual test sheet for 4.0.0 (owner) | 4.0.0 | current | [RELEASING](RELEASING.md) |
| [reviews/MANUAL_TEST_4.0.1](reviews/MANUAL_TEST_4.0.1.md) | Manual test sheet for 4.0.1 (owner sign-off on the beta) | 4.0.1 | current | [RELEASING](RELEASING.md) |
| [reviews/SWV4_P01_VERIFY](reviews/SWV4_P01_VERIFY.md) | Owner-assisted verification, ported from the legacy branch | V4 | historical | [MIGRATION_AUDIT](MIGRATION_AUDIT.md) |
| [reviews/V45_VOICE_LATENCY_2026-10-24](reviews/V45_VOICE_LATENCY_2026-10-24.md) | V4.5 voice latency baseline (issue #122, automatic measurement) | V4.5 | current | [BUGFIX_LANE](BUGFIX_LANE.md) |

| [Windows installer #58](INSTALLER_58.md) | Installer setup, backup, rollback and measured timings | V4 | current | itself |
