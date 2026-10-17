# Current handoff
Task: S1.T02 — Blender visual scene from CAD (after S1.T01a/b CAD milestone + quality gate).
Status: DONE, awaiting review. Godot (S1.T03) NOT started.
Changed files: cad/export_meshes.py, blender/{build_scene.py,smoke_blender.py,JARVIS.blend,JARVIS.glb,JARVIS_render.png,JARVIS_internals_render.png}, cad/smoke_test.py, cad/check_layout.py (non-zero exit), cad/build_blockout.py (JARVIS_CAD_OUT override), ci/cad-smoke.yml, .gitignore, docs/{NOW,HANDOFF}.md.
Tools (user-scope, no admin): FreeCAD 1.1.4 (winget); Blender 5.2.2 portable ZIP in %LOCALAPPDATA%\Programs\Blender-5.2.2 (official, SHA256 verified; winget MSI needs admin).
Smoke tests (run before DONE):
- CAD: `"C:\Users\97254\AppData\Local\Programs\FreeCAD 1.1\bin\freecadcmd.exe" cad\smoke_test.py` -> PASS (~2 s: generate, validity, containment/collisions, Width 200->220 regression on temp copy). Negative case (Pi moved into wall) -> exit 1.
- Blender: `"...\Blender-5.2.2\blender-5.2.2-windows-x64\blender.exe" -b --python blender\smoke_blender.py` -> PASS (17 CAD parts + face placeholder present with materials; envelope 200x160x280 mm).
Rebuild assets: `freecadcmd cad\export_meshes.py` then `blender -b --python blender\build_scene.py` (~100 s, Cycles CPU, 2 renders).
CI: BLOCKED — gh token lacks `workflow` scope, so push of .github/workflows was rejected. Workflow parked at ci/cad-smoke.yml (untested on a runner; apt freecad-python3 on ubuntu-24.04 assumed). USER ACTION: run `gh auth refresh -h github.com -s workflow`, then move it to .github/workflows/ and check the run.
Limitations: CAD dims PROVISIONAL (see cad/ASSUMPTIONS.md); renders are a visual blockout, not product art; screen face is a visual placeholder; no textures/vents/seams; glTF materials simple.
Next (not started): S1.T03 Godot interactive 3D assembly, after user approval.
User approval: PENDING
