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

---

**Backlog rule:** Every idea promoted to active work gets its own agreed scope, GitHub issue, acceptance criteria, and Windows/Pi support plan. Leaving it here does not mean it has been forgotten.