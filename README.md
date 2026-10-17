# JARVIS / Peri

Stationary personal companion robot: **simulation first**, physical prototype later.

## Project layers
- **FreeCAD** (`cad/`): parametric geometry, part layout, manufacturing source of truth.
- **Blender** (`blender/`): CAD-derived visual model, materials, lighting and renders.
- **Godot** (next milestone): interactive Windows 3D digital twin and face display.
- **Python Core** (later): state, assistant skills, voice and AI.
- **DevicePort** (later): simulated and physical device adapters.

No motors, wheels or navigation are currently in scope.

## Current status — October 17, 2026
- **S1.T01 CAD**: blockout done; dimensions remain provisional.
- **S1.T02 Blender**: agent reports DONE, awaiting user review. Assets: `blender/JARVIS.blend`, `blender/JARVIS.glb`, `blender/JARVIS_render.png` and `blender/JARVIS_internals_render.png`.
- **S1.T03 Godot**: not started; requires approval.

Read [docs/NOW.md](docs/NOW.md) for the authorized task and [docs/HANDOFF.md](docs/HANDOFF.md) for exact tools, commands, evidence and caveats. Blender output is a visual blockout, not finished industrial design or print geometry.

## How to work
1. Start with [AGENTS.md](AGENTS.md) and [docs/NOW.md](docs/NOW.md), not a recursive repository scan.
2. For architecture and roadmap, read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/ROADMAP.md](docs/ROADMAP.md) only when relevant.
3. Execute a **pre-approved milestone** with related subtasks autonomously; check in at completion or for blockers/material decisions.
4. Use targeted checks, concise handoff, and no unrelated refactoring.
5. Once Godot is viewable, collect user feedback in review rounds; fix the owning layer and re-export/re-import downstream. Do not mask an upstream issue in Godot.

## Key caveats
Physical fit, thermal and audio performance, hardware interfaces, and Godot performance on Raspberry Pi are not validated. Blender is not the manufacturing geometry source.
