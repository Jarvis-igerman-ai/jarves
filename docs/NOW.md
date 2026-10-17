# NOW — S1.T01a: parametric CAD silhouette ONLY

**Status:** READY / not started. **Owner:** one agent. **Do not start S1.T01b or other stages.**

## Goal
Create a small FreeCAD parametric blockout of a **stationary JARVIS** that resembles the provided visual reference: round screen/head, compact body, top mic region, camera location, dual speaker locations, rear removable-access region. Treat everything mechanical as *provisional*.

## Read only when needed
- `assets/JARVIS_visual_reference.png` (visual reference; NOT a dimensional drawing).
- `docs/ARCHITECTURE.md` for geometry conventions.
- `docs/archive/MECHANICAL.md` ONLY when a hardware envelope detail is needed.

## Allowed deliverables (no extra systems)
1. `cad/JARVIS_blockout.FCStd` — editable, named distinct solids for head/body/screen/camera/mic/speaker regions, door; controlled by a short parameter list (H/W/D, screen opening).
2. `cad/ASSUMPTIONS.md` — **10 lines max** with dimensions marked `PROVISIONAL` and a list of critical dimensions to request before fit claims.
3. One screenshot or orthographic preview from the FreeCAD model for review.

## Not in this task
No STEP/STL exports yet; no Blender, Godot, animation, Python backend, GPIO, AI, camera feed, audio, movement, full BOM, slicer, render farms, or purchasing. **Do not silently extend scope.**

## Acceptance
- Native FreeCAD file opens and editable parameters change geometry.
- Main pieces are independently selectable; there is a recognizable robot silhouette matching the reference.
- No unverified dimension is called production-ready. Provisional overall envelope may start near H280×W200×D160 mm; confirm before manufacturing.
- Exactly one targeted check (open file, modify one parameter or equivalent supported verification). If FreeCAD unavailable, report blocker, do not substitute a fake `.FCStd`.

**Stop** after a succinct handoff and obtain user approval for the next task.
