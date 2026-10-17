# NOW — S1.T03: Godot interactive 3D digital twin

**Status:** APPROVED by user (17 Oct 2026), awaiting agent execution. **Previous:** S1.T02 Blender reported DONE; review the assets as part of this visual milestone. **Owner:** one implementing agent.

## Objective
Build the smallest useful **runnable Windows Godot 3D viewer** of the stationary Peri/JARVIS robot from the existing CAD-derived `blender/JARVIS.glb`, for hands-on user visual review.

## Approved milestone scope
1. Import existing GLB, preserve stable part identity, verify CAD-mm → Godot-meter scale.
2. Create a well-framed, lit interactive 3D scene with orbit and zoom/pan.
3. Allow shell/exterior vs internals inspection and click/select to identify an individual part.
4. Keep the current eyes/mouth as a simple static placeholder; do not introduce AI, voice, animation, Python Core, new hardware interfaces or unnecessary frameworks.
5. Deliver clear Windows launch instructions, a preview/screenshot if practical, one focused Godot import/runtime check, and a brief handoff with known limitations.

## Visual review loop — required
The first viewer is a **review candidate, not product design sign-off**. Expect multiple rounds of user feedback. Batch notes into a review milestone; fix **source-of-truth first**: FreeCAD for geometry/fit, Blender for appearance/materials, Godot for interaction/rendering, Core/DevicePort later for behavior. Re-export/re-import affected downstream assets and validate the changed boundary. Don't mask CAD issues with Godot-only tricks. Follow `docs/ROADMAP.md`.

## GitHub Actions / CI
There is **no active GitHub Actions workflow** at present. `ci/cad-smoke.yml` is a parked, unvalidated draft, not a working workflow: the earlier agent was blocked from pushing under `.github/workflows/` by missing `workflow` token scope. Do not report CI as active or passing. Local focused checks are enough for S1.T03. Do not spend this milestone on CI; report separately what account/token authorization and runner validation would be required to enable it.

## Execution rules
Read `AGENTS.md`, this file, and only the relevant targeted files. Execute this approved milestone autonomously without asking on each small step. Keep token usage, installs and tests bounded. Stop for a real blocker/material new decision or at milestone review; update `docs/HANDOFF.md` with evidence and commit the work. **Do not start S1.T04.**
