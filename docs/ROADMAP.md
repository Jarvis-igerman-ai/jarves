# JARVES — roadmap and milestones

> Planning and future backlog only. **Only [docs/NOW.md](NOW.md) authorizes work.** Future Issues are NOT permission to start them.

## Active software bundle — approved 18 October 2026

The user explicitly authorized one sustained implementation package: **[SW_V1_WORK_PACKAGE.md](SW_V1_WORK_PACKAGE.md)**. It covers **#2 CI → #5 library choice → #6 real Core/Godot → #7 Hebrew voice/face → the first YouTube Music Skill → integration testing and handoff**. The agent proceeds automatically between related phases; no new approval at each checkpoint. R5b remains a software baseline, not an approved physical print. No new subscriptions or account connections without consent.

Latest #7 correction: hands-free wake/conversation with 180 s quiet timeout; persistent truthful connection/activity cues shared with face display; playback-driven mouth; Vesper/Cedar; collapsed Options; no ETA; closer camera. Offline checks and actual renders delivered, live human/physical review still open. See HANDOFF; no subsequent milestone started.

## Current and upcoming

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

## Design feedback workflow
Work forward **FreeCAD → Blender → Godot**, but process feedback back to the **owning source layer** (geometry and mounts in FreeCAD, finishes in Blender, runtime/UI in Godot, behavior in Core/DevicePort). Re-export only affected downstream assets; focused verification at boundaries; user approval after each review round. Never patch physical geometry only in Godot. Manufacturing path is FreeCAD → slicer → 3D print.

## Durable rules
Read [AGENTS.md](../AGENTS.md) for agent execution policy and [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md) for design, buildability, Israel sourcing, budget and repository hygiene. Avoid premature part selection: no final display/mic/SSD choice, no hardware orders or print approval. Exact listening range is undecided. Library research first, then software implementation rather than reinventing systems.

Keep the README concise; record tests, actual commits, blockers and handoff in [HANDOFF.md](HANDOFF.md). Close Issues whose acceptance criteria are completed; don't delete history or create overlapping speculative tasks.
