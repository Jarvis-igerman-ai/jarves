# JARVIS Godot review viewer (S1.T03)
Review candidate, not product sign-off. Geometry comes from CAD -> Blender -> `assets/JARVIS.glb` (a copy of `blender/JARVIS.glb`; refresh with `copy ..\blender\JARVIS.glb assets\`). CAD dimensions are PROVISIONAL.

- Install: `winget install GodotEngine.GodotEngine` (Godot 4.7.x, standard build; no editor needed to run).
- Run: double-click `godot\run_viewer.cmd` (or `Godot.exe --path godot`). Orbit = left-drag, pan = right-drag, zoom = wheel, click = identify part. Toggles: outer shell / internal components; "Reset view".
- Check: `godot\check_viewer.cmd` -> `CHECK RESULT: PASS` (parts by CAD name, mm->m scale, toggles, click picking).
- The GLB is loaded at runtime (GLTFDocument), so no import cache is required. A packaged .exe would need Godot export templates (not installed).
