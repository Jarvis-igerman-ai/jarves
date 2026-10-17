# JARVES Godot review viewer
Review candidate, not product sign-off. Shows the **Pebble A** design (S1.T03R2). Geometry comes from CAD → Blender → `assets/JARVIS.glb`, plus `assets/JARVIS_manifest.json` (parts, categories, CAD bounding boxes) and `assets/finishes.json` (PROVISIONAL colours). `blender/build_scene.py` syncs these files automatically. All dimensions and finishes are PROVISIONAL.

- **Install:** `winget install GodotEngine.GodotEngine` (Godot 4.7.x, standard build; no editor needed).
- **Run:** double-click `godot\run_viewer.cmd`.
  - Left-drag orbits, right-drag pans, the wheel zooms, and a click identifies a part (name, category, size).
  - Toggles: **Outer shell / Structure / Components / Reserved volumes**.
  - A **finish** selector (3 provisional treatments) and view buttons (front / side / rear / ¾).
  - **Blank cover instead of LED ring** swaps `LED_Blank_*` (hidden by default) for the LED diffusers and strip.
  - **Exploded view** toggle plus a 0..1 slider offsets each part by its manifest `explode_mm` (the whole model is also lifted 0.1 m so nothing sinks below the ground); picking keeps working. Extra view buttons: top, LED closeup.
  - CLI flags: `--exploded[=<f>]`, `--blank-led`, `--view=top|led_closeup|...`; an explicit `--view` wins over the exploded preset.
- **Previews:** `godot\export_previews.cmd` writes the 13 review screenshots to `godot\previews\r3\` and exits 1 if any is missing or empty.
- **Check:** `godot\check_viewer.cmd` → `CHECK RESULT: PASS`, exit 0; any failure exits 1, including a script error, via a frame limit and a PASS-line test. It checks:
  - every CAD part is present, with its bounding box within 1.5 mm of the CAD manifest (mm → m, Z-up → Y-up);
  - the layer toggles;
  - click picking;
  - the finish switching.
- **Screenshots:** `Godot.exe --path godot -- --view=front --finish=B_Warm_Sand --shot=out.png`.
- The GLB is loaded at runtime (GLTFDocument), so no import cache is required. A packaged .exe would need Godot export templates, which are not installed.
