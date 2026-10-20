# JARVES — Future Ideas and Deferred Work

**Created:** 19 October 2026  
**Status:** Backlog only. Ideas are **not implementation authorization**. Promote to a scoped GitHub issue when approved.

## F01 — One shared JARVES across the PC and robot

**Explicit owner decision:** Today, Windows JARVES and future Raspberry Pi JARVES may run as **independent installations**. In a later milestone, the owner wants a single, shared assistant spanning both.

Desired capabilities:
- Shared personal memory, preferences, notes, reports, schedules and Skill definitions/grants where appropriate.
- Synced assistant identity and accounts, but **per-device** microphone, renderer, wake and private mute preferences.
- Duplicate-free reminders and scheduled actions (ownership/leader election or an equivalent execution claim).
- Conversation handoff between computer and robot, without two devices answering the same wake phrase at once.
- Offline-first operation and reconnection conflict handling, with explicit user awareness of sync status.
- Secure device pairing, revocation, encryption, and a simple data export/migration path.
- Decide **later** between peer-to-peer LAN, home hub and other sync architectures; no cloud hub is assumed or authorized.

**Prerequisite:** Stable per-device capabilities, state/job IDs, idempotent scheduler, permission policy and tested persistence.

## F02 — Complete Raspberry Pi feature parity

This is a **product requirement**, not optional polish. Individual missing features belong in the live **capability matrix** and tracked GitHub issues, not only in this backlog. Areas likely requiring physical evidence:
- Real microphone-array range, echo cancellation and wake phrase calibration.
- Voice models/provider behavior with ARM64, stable audio drivers and headless mode.
- Full Face/2D/3D render performance, display integration, memory and thermals.
- Reliable Pi deployment/service boot, local recovery, logging and updates.
- Physical privacy mute, safe power control and durable storage.
- Google/third-party OAuth flows without a built-in browser on the robot.

No feature is marked verified on Pi until supported by real physical-device evidence.

## F03 — User-defined activation phrase and assistant name

SW-V2 allows changing display name and spoken name. True custom wake-phrase detection may require a trained wake model and real acoustic tests on each platform. Later: train/select/customize phrase with user-specific calibration and offline fallback, rather than changing visible text while the detector still listens for 'Hey Jarvis'.

## F04 — Secure remote Dashboard outside the home

Later opt-in VPN/relay/access model with pairing, robust auth, revocation and minimal attack surface. SW-V2 remains local/LAN only; do not expose the Dashboard publicly.

## F05 — Shared Skill and avatar library

Portable reviewable Skill/Avatar bundles, provenance, update/rollback, permission prompts, signing where practical, community import only under owner approval. No automatic untrusted plugin marketplace.

## F06 — Additional connected services

Gmail, Google Drive, contacts, smart home, messaging, other calendars and future providers may become individually approved Skills. Each needs real permission boundaries, token security, Pi support plan and human confirmation for external effects.

## F07 — Higher-autonomy proactive agent

In later stages, additional safely bounded user-approved automatic remediation and long-running background research/actions, with rollback, audit, quiet hours and hard cost ceilings. Avoid unrestricted self-modifying code or continuous paid LLM polling.

## F08 — Multi-device presence and routing

With synchronized installations, choose which JARVES should announce a reminder or respond to wake, support handoff between devices and ensure no duplicated payments or actions. Deferred with F01.

## F09 — Optional encrypted off-device backup

After local automatic backups are proven, consider an explicitly authorized encrypted off-device destination. Never silently back up sensitive notes, credentials, calendar information or generated content to an external service.

## F10 — ChatGPT history and personal memory import (DEFERRED by owner, 19 October 2026)

**Status: FUTURE BACKLOG ONLY. NOT PART OF SW-V3 DEVELOPMENT.** The owner requested the official ChatGPT export. ChatGPT confirmed it is preparing the archive and will email a download link; the archive has not been supplied to JARVES. Do not perform architecture research, model analysis, indexing or implementation for this future task now. Do not ask the owner for the ZIP or block active SW-V3 deliverables. Preserve existing notes/memory functionality and tests.

### Preserved owner decisions and recommended future design

- **Data sources:** download one official ChatGPT export ZIP when emailed. Optionally copy the ChatGPT Memory summary separately into memory-summary.md; do not assume the ZIP contains the entire Memory summary.
- **Agreed Windows staging folder:** `%LOCALAPPDATA%\jarves\imports\chatgpt\`. Owner may create it manually later and place the original ZIP there unextracted, plus optional memory-summary.md. The importer has not yet been built.
- **Recommended three layers:** (1) short current, approved personal context always available; (2) searchable structured long-term memory with source, date, confidence, updates and deletion; (3) a complete local searchable archive of ChatGPT conversations, keeping full source/role/thread/date provenance.
- **Architecture and retrieval:** preferably evaluate reusing existing JARVES SQLite conventions and FTS5, with local offline ingestion and no model calls for bulk indexing. JARVES should search past relevant discussions/decisions automatically when user questions warrant it, using a few relevant source-linked excerpts instead of pushing the entire archive into context. Test Hebrew/English recall and stale or contradictory facts. Only add local semantic retrieval if justified by actual results.
- **Review and privacy:** memory summary may seed editable owner-reviewed context; distinguish user-provided facts from assistant guesses, especially sensitive data. Dashboard import progress, search, approved memory proposals, edit/delete and source links. Keep ZIP/database private, outside Git and CI. No unapproved transmission of full archives or costly provider usage.
- **Later technical decision:** once the owner *explicitly reactivates* this item, Opus may refine the recommended design and make technical implementation decisions without asking about ordinary architecture details. Opus must not analyze every historical chat; use economical local indexing and scoped retrieval.
- **Reactivation:** separate owner-approved milestone/issue with Pi roadmap and tests. Not active now.

## F11 — Family Intelligence (DEFERRED; NOT V5)

**Owner decision, 20 October 2026:** retain this idea explicitly in the future backlog. It was suggested for V5 but the owner chose to exclude it from V5, **not to discard it**. It has no implementation authorization, target date or automatic trigger.

**Vision:** evolve the single personal JARVES into a permission-aware household assistant when the mobile/platform foundations and real-world use are mature. Potential features: separate household member and guest profiles, age-appropriate interactions, shared routines/reminders/calendar and planning, household coordination, and responses adapted to the active consenting person.

**Design and safety gates:**
- Consent-based recognition/selection of the active speaker or profile, with an honest fallback when uncertain; do not assume reliable voice identity or enable biometric recognition by default.
- Person-specific access controls and separate private memories, with deliberate explicit sharing of household information only where authorized. Never leak private messages, calendars or preferences between members.
- Age-appropriate interactions, guardian-configurable permissions where relevant, simple manual profile switching, and no passive collection or profiling of children by default.
- Quiet hours, notification routing, duplicate prevention, transparency about microphone/camera/cloud use and human approvals for consequential actions.
- Real family/guest usability and privacy acceptance tests before enabling household mode. Reuse the common Core/identity/memory architecture; do not create separate assistant brains.

**Candidate timing:** reconsider after the stage-2 mobile foundations and more mature day-to-day Android experiences (PRODUCT_VISION stages 2–4), if the owner explicitly chooses a household-focused milestone. It is *not* part of V5 Goes Mobile, V4, Android Auto, or physical hardware readiness.

---
**Backlog rule:** Every idea promoted to active work gets its own agreed scope, GitHub issue, acceptance criteria, and Windows/Pi support plan. Leaving it here does not mean it has been forgotten.