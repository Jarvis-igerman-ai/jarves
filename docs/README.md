# JARVES documentation — browse by topic / תיעוד לפי נושאים

**Start here to locate a document.** This page is for navigation, not a competing project plan. The **complete canonical index** (every doc, status and source of truth) is [DOC_MAP](DOC_MAP.md). Agents begin with [AGENTS.md](../AGENTS.md) and [NOW.md](NOW.md), then open only the relevant file from [CONTEXT_MAP](CONTEXT_MAP.md). **A proposal is not implementation authorization.**

## 1. מצב הפרויקט והחלטות / Status and roadmap

| Need | Source |
|---|---|
| What work is authorized now? | [NOW](NOW.md) |
| Current agent handoff (newest note by file name; HANDOFF.md = latest entry + pointers) | [handoff/](handoff/README.md), [HANDOFF](HANDOFF.md) |
| V4 / V5 / V6 / V7 status and gates | [ROADMAP](ROADMAP.md) |
| Long-term goals | [PRODUCT_VISION](PRODUCT_VISION.md) |
| Owner decisions | [PRIORITIES](plans/PRIORITIES.md), [PROJECT_PRINCIPLES](PROJECT_PRINCIPLES.md) |
| Deferred feature backlog | [FUTURE_IDEAS](FUTURE_IDEAS.md), [V7 proposal](archive/plans/V7_PROPOSAL_2026-10-21.md) |

## 2. שימוש יומיומי והתקנה / Everyday use and setup

| Topic | Source |
|---|---|
| Start, voice, dashboard, permissions and troubleshooting | [USER_GUIDE](USER_GUIDE.md) / [עברית](USER_GUIDE.he.md) |
| Installer check and updates | [INSTALL_TEST](INSTALL_TEST.md), [RELEASE_NOTES_4.0](RELEASE_NOTES_4.0.md), [CHANGELOG](../CHANGELOG.md) |
| Connect Google services | [GOOGLE_SETUP](GOOGLE_SETUP.md) |
| Camera and appearance | [CAMERA_SETUP](CAMERA_SETUP.md), [APPEARANCE](APPEARANCE.md) |
| Phone connection | [PHONE_ACCESS_TAILSCALE](PHONE_ACCESS_TAILSCALE.md) / [עברית](PHONE_ACCESS_TAILSCALE_HE.md) |
| Commands, local Core and troubleshooting | [core/README](../core/README.md) |
| Hebrew UI terminology | [GLOSSARY_HE](GLOSSARY_HE.md) |

## 3. אינטגרציות / Skills and external services

| Topic | Source | Scope / note |
|---|---|---|
| Google Calendar, Gmail, Contacts | [Google setup](GOOGLE_SETUP.md), [original plan](plans/GOOGLE_CALENDAR_GMAIL_SKILLS.md) | Verify actual permissions |
| WhatsApp | [WhatsApp research](plans/WHATSAPP_REAL_ACCESS.md), [alternatives](archive/plans/WHATSAPP_GREENAPI_FLOWAPI_EVAL.md) | Feature and research, see ROADMAP |
| **Webtop — guardian and student** | **[Parent / Student School Skill](archive/plans/WEBTOP_PARENT_STUDENT.md)** | **Standalone proposal**: homework, inbox, timetable |
| **Webtop — teacher workspace** | **[Teacher Workspace](archive/plans/WEBTOP_TEACHER_WORKSPACE.md)** | **Separate staff proposal**: teaching schedule, report tracking and safe AI preparation |
| Skill lifecycle, consent and sandbox | [SKILL_CREATION](SKILL_CREATION.md) | Established JARVES architecture |
| Future service ideas | [FUTURE_IDEAS](FUTURE_IDEAS.md) | Deferred; not implementation authorization |

**Role separation:** staff Webtop and guardian/student Webtop must not share credentials, caches, permissions, profiles or private data. Both documents contain research only; no real school account has been connected.

## 4. AI, architecture and coding agents

- [ARCHITECTURE](ARCHITECTURE.md) — Core, providers, model boundaries
- [SKILL_CREATION](SKILL_CREATION.md) — Skill definition, grants, approvals
- [V7 proposal](archive/plans/V7_PROPOSAL_2026-10-21.md) — Android, browser automation, GitHub, OpenCode, OpenRouter and research; **not approved for implementation**
- [AGENTS](../AGENTS.md), [AGENT_HANDOFF_TEMPLATE](archive/AGENT_HANDOFF_TEMPLATE.md), [CONTEXT_MAP](CONTEXT_MAP.md) — work rules and focused navigation
- [OPENAI_ADMIN_KEY](OPENAI_ADMIN_KEY.md), [JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS](archive/JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md) — usage, controls and safe operation

## 5. טלפון, רובוט וסימולציה / Devices, face and physical design

- [DEVICE_CONTRACT](DEVICE_CONTRACT.md), [FACE_RUNTIME](FACE_RUNTIME.md) — desktop/Pi boundaries and face reuse
- [APPEARANCE](APPEARANCE.md), [Godot viewer](../godot/README.md) — appearance and simulation
- [Assembly guide](assembly/ASSEMBLY_GUIDE.md), [Israeli sourcing evidence](archive/reviews/JARVES_ISRAEL_HARDWARE_SOURCING_2026-10-17.md), [PROJECT_PRINCIPLES](PROJECT_PRINCIPLES.md) — physical design, not verified fabrication

## 6. פיתוח, בדיקות ושחרורים / Engineering and QA

- [GITHUB_FLOW](GITHUB_FLOW.md), [RELEASING](RELEASING.md), [CI_PERFORMANCE](CI_PERFORMANCE.md), [CI_DETERMINISM](CI_DETERMINISM.md) — GitHub, release and CI
- [ENGINEERING_REVIEW_GATES](ENGINEERING_REVIEW_GATES.md), [BUGFIX_LANE](BUGFIX_LANE.md) — quality gates and bug flow
- [V4 release checklist](reviews/SWV4_RELEASE_CHECKLIST.md), [V5 acceptance](reviews/SWV5_ACCEPTANCE.md), [V5 security](reviews/SWV5_SECURITY_REVIEW.md)
- Full test campaigns, manual acceptance and evidence: **[DOC_MAP section 2](DOC_MAP.md#2-testing-and-manual-testing)** and **[section 7](DOC_MAP.md#7-evidence-reports-docsreviews-never-deleted)**

## 7. תוכניות, סקירות וארכיון / Plans, evidence and history

- All plans and work packages: [DOC_MAP section 4](DOC_MAP.md#4-product-plans-work-packages)
- Retained review reports and historical evidence: [DOC_MAP section 7](DOC_MAP.md#7-evidence-reports-docsreviews-never-deleted)
- Migration status: [MIGRATION_STATUS](archive/MIGRATION_STATUS.md), [MIGRATION_HANDOFF](archive/MIGRATION_HANDOFF.md)
- Historical reference: [archive/DECISIONS](archive/DECISIONS.md) and [the archive index](DOC_MAP.md#6-archive-not-read-by-default)

**Repository hygiene:** keep existing stable paths so old links and agents work; add links in DOC_MAP for new documents. Place new proposals in `docs/plans/`, evidence in `docs/reviews/` and genuinely historical docs in `docs/archive/` when approved. Avoid bulk moves or deleting evidence solely to make folders look tidy.
