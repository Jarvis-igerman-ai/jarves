# JARVES — SW-V3 Work Package (prioritized)

**Owner approval:** 19 October 2026. **Status:** approved scope for the implementing Claude Code lead to plan, research, implement, test, and integrate.  
**Development base:** latest `swv2-integrate` plus any relevant in-progress worktrees. **Final delivery branch: `main`**, which is this repository's master/default branch.  
**Scope:** upgrade the existing agent, not rewrite it. This document supersedes more prescriptive *V3 draft* coordination instructions; preserve SW-V1/V2 decisions and existing product functionality.

## Working principles

- **Lead manages execution.** The engineering lead decides how many subagents to run, how to partition work, which models to use, and when independent work should run in parallel. Use subagents/worktrees when productive; do not enforce a fixed team size, model on every task, or heavyweight orchestration.
- **Use Claude Opus 5.5 for the Dashboard UX/design review and substantive startup/background performance investigation**, **if actually available**. The lead assigns suitable models for the rest.
- **Research before selecting implementation for EACH feature**: inspect existing code and ongoing branches; compare maintained libraries/SDKs/official APIs vs minimal implementation; check quality, license, security/ToS, maintenance, Windows and Raspberry Pi 5 ARM64 support, memory/CPU, costs and dependencies. Document a **concise recommendation with sources, tradeoffs, and limited PoC where needed**. Reuse proven packages and existing JARVES APIs; avoid speculative refactoring.
- Work **in parallel when independent**. Shared-file changes, risky integrations, CI/publishing, physical/virtual audio devices and external account access need coordination. The lead owns merge order and decides safe concurrency. No concurrent writers in the same shared files.
- **Priority controls the work when Claude Max/session/weekly quotas or local RAM/disk run low.** Finish higher-priority items, integrate them, run their tests and push them before starting or continuing lower-priority expansions. Do not burn scarce quota on redundant agents, broad repeated test loops or optional polishing. Never silently fall back to a billed model API.
- Each implemented feature needs a skill/feature switch if applicable, Dashboard settings/status, coherent CLI/help entry where relevant, permissions, bounded logs, regression evidence, and **Windows + Raspberry Pi compatibility status and a linked roadmap issue for any Pi gap**. `implemented_unverified` is not `verified_on_device`.
- Do not presume any account/subscription, credential, paid telephony call, actual WhatsApp message, software signing, phone pairing, device enrollment or physical-hardware test is authorized merely by this work package. Keep explicit account/financial/security approval gates.

## Priorities and requirements

### P0 — MUST COMPLETE FIRST: real quality, reliability, usability, and verified integration

**P0.1 — Review actual prior conversations and fix bugs (NEW, highest-priority task).**
- Inspect existing user conversations / logs / diagnostic records **on the authorized local JARVES installation**, and the documented [19 Oct conversation review](reviews/CONVERSATION_LOG_REVIEW_19OCT.md); inventory the actual sessions since then. Respect privacy settings and never commit transcripts/secrets to Git.
- Inspect all recorded failures **and recurring bad behavior**, including false or delayed replies, transcript/microphone/audio issues, incorrect tool actions, music playback and volume, stuck web lookups, failed/slow Skills, wasted tokens, incorrect status, Godot disconnects, false diagnostics and forgotten/repeated context. Compare past issue fixes before filing duplicates.
- Build a severity-ranked table: observed session/time, user impact, root cause/evidence, owner, proposed fix, reproducibility, regression test and status. Differentiate **reproduced bug**, **likely bug** and **unverified suspicion**.
- Fix critical/high-impact defects before extending agent autonomy or browser control. Add tests reproducing failures and verify fixes with the actual running Core. Track unreproducible issues honestly. Do not just summarize logs; **fix validated bugs**.
- For sensitive/private local logs, keep analysis local/redacted; publish only safe summaries and test identifiers.

**P0.2 — Dashboard UI/UX major redesign (Opus 5.5 where available).**
- Redesign existing Dashboard into clear, well-organized sections/subsections; **do not lose any existing features, settings, capabilities, reports or admin functions**. The current ungrouped 20+ navigation entries and mixed Hebrew/English labels are unacceptable.
- Hebrew-first, correct RTL and consistent translations; allow **full English** as an alternative locale rather than mixing language labels. Account for mixed-direction code, numbers, URLs and cost units.
- Professional, visually polished and practical UI on desktop, phone and tablet; clear information hierarchy, search, useful empty/loading/error states, readable diagnostics, accessible controls and coherent theme.
- Run real browser screenshot/interaction review **on the actual application**, covering primary workflows, light/dark, wide/narrow displays and representative populated data; iterate based on issues found. Prefer incremental design-system improvements rather than a framework rewrite without a reason.
- Preserve/reorganize Pi compatibility indicators and `verified/planned/blocked` details for **every** feature. Retain persistent compact ChatGPT/API usage indicators with truthful Unknown states.

**P0.3 — Startup, always-on and foreground/background performance (Opus 5.5 where available).**
- Benchmark **cold start, warm start, background startup, idle resource usage, wake-to-ready, wake-to-first-audio, Dashboard load and show/hide the face**. Include CPU, peak/steady RAM, disk work, subprocess count and load alongside Pear/voice where relevant.
- Profile before modifying; show **before/after numbers on the same machine** and identify proven bottlenecks. Avoid optimizing away functionality or harming Pi/Linux portability.
- Detect and fix expensive startup imports, duplicate initialization/processes, improper session reuse, blocking I/O, memory leaks and avoidable Godot render/init overhead **only when evidence supports the change**.
- Maintain reliable headless background operation without paid idle voice sessions. Measure recovery after sleep/resume, device changes and network failures. Provide clear performance regressions/acceptance budgets based on measured baseline, not invented targets.

**P0.4 — Comprehensive installed-system QA with virtual microphone/speaker.**
- Audit coverage for **every existing and new feature**. Do not rely on unit tests or fake skills alone.
- Execute full real Core/agent + scheduler + Dashboard + local IPC + Godot/visuals + tool adapters where available. Include a Windows **virtual mic and virtual speaker** loop; feed recorded/generated Hebrew utterances and capture/inspect actual outbound audio/events **without playing loud sound**. Also test delays, silence, barge-in, long speech, wake false positives, interrupted calls, mute, cancelled approvals and cost caps.
- Reuse existing `core/tools/suite_voice.py`, `suite_music.py`, `suite_system.py` and platform tests, extending them rather than creating an unrelated test framework.
- Test each Skill's happy path, failures/timeouts, unauthorized actions, feature disabled state, persistence, configuration, crash/restart, real service paths **only when account permissions exist**, and Windows/Pi target status.
- Test packaged/installed application and clean setup, not only source checkout. Keep a pass/fail matrix with exact command, commit, measured latency/cost and **mock/virtual/physical/account evidence distinctions**.
- Fix regressions discovered by QA, then rerun focused tests and ordinary CI. Never call a virtual microphone run a test of the physical mic or speaker.

**P0.5 — Finish actual merge and release-quality validation (mandatory closure).**
- Current `main` is behind `swv2-integrate`. **The work is NOT done while only living in subagent worktrees or `swv2-integrate`.**
- First collect and review all completed V2/V3 work from agent branches and worktrees; remove conflicting/duplicate changes safely; run focused integration QA, full CI and Windows installer smoke. Verify security/privacy and no secrets included. Fix failed tests; do not merge known broken builds merely to say 'merged'.
- **Merge the fully reviewed integrated result into `main` (the repo's master/default branch)** via a reviewed PR or equivalent authorized safe integration route. Check that the final `main` commit includes the Dashboard, bug fixes, tests, packaging and docs, and **GitHub Actions is green on the exact resulting main SHA**. Recheck installable artifact built from that SHA.
- Close or update Issues and `docs/HANDOFF.md`/roadmap with exact references, Pi gaps and tests; do not claim merged/passing until verified. Only then report completion.

### P1 — NEXT: practical agent capabilities

**P1.1 — Topic-based `jarvis help` and discoverability.**
- Inspect the existing help/setup/Guides implementation. Group CLI and Dashboard guidance into short discoverable topics: quick start; voice/wake; AI/providers/usage; Skills; schedules/reports; memory/notes/files; Google/WhatsApp; browser/computer; notifications; privacy/permissions; troubleshooting.
- `jarvis help` shows categories, `jarvis help <topic>` shows usable commands and examples (adapt CLI parser if needed). Dashboard help matches actual capabilities and true unavailable/experimental states; no stale duplicated guidance.

**P1.2 — Browser Skill for the agent.**
- Research and compare current browser automation options such as Playwright, browser-use, CDP, and official tool approaches. Confirm Windows and Pi ARM64 browser availability, resource footprint, session isolation, remote websites' restrictions and security.
- Add a bounded browser capability to navigate/read sites, complete forms and download into approved sandbox, with visible activity/status. Restrict profiles/cookies/files/network and approvals. Require explicit user confirmation for sends, purchases, registrations, account changes and any irreversible external side effect.
- No automatic scraping of private ChatGPT/Claude quota pages, no reuse of personal browser sessions or hidden credential extraction. Robust injection-resistant treatment of page content.

**P1.3 — WhatsApp: personal use FIRST, research official Business option.**
- Desired personal-account workflow: draft recipient/message, preview, explicit owner approval, handoff to installed WhatsApp app/web; **the user completes Send until a supported direct-send route is proven**. Do not claim that 'approved' means 'sent'.
- Deeply assess Meta WhatsApp Business Platform Cloud API, a dedicated Business account/number, supported Business App/API coexistence if eligible, fees and terms. Also evaluate unofficial personal-account libraries **as research**, explicitly documenting ToS, account-ban, maintenance and credential exposure risks. Do not connect/migrate the owner's personal account or perform unattended QR login/real sends without separate approval.
- Prototype on fake/demo data; choose and document a practical route, with Pi compatibility story.

**P1.4 — High-value suggestions already proposed by the agent.**
- Read `docs/plans/FEATURE_SUGGESTIONS.md`, deduplicate against V2, rank practical value/effort, and implement only small high-value candidates that do not destabilize P0 work. Suggested first candidates after de-duplication: timers/alarms, shopping/to-do lists, morning briefing, useful conversation recall.
- Important home/safety integrations such as Pikud HaOref alerts require authoritative reliability analysis and must not silently replace official alert channels. Defer broad Home Assistant/IoT or personal-data integrations until individual approval.

### P2 — AFTER P0/P1: higher-risk, higher-cost exploration and phased rollout

**P2.1 — Virtual computer for the agent.**
- Assess when a full computer is truly needed rather than browser automation and sandboxed filesystem tools. Compare Windows Sandbox/VM, isolated container/Linux desktop and managed cloud computer approaches for real Windows/Pi support, CPU/RAM/cost/security.
- Implement only a bounded, isolated prototype if justified; **do not give the agent unrestricted access to the host OS**. Permission review before side effects; track Pi feasibility and measured overhead.

**P2.2 — Calling in TWO stages.**
- Stage A: assisted dialing — prepare contact/number, ask owner to confirm, open telephone dialer; **owner conducts the conversation**.
- Stage B: investigate credible telephony providers and media-streaming SDKs for a JARVES-managed call, with natural Hebrew speech, connection reliability, disclosure of an AI assistant, call duration/price limits, recording opt-in and recipient consent; use simulation first. Real outbound calls require separate explicit recipient/purpose/budget approval.
- Evaluate telephone calls independently from WhatsApp calling; do not imply personal WhatsApp voice-call API support.

## Delivery and priority policy

1. **Begin P0 now** while allowing independent, read-only P1/P2 library research in parallel if it does not delay fixes. No hardcoded agent count or team topology; lead makes these decisions.
2. At any quota/resource pressure: **pause P2, then P1; protect P0 bug fixes, meaningful QA, integration, and final push/merge**. Save short commits/handoffs frequently so completed work survives quota exhaustion.
3. When P0 is solid, complete P1 as capacity permits; P2 is explicitly lower priority. An optional feature being incomplete must not block shipping stable P0/P1, provided it remains clearly tracked, disabled/experimental, and excluded from claims of completion.
4. For every work item: concise research decision -> scoped code/test -> independent review for consequential changes -> integration -> verification. Link real CI and evidence; avoid mass refactors.
5. **Final acceptance:** current stable code and docs on `main`, green Actions on that commit, working installer, tests for existing and V3 features, documented real-conversation bug fixes and performance metrics, no misleading virtual-vs-physical claims, honest unsupported-Pi list, and a short link-based user handoff.

## Owner decisions carried forward

- Local background Windows agent now; later the *same features* on Pi with status and remediation for gaps.
- Three visual choices Face/2D/3D plus hidden mode; editable assistant name and selectable voices.
- Google Calendar create/update/read/reminders **no delete**; proactive actions request approval unless specifically allowed in Dashboard.
- ChatGPT plan/API quota monitor: show 75/90/100 threshold alerts **only when reliable**; otherwise Unknown / transparently labeled local estimates.
- Scheduled jobs, persistent reports, notes, sandbox, backups and skill enable/disable stay functional.
- Final shared Windows+Pi agent with synchronized state remains a **future idea**, not V3 implementation.
- Real account credentials, paid provider calls, third-party sending, hardware purchases and untrusted Skill installs need their own consent gate.
