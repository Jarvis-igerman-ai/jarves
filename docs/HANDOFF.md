# Current handoff
Task: S1.T03 — Godot interactive 3D review viewer (GitHub Issue #1). Status: DONE as a REVIEW CANDIDATE, awaiting user review. S1.T04 / Issue #2 NOT started.
Run: `godot\run_viewer.cmd` (Godot 4.7.2 via `winget install GodotEngine.GodotEngine`, no editor needed). Left-drag orbit, right-drag pan, wheel zoom, click = part name + bbox mm, toggles: outer shell / internal components, Reset view. See godot/README.md.
Changed files: godot/{project.godot,main.tscn,main.gd,assets/JARVIS.glb (copy of blender/JARVIS.glb),run_viewer.cmd,check_viewer.cmd,README.md,previews/*.png}, docs/{NOW,HANDOFF}.md.
Check: `godot\check_viewer.cmd` -> `CHECK RESULT: PASS (20 parts)` exit 0: all CAD part names + 3 face placeholders found; envelope 200.0 x 280.0 x 160.0 mm (mm->m scale verified); shell/internal toggles; ray-pick Pi_5 (shell off) and Speaker_L (shell on) return correct names. Windowed screenshots in godot/previews/ (viewed). Earlier CAD/Blender smoke tests unchanged and not re-run (no upstream change).
Assumptions: GLB loaded at runtime via GLTFDocument (no import cache); front of robot faces +Z in Godot; dimensions stay PROVISIONAL.
Known review items (not fixed; triage per docs/ROADMAP.md): Body_Shell/box faces show smooth-shading gradients (Blender layer: needs flat/auto-smooth normals); screen face is a static placeholder; no packaged .exe (would need Godot export templates, not installed); no exploded view (out of scope); render quality is blockout level.
CI: still NOT active. Enabling needs (1) user to run `gh auth refresh -h github.com -s workflow`, (2) move ci/cad-smoke.yml to .github/workflows/, (3) validate FreeCAD (apt freecad-python3 0.21 on ubuntu-24.04) actually runs smoke_test.py on a runner. Local smoke tests remain the gate.
Next proposed (not started): review round of user observations (route to FreeCAD/Blender/Godot), then S1.T04 only after approval.
User approval: PENDING
