# JARVES — roadmap and milestones

> Planning and future backlog only. **Only [docs/NOW.md](NOW.md) authorizes work.** Future Issues are NOT permission to start them.

## Current and upcoming

| Stage | Goal | Issue | State |
|---|---|---|---|
| 1. CAD / visual baseline | FreeCAD structural and printable geometry, Blender visuals | [#3](https://github.com/igalgerman/JARVIS/issues/3) | Preliminary design review delivered; unresolved engineering choices |
| 2. Interactive Windows viewer | Orbit, part inspection, shell/interior visibility | [#1](https://github.com/igalgerman/JARVIS/issues/1) | Initial viewer implemented; review/follow-up pending |
| **3. Visual design iteration — NOW** | Pebble A mechanical source update → Blender → Godot, interactive review | [#4](https://github.com/igalgerman/JARVIS/issues/4) | **Approved; current milestone** |
| Parallel foundation (separate approval) | Minimal real GitHub Actions CAD smoke | [#2](https://github.com/igalgerman/JARVIS/issues/2) | Open, no active CI |
| 4. Software research | Evidence-based review of maintained agent/voice/Skill tooling, **YouTube Music first using existing projects** (Pear Desktop, Music Assistant, or an appropriate Skill/MCP only after feasibility/terms/security evaluation). User has YouTube Premium and does **not** want Spotify Premium. | [#5](https://github.com/igalgerman/JARVIS/issues/5) | Parked; separate approval required |
| 5. Software skeleton | Godot ↔ Python Core ↔ mocked DevicePort, single working scenario | [#6](https://github.com/igalgerman/JARVIS/issues/6) | Parked; after #5 |
| 6. Face + voice | User-reviewed expressions, speech prototype, measured tradeoffs | [#7](https://github.com/igalgerman/JARVIS/issues/7) | Parked; after #5 and #6 |
| 7. Physical readiness | Locally available components, verified fit, test coupons, assembly plan | [#8](https://github.com/igalgerman/JARVIS/issues/8) | Parked; requires explicit purchase/print gates |
| 8. Physical build | Printed and assembled stationary robot with tested Pi/peripherals | New issue **only when approved** | Not scheduled |

## Future assistant product scope (not yet authorized)
The first software release is a **real, persistent, Hebrew-speaking personal assistant**, with its own memory, tool use, web search when available, a minimal extensible Skills interface, and **real YouTube Music playback as the first external-action Skill**. Investigate reusable well-maintained players/integrations before writing custom code. **Gmail, Calendar and other account connectors are future optional Skills**, not requirements for the first version. Verify the actual music service integration on Linux/Raspberry Pi; do not promise unverified playback, bypass streaming protections or require an additional Spotify subscription. Keep hardware/CAD work (#4) separate. Detailed decisions and acceptance criteria: [assistant & Skills requirements](JARVES_CONTROLS_DIAGNOSTICS_REQUIREMENTS.md).

## Design feedback workflow
Work forward **FreeCAD → Blender → Godot**, but process feedback back to the **owning source layer** (geometry and mounts in FreeCAD, finishes in Blender, runtime/UI in Godot, behavior in Core/DevicePort). Re-export only affected downstream assets; focused verification at boundaries; user approval after each review round. Never patch physical geometry only in Godot. Manufacturing path is FreeCAD → slicer → 3D print.

## Durable rules
Read [AGENTS.md](../AGENTS.md) for agent execution policy and [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md) for design, buildability, Israel sourcing, budget and repository hygiene. Avoid premature part selection: no final display/mic/SSD choice, no hardware orders or print approval. Exact listening range is undecided. Library research first, then software implementation rather than reinventing systems.

Keep the README concise; record tests, actual commits, blockers and handoff in [HANDOFF.md](HANDOFF.md). Close Issues whose acceptance criteria are completed; don't delete history or create overlapping speculative tasks.
