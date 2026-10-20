# JARVES — roadmap and milestones

> Planning and future backlog only. **Only [docs/NOW.md](NOW.md) authorizes work.** Future Issues are NOT permission to start them.

## Product stages and release direction — owner decision, 20 October 2026

**North star:** [PRODUCT_VISION](PRODUCT_VISION.md) defines six distinct product stages. The roadmap below maps software milestones to that vision; it does not replace existing CAD/physical gates or authorize future implementation. `docs/NOW.md` and the active approved work package remain the execution authority.

| Vision stage | Outcome | Intended release direction | Status |
| --- | --- | --- | --- |
| **1. Desktop assistant** | Reliable Windows Core, voice, face, skills, diagnostics and installer | Existing SW-V1/V2/V3/V4 work | V3 integration reported; V4 in development, real acceptance and release gates must be checked |
| **2. PC-hosted personal server + phone access** | One PC-hosted JARVES, authenticated phone client, shared conversation and abilities | **V5 — JARVES Goes Mobile** | Owner selected for the roadmap, **not implemented or authorized to start coding yet** |
| **3. Android Auto / car** | Safe hands-free car interaction plus cross-device Skills Platform 2.0 | **V6 — JARVES Drive & Act** | Owner-approved roadmap direction, implementation gated after V5; subject to official platform restrictions |
| **4. Everyday Android assistant** | Native daily phone companion with notifications and relevant local abilities | Later milestone (version TBD) | Future, not the V5 phone-access client |
| **5. Phone as robot stand-in** | Docked spare OnePlus 11 used to validate face/mic/speaker/camera/wake behavior | Later milestone (version TBD) | Future; no 1:1 hardware claim |
| **6. Physical robot** | Serviceable 3D-printed JARVES with approved Pi-class hardware | Final physical milestone (version TBD) | Deferred: CAD, sourcing, print, safety, acoustic and owner approval gates |

**Important:** the six *product stages* are not necessarily numbered software releases. V5 targets **stage 2**, and owner-approved **V6** targets **stage 3**; later version mappings, implementation authorizations and dates remain uncommitted. Physical design work continues to be tracked separately under issues #3, #4 and #8.

## V5 — JARVES Goes Mobile + personal intelligence (owner-selected roadmap scope)

**Decision recorded 20 Oct 2026:** preserve all the proposed V5 concepts except Family Intelligence. Family Intelligence is intentionally **deferred**, not rejected; see [F11 in FUTURE_IDEAS](FUTURE_IDEAS.md#f11--family-intelligence-deferred-not-v5). V5 should deliver stage 2 of PRODUCT_VISION while making the already-working desktop assistant more useful. This is a **scope/direction decision**, not permission to start the implementation. Finish and verify V4 first.

### V5 feature pillars

1. **JARVES Everywhere (stage-2 subset: Desktop ↔ Phone).** The Windows PC remains the authoritative Core; a secure, mobile-friendly phone client connects to the same assistant over a deliberately approved transport (LAN first unless the owner separately approves a remote-access architecture). Reuse the existing Core, Skills, identity, memory and DevicePort/API contracts. Support paired-device management, revoke, connection status, live voice, face/status where practical, and seamless follow-up between desktop and phone. Do not create a second brain or silently fork the memory. Always-on phone wake, full native Android integration, Android Auto and dedicated robot hardware remain later stages.
2. **JARVES Executes Goals.** Owner-requested outcomes become bounded, inspectable, resumable multi-step plans using existing Skills and V4 approvals. Persist task status/checkpoints, show what is pending, allow pause/cancel/retry, respect costs/timeouts, and deliver a concise evidence-linked result. Confirm consequential actions; no unrestricted autonomy, opaque external effects or self-modifying code.
3. **Adaptive Memory.** Build on V4 conversation recall: source-linked, correctable user preferences and lessons from explicit corrections, with confidence/staleness/conflict handling. The assistant may suggest a memory update, but personal facts and behavior rules are reviewable, editable and deletable; prevent silent speculative profiling. Changes remain consistent across the paired desktop/phone interface.
4. **Proactive Intelligence.** Opt-in, context-aware suggestions about useful pending work, scheduling conflicts and follow-ups; use event-driven/scheduled signals rather than perpetual paid LLM polling. Respect quiet hours, snooze/stop, frequency limits, permission boundaries and spending caps; deduplicate across desktop and phone. No automatic consequential external side effects.
5. **JARVES Development Manager.** Extend—not rebuild—the V4 Agent Orchestrator (#49). Report what existing Claude Code/Codex agents are doing, correlate tasks with GitHub Issues/commits/PRs/CI, detect blocked or finished work, and recommend review/repair/next action. Use supported session interfaces; distinguish status-only observation from live push/steer. Reviewer independence, repository access, security approvals and user consent remain hard gates; no autonomous merge/deploy by default.

### Required foundations / currently easy to overlook

- **Phone security:** documented threat model, authenticated pairing and revocation, least-privilege per-device grants, protected transport and secrets, explicit mic/camera/cloud indicators. Internet access is separately reviewed; never expose a home PC port merely to satisfy phone access.
- **Continuity and resilience:** one source of truth for conversation/task/memory state, idempotent commands, correct device routing and deduplication, reconnection after sleep/Wi-Fi change, honest offline/PC-unreachable behavior and graceful recovery without duplicate reminders or actions.
- **Voice/product UX:** measure real Android phone mic/speaker latency, echo, push-to-talk vs supported hands-free options, interrupt/barge-in and battery/background behavior; do not confuse a browser demo with a reliable day-to-day mobile client.
- **Control and observability:** truthful feature/status/capability matrix, CLI/dashboard parity where relevant, task audit trails, privacy-preserving diagnostics, budget/usage visibility, rollback, targeted regression coverage and accessibility in Hebrew/English.
- **No stage creep:** no Android Auto controls, everyday standalone Android assistant, docked-phone robot, dedicated Pi hardware or Family Intelligence within V5 merely because shared interfaces could be reused.

### V5 definition of done (proposed acceptance gates for the future work package)

- On an **actual Android phone**, pair/revoke safely, speak with the existing PC Core over the approved transport, resume the same conversation from Windows, read relevant status, and run a permitted Skill with the right approval; no second memory or duplicate actions.
- Verify live voice and reconnection on the physical phone (including host unavailable, interrupted network, app suspend/resume, permission denial and explicit cloud-audio indicators); keep an honest real-vs-fake evidence table.
- Demonstrate an approved multi-step goal with persistent progress, cancellation, bounded budget and a verified final report; test a failure-and-retry path.
- Demonstrate a user-confirmed adaptive memory correction and its source, revision/deletion and effect from both clients.
- Demonstrate useful opt-in proactive suggestions while proving quiet hours, deduplication, stop/snooze and no unapproved action.
- Demonstrate Development Manager status/review reports against real Claude/Codex/GitHub data, stating clearly when a particular live push/attach/control route is unsupported; no implicit PR merge or permission escalation.
- Complete security/privacy review and automated + owner-present Windows/Android end-to-end checks. Record performance, actual costs, unresolved blockers and a V5 release artifact/rollback path.

### Sequencing and authorization

1. Close V4 release and outstanding live acceptance gates, especially the V4 Orchestrator, memory, security and installer; reuse those modules rather than duplicating them.
2. Research current Android/browser/voice/background restrictions and choose the smallest maintainable stage-2 client plus secure transport. Write a scoped V5 package and Issues with dependencies, acceptance criteria and actual device test plan.
3. Implement stage-2 phone access and shared-session/approval reliability first; layer Goals, Adaptive Memory, Proactivity and Development Manager onto that proven backbone, with bounded parallel work only when independent.
4. Perform real-device, real-agent and release acceptance. Do not start stage 3, hardware purchases, new subscriptions, paid tests or network exposure without separate owner approval.

**Docs-only update:** this roadmap entry does not update `NOW.md`, install software, create a V5 implementation branch or start V5 before its separate execution approval. Earlier SW-V1/V2 review notes below are retained as historical evidence, not current completion claims.

## V6 — JARVES Drive & Act (owner-approved future product direction, 20 October 2026)

**Primary product outcome:** complete **stage 3 of [PRODUCT_VISION](PRODUCT_VISION.md)** — safely access the same JARVES while driving through whatever Android Auto/Android/phone-Bluetooth paths are legitimately supported. This is the successor to stage-2 phone access in V5. **Secondary outcome:** introduce new heavyweight assistant capabilities that benefit desktop, phone and later Android, not just a thin in-car UI. The owner approved the following six pillars as the V6 roadmap direction, **not an authorization to start implementation before the prior release gates**.

### Approved six V6 pillars

1. **Driving Companion:** hands-free, conversational assistant tuned for the driver's attention. Natural dialog, concise spoken confirmations, interruption/barge-in, fail-safe cancellation and low-distraction UX. No vehicle motion/control.
2. **Journey Intelligence:** combine user-permitted calendar context, route and traffic information, weather, parking/stop suggestions and timing estimates to help plan a journey. Label freshness, uncertainty and providers accurately; do not invent real-time traffic, parking or routes. Reuse V5 secure phone access and existing connected Skills.
3. **Drive Actions:** approved voice-initiated calls, messages, reminders, music/navigation handoff and calendar/task actions, within the actual OS-app category and permission limits. Sending, booking or consequential changes require appropriate confirmation; when driving, use minimal safe prompts and defer unsafe interactions until stopped.
4. **Voice Engine 2.0:** robust mic/Bluetooth/car-audio routing, noise and echo handling, wake/PTT feasibility, speaker interruption, route and network transitions, headset changes, recovery and measured response latency. Verify with real phones and compatible vehicles rather than simulator-only success.
5. **Contextual Briefings:** opt-in pre-drive and journey-aware concise updates (schedule conflicts, departure/arrival reminders, relevant stops and time-sensitive tasks); avoid notification spam, irrelevant interruptions and spoken sensitive content to passengers by default. Respect quiet hours, privacy, deduplication, budgets and driver attention.
6. **Skills Platform 2.0:** significant cross-device foundation for vetted, permission-scoped, reviewable and maintainable Skills with manifests, dependency/version constraints, capability discovery, setup, diagnostic hooks, updates and rollback. Define a safe import/signing/trust model as appropriate; no arbitrary third-party code execution, auto-install of untrusted Skills or new subscription without approval. Reuse existing Skills contracts instead of a platform rewrite.

### Android Auto platform reality — mandatory research and feasibility gate

- Before design, recheck **current official Android Auto and Android developer policies/APIs** for supported app categories and templates, audio capture/voice use, background/lifecycle, steering-wheel assistant-button behavior, messaging/phone/media/navigation integration and Play/distribution requirements; record documentation versions/date.
- **No promise** that JARVES can replace Gemini/Google Assistant via steering-wheel button, display an unrestricted AI chat UI on the vehicle screen, control native calls/messages/navigation directly, or play every music source from Android Auto. Build only platform-compliant features.
- Maintain an **equivalent practical phone + Bluetooth hands-free fallback** where Android Auto denies required APIs. Each claimed capability must distinguish official in-car UI vs phone-mediated use vs unsupported behavior.
- No interaction with steering, acceleration, braking, or other vehicle-control systems. No unsafe visual interactions, extended text entry or driver distraction.

### Proposed V6 acceptance and dependency gates

- **V5 must first deliver stage 2**: phone pairing, shared Core/memory/permissions, reliable voice, transport and truthful offline state on a real Android device. No separate car 'brain'.
- User can start and end a hands-free conversation through the **validated** Android Auto or Bluetooth phone path in an actual compatible parked vehicle, then continue it on the desktop/phone without losing context or duplicating commands.
- Demonstrate accurate planning of at least one calendar-related trip using real permitted inputs, report estimated vs observed times/freshness and a useful fallback for missing location/traffic/weather provider data.
- Demonstrate authorized driving-mode actions with confirmations, app/OS handoff as needed, and denied-action/unsupported flows that never pretend success.
- Test noise/echo, Bluetooth disconnect/reconnect, phone lock/app background limits, incoming calls, lost data link, session interruption, driver distraction risks and safe failure behavior; real-device evidence is mandatory.
- Demonstrate opt-in briefings with quiet-hours, passenger-privacy and deduplication tests; demonstrate two maintained Skills with permission separation, update and rollback, and no regression to existing V4/V5 Skills.
- Complete security/privacy review, CI/real-device regression matrix and an honest Android Auto compatibility matrix (supported, phone-mediated, blocked/unavailable), with no purchases or paid external service calls without owner approval.

**Out of scope:** turning V6 into the everyday native standalone Android assistant (vision stage 4), docked-phone robot (stage 5), physical robot (stage 6), Family Intelligence (future F11), hidden vehicle controls or broad unapproved automation. Future V7+ version numbers remain tentative until approved.

**Planning authority:** this V6 scope is **approved to be retained in the roadmap**. `docs/NOW.md` and a later bounded V6 implementation package/Issues must explicitly authorize coding. Do not interrupt V4 migration, active delivery or V5 stage-2 sequence.

---
## Active software bundle — approved 18 October 2026

The user explicitly authorized one sustained implementation package: **[SW_V1_WORK_PACKAGE.md](SW_V1_WORK_PACKAGE.md)**. It covers **#2 CI → #5 library choice → #6 real Core/Godot → #7 Hebrew voice/face → the first YouTube Music Skill → integration testing and handoff**. The agent proceeds automatically between related phases; no new approval at each checkpoint. R5b remains a software baseline, not an approved physical print. No new subscriptions or account connections without consent.

Latest #7 correction: hands-free wake/conversation with 180 s quiet timeout; persistent truthful connection/activity cues shared with face display; playback-driven mouth; Vesper/Cedar; collapsed Options; no ETA; closer camera. Offline checks and actual renders delivered, live human/physical review still open. See HANDOFF; no subsequent milestone started.

## Current and upcoming

19 Oct complete retained-log quality review: [report](reviews/ALL_RETAINED_LOGS_19OCT2026.md), all988 records and
100 voice text entries with history/backup reconciliation. Recommended P0: correct full-exit intent and recover
when a completed backend does not produce an answer. Further diagnostics/grounding/startup/voice improvements
are prioritized under existing #7/#17. This checkpoint delivers analysis, not runtime changes or new live tests.

19 Oct text CLI request: standalone `jarvis execute "..."` uses existing text AI, budget, memory/Google tools
and terminal confirmations with no Core/device startup. Optional interactive mode keeps follow-up context.
Camera report correction is limited to grounded status instructions and the module CLI's real switch source;
human image verification remains open. See HANDOFF for focused evidence; no hardware or new model work.

19 Oct user-requested Google extension: explicitly remembered relationships plus optional read-only People API
contact lookup, confirmed event guests/sharing and Gmail event links. Implemented in the Python Core;
live Contacts consent/search and invitation/email delivery remain unverified. No whole-calendar sharing or
automatic directory import. Current evidence and gates are in HANDOFF; broader #17 remains open.

| Stage | Goal | Issue | State |
|---|---|---|---|
| 1. CAD / visual baseline | FreeCAD structural and printable geometry, Blender visuals | [#3](https://github.com/igalgerman/JARVIS/issues/3) | Preliminary design review delivered; unresolved engineering choices |
| 2. Interactive Windows viewer | Orbit, part inspection, shell/interior visibility | [#1](https://github.com/igalgerman/JARVIS/issues/1) | **Done** (closed 18 Oct; viewer check green in CI); now also a furnished room view |
| 3. Visual design iteration — CAD delivered | Pebble A mechanical source update → Blender → Godot, interactive review | [#4](https://github.com/igalgerman/JARVIS/issues/4) | CAD baseline delivered; **open: user visual approval**; physical items in #8 |
| **Active WP0: CI** | Minimal real GitHub Actions CAD smoke | [#2](https://github.com/igalgerman/JARVIS/issues/2) | **Done**: active on every push (Core Ubuntu/Windows, CAD, Blender, Godot, Core-Godot E2E) |
| **Active WP1: Software research** | Evidence-based review of maintained agent/voice/Skill tooling, **YouTube Music first using existing projects** (Pear Desktop, Music Assistant, or an appropriate Skill/MCP only after feasibility/terms/security evaluation). User has YouTube Premium and does **not** want Spotify Premium. | [#5](https://github.com/igalgerman/JARVIS/issues/5) | **Done** ([tech decision](reviews/SW_V1_TECH_DECISION.md)) |
| **Active WP2: Core/DevicePort/Godot** | Godot ↔ Python Core ↔ mocked DevicePort, single working scenario | [#6](https://github.com/igalgerman/JARVIS/issues/6) | **Done**; review hardening in [#9](https://github.com/igalgerman/JARVIS/issues/9) |
| **Active WP3: Face + voice** | User-reviewed expressions, speech prototype, measured tradeoffs | [#7](https://github.com/igalgerman/JARVIS/issues/7) | Room default, close-to-stop, mode 2 and progress/ETA readiness gate implemented; same-session greeting measured at 4.81 s first audio in one silent-input API probe; **open: actual mic/recognition, full desktop startup/user review and remaining integration/appearance gates in HANDOFF** |
| 7. Physical readiness | Locally available components, verified fit, test coupons, assembly plan | [#8](https://github.com/igalgerman/JARVIS/issues/8) | Parked; requires explicit purchase/print gates |
| 8. Physical build | Printed and assembled stationary robot with tested Pi/peripherals | New issue **only when approved** | Not scheduled |

## Future assistant product scope (not yet authorized)
The first software release is a **real, persistent, Hebrew-speaking personal assistant**, with its own memory, tool use, web search when available, a minimal extensible Skills interface, and **real YouTube Music playback as the first external-action Skill**. Investigate reusable well-maintained players/integrations before writing custom code. **Gmail, Calendar and other account connectors are future optional Skills**, not requirements for the first version. Verify the actual music service integration on Linux/Raspberry Pi; do not promise unverified playback, bypass streaming protections or require an additional Spotify subscription. Keep hardware/CAD work (#4) separate. Detailed decisions and acceptance criteria: [assistant & Skills requirements](JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md).

### Planned optional Skills (research done, not authorized) — Issue #17 items 12, 13
- **Google Calendar + Gmail** ([plan](plans/GOOGLE_CALENDAR_GMAIL_SKILLS.md)): direct Google APIs from the Core as normal Skills, read-only scopes first (`calendar.events.readonly`, then `gmail.readonly`), no sending (drafts only, later, always confirmed).
- OAuth installed-app loopback flow; tokens in `<state_dir>/google_token.json` (0600). Testing-mode refresh tokens expire after 7 days, so expect weekly re-connect unless unverified production proves workable. Google's hosted Gmail/Calendar MCP servers are a Developer Preview: upgrade path, not v1.
- Email content is untrusted (prompt injection): sanitize, fence, and refuse action tools in the same turn without spoken confirmation. CI uses fakes only; the user creates the Cloud project and consent screen at a user gate.
- **Camera** ([plan](plans/CAMERA_PLAN.md)): keep Camera Module 3 Wide (CAD E03) + printed privacy shutter; off by default, no recording, local presence/gaze on the Pi CPU, single still to `gpt-6-luna` only on request ("מה זה?"). GPT-Live 1 does not accept images (checked 2026-10-19). Phases C0 (CAD bosses/shutter, #8) to C6; accelerators only if measured CPU contention.

### Stage scope after the 19 Oct review — what is in and what waits
**Active (software, Windows first):** SW-V2 spec milestones M1–M6 ([spec](JARVES_SW_V2_IMPLEMENTATION_SPEC.md)) under Issue #17: always-on runtime + tray, appearances (Face / 2D / 3D / hidden), scheduler / reports / notes / sandbox, Calendar read + create/update (never delete) and Gmail with confirmations, usage meters + backup, Windows Setup.exe built by CI after merge (latest 3 kept), update notify + ask, custom wake phrase (train/load a custom openWakeWord model), dashboard (home LAN with pairing), feature switches with Windows/Pi status.
**Not at this stage (hardware / physical, deferred by the owner on 19 Oct):** Raspberry Pi 5 install and boot, real microphone array and acoustics, camera module purchase and mounting (CAD camera bosses, printed shutter, Issue #8), physical mute, thermals and Pi render performance, spec M7 Pi parity. These stay `planned` / `implemented_unverified` in the capability matrix with real issues; no Pi feature is called verified without physical evidence.
**Later ideas:** shared PC + robot memory (FUTURE_IDEAS F01), remote dashboard outside the home (F04), skill/avatar library (F05).

### After the software stages (SW-V2/V3): phone as a JARVES device — owner idea, 19 Oct 2026
The owner has unused phones, starting with a **OnePlus 11** (Android, ARM64, good mic array, hardware echo cancellation, camera, speaker, screen, battery). Plan, in order, after SW-V3 is stable on `main`: (1) phone as dashboard + face display over the home network (reuse the phone-access pairing and the `demo/android-apk-20261019` Godot Android export); (2) phone as a **satellite device** with the PC as the brain: mic/speaker/camera/face through DevicePort over an authenticated LAN link, on-device wake word, the same privacy strip (mic/camera/cloud-audio) shown on the phone; (3) Core-on-phone (Termux) only as a late experiment. A research agent first checks the OnePlus 11 Android version and background-service/kiosk limits, wake-word options on Android, battery-health risk of an always-charging phone, and the secure link design. Nothing is installed on a phone until the owner approves; the phone is also a stand-in for the future Raspberry Pi device and its results feed the Pi capability matrix.

## Design feedback workflow
Work forward **FreeCAD → Blender → Godot**, but process feedback back to the **owning source layer** (geometry and mounts in FreeCAD, finishes in Blender, runtime/UI in Godot, behavior in Core/DevicePort). Re-export only affected downstream assets; focused verification at boundaries; user approval after each review round. Never patch physical geometry only in Godot. Manufacturing path is FreeCAD → slicer → 3D print.

## Durable rules
Read [AGENTS.md](../AGENTS.md) for agent execution policy and [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md) for design, buildability, Israel sourcing, budget and repository hygiene. Avoid premature part selection: no final display/mic/SSD choice, no hardware orders or print approval. Exact listening range is undecided. Library research first, then software implementation rather than reinventing systems.

Keep the README concise; record tests, actual commits, blockers and handoff in [HANDOFF.md](HANDOFF.md). Close Issues whose acceptance criteria are completed; don't delete history or create overlapping speculative tasks.
