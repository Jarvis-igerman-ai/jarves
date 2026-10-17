# NOW — S1.T02: Blender visual scene derived from CAD

**Status:** DONE, pending user review. **Owner:** one agent. **Do not start S1.T03 (Godot).**

## Goal
Materials, lighting and polished renders derived from the CAD parts (`cad/export_meshes.py` -> `build/parts/*.stl` -> `blender/build_scene.py`). Outputs: `blender/JARVIS.blend`, `blender/JARVIS.glb` (stable CAD part names), two renders.

## Scope limits
Visual only; never a source for printed geometry. Face eyes/mouth are a visual placeholder (not CAD). No Godot, Python Core, animation or manufacturing parts. CAD dimensions stay `PROVISIONAL`.

## Acceptance
`blender -b --python blender/smoke_blender.py` prints PASS; `freecadcmd cad/smoke_test.py` still PASS.
