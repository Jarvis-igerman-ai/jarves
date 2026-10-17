# Current handoff
Task: S1.T03R2 — Pebble A visual redesign (Issue #4). Status: REVIEW CANDIDATE DELIVERED, awaiting your visual feedback. No purchase, no SKU lock, no manufacturing release; S1.T04 and Issue #2 not started.
Model: Claude Opus 5.5.
Read first: `docs/reviews/S1_T03R2_PEBBLE_VISUAL.md` (before/after, multi-angle previews, what is real vs visual, check evidence, printing, known issues, where changes go).
Run: `godot\run_viewer.cmd` (toggles shell / structure / components / reserved; finish A/B/C; view buttons).
Rebuild pipeline: `freecadcmd cad\build_pebble.py` -> `freecadcmd cad\export_meshes.py` -> `blender -b --python blender\build_scene.py` (≈8 min with renders; `-- --no-render` for fast) -> Godot assets synced automatically.
Checks (all PASS, nonzero on failure): `cad\smoke_test.py` (blockout + Pebble, incl. HeadRX regression); `cad\check_pebble.py` (containment, collisions, 16 load-path contacts, 220 mm bed); `blender\smoke_blender.py` (39 parts vs CAD manifest, worst 0.28 mm, Godot sync); `godot\check_viewer.cmd` (parts/scale, toggles, picking, finishes). Negative test: broken script -> exit 1.
New files: cad/{build_pebble.py,check_pebble.py,JARVIS_pebble.FCStd}, blender/{finishes.json,JARVIS_manifest.json,renders/pebble_*.png}, godot/assets/{JARVIS_manifest.json,finishes.json}, godot/previews/pebble_*.png, docs/reviews/S1_T03R2_PEBBLE_VISUAL.md.
Changed: cad/{export_meshes.py (Pebble + manifest),smoke_test.py}, blender/{build_scene.py,smoke_blender.py,JARVIS.blend,JARVIS.glb}, godot/{main.gd,check_viewer.cmd,README.md,assets/JARVIS.glb}, docs/{HANDOFF,NOW}.md. Old blockout files and S1.T02 renders kept unchanged as "before".
Overall size (PROVISIONAL): W215 x D218.5 x H291.5 mm.
UNVERIFIED: strength/tip/tolerances, heat, acoustics, camera behind tinted cover, cable bends; mic/Pi/speaker/SSD are placeholder boxes.
Cleanup suggestions (not done): `blender/JARVIS_render.png`/`JARVIS_internals_render.png` and `godot/previews/JARVIS_viewer_*.png` are now "before" images; the blockout pipeline (`cad/build_blockout.py`, `check_layout.py`) could be archived later — only with your approval.
CI: still not active (Issue #2).

## Questions for the user
1. **Overall look:** does Pebble A now feel cute/friendly/premium enough? What should change first (head shape, face size/position, base height, neck)?
2. **Side profile:** the head reads as a half-dome behind a flat face. Prefer (a) deeper, rounder back head, (b) gently curved face rim, or (c) keep as is?
3. **Finish direction:** A Pearl/graphite/silver, B Warm ivory/oat/brass, or C White/sky-blue/navy — or a mix (name which parts)?
4. **Light ring:** keep under the base, move it up (e.g. top of base), or drop it?
5. **Mic crown:** OK as a small flat top cap? (If the 4-mic XVF3800 is chosen later, a raised crown pod ~+15 mm is needed — see S1_T03R1 review.)
6. Still open from S1.T03R1: imported mic acceptance, local 5″ display vs compact 4″ variant, bench-test purchase approval, printer model/bed/material.
