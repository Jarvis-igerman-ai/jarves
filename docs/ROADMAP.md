# JARVIS / Peri Roadmap

> Planning document only. The **current approved scope** lives in `docs/NOW.md`.

| Milestone | Scope | Output | Status |
|---|---|---|---|
| 1. FreeCAD | enclosure, slots, internals, fit assumptions | parametric CAD and part exports | blockout done; geometry provisional |
| 2. Blender | CAD-based materials, lighting and renderable model | blend, glb and renders | S1.T02 agent-reported DONE; review pending |
| 3. Godot | interactive Windows digital twin | orbit/zoom, part inspection, shell/internal views, basic face placeholder | next; not started |
| 4. Simulated behaviors | Python Core, assistant, state, voice and mocked devices | working simulated interactions | future |
| 5. Physical robot | verified dimensions, printed enclosure, Pi/peripheral integration | stationary hardware prototype | future |

## Iterative visual review and rework (important)

The first Godot demo is **not** a final sign-off. Expect multiple user review rounds and corrections to earlier layers. The cycle is:

1. Show a runnable Godot build, screenshots/video and short list of what is implemented vs provisional.
2. Collect the user's observations, preferably annotated with screenshot, part name, expected result and severity.
3. Triage each issue to its **source-of-truth layer**:
   - **FreeCAD**: shape, dimensions, mechanical clearance, mounting positions, fit and manufacturing constraints.
   - **Blender**: material, color, styling, visual finish, lighting and render asset errors.
   - **Godot**: interactions, import/scaling, camera, scene wiring, runtime graphics and UI.
   - **Python Core/DevicePort** (when introduced): behavior, event flow, simulated devices.
4. Fix **upstream first**; if FreeCAD changes, repeat the appropriate CAD → Blender → Godot export/import steps. Never hide a geometry mistake with a visual-only patch.
5. Run focused validation of changed layers plus one downstream integration check. Present a fresh reviewable build, short changelog and open issues.
6. Repeat until the user approves the **review milestone**. Approval applies to the specified milestone, not the entire final product.

Batch related notes into review rounds to minimize context switches and token use. Do not make the agents request permission for each small fix **within an approved review round**. Escalate breaking architecture changes, significant new scope, costs, or blockers.

## Next approval gate
Review `blender/JARVIS_render.png`, `blender/JARVIS_internals_render.png` and the glb. Agent reports CAD and Blender smoke checks passed; ensure failures produce nonzero exit codes. Then explicitly approve S1.T03 if acceptable. S1.T03 should build the smallest useful Godot inspector; do not silently start voice, AI, networking or hardware.

Refer to `docs/ARCHITECTURE.md` and `docs/HANDOFF.md` for technical details and known limitations.
