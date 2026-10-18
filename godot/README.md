# JARVES Godot review viewer
Review candidate, not product sign-off. Shows the **Pebble A** design (S1.T03R2). Geometry comes from CAD → Blender → `assets/JARVIS.glb`, plus `assets/JARVIS_manifest.json` (parts, categories, CAD bounding boxes) and `assets/finishes.json` (PROVISIONAL colours). `blender/build_scene.py` syncs these files automatically. All dimensions and finishes are PROVISIONAL.

- **Install:** `winget install GodotEngine.GodotEngine` (Godot 4.7.x, standard build; no editor needed).
- **Run:** double-click `godot\run_viewer.cmd`. It opens in the **room simulation** (below); press **R** for the plain studio/inspection view.
- **Room simulation (default view, `room/room.gd`):** the device at true scale (metres, as exported) stands on a 75 cm oak desk in a furnished 4 × 4 × 2.6 m room. The room has a window with daylight (sun + sky), curtains, a plant, a bookshelf, a chair, a rug, a sofa, a floor lamp and a ceiling pendant. Everything is procedural (primitive meshes, StandardMaterial3D, generated wood/rug/art textures), so there are no downloaded assets. The live face keeps rendering on the display.
  - Keys: **R** room ↔ studio, **N** day ↔ evening (sun off, pendant + floor lamp on), **C** camera preset: desk view (standing eye 1.6 m, 1.75 m away) → sofa view (seated, 3 m) → close-up (0.6 m), **H** hide the UI. Orbit, pan, zoom and part picking work in the room (the camera is clamped inside the walls; clicking furniture shows "Room: …").
  - Flags: `--studio` (start in the inspection view), `--room` (force the room even with inspection flags), `--evening`, `--cam=desk|sofa|closeup`, `--hide-ui`, `--low` (Pi profile: no SSAO/glow, cheaper shadows), `--gi` (desktop only: SDFGI + SSIL bounce light), `--bench` (prints fps, draw calls and VRAM, then quits). `--view=`, `--exploded`, `--hide-shell`, `--show-reserved`, `--step=`, `--part=` and `--overview` imply the studio view, so the review/instruction exports are unchanged.
  - Previews: `godot\room\export_room_previews.cmd` writes `previews\room\room_day.png`, `room_evening.png`, `room_closeup.png` and `room_sofa.png`, and exits 1 if any is missing or empty.
  - Pi 5 notes: only one shadowed light is active at a time (the sun by day, the pendant at night), there is no reflection probe (it measured ~570 MB VRAM), and SDFGI/SSIL are opt-in. The room adds about 150 draw calls. On the Pi, run with `--low` and the GL renderer (`--rendering-method gl_compatibility`), which was checked to render correctly.
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
  - the finish switching;
  - the room: it is the default view, has its main pieces, the device bottom is within ±1 cm of the desk top and inside its footprint, the desk is 75 cm high, every camera preset sees the face unobstructed (a ray to the face hits `Display_Panel`, ≥ 3 % of the screen height), the face SubViewport is wired to the display, the day/evening lights switch, and the studio toggle hides the room and its collision.
- **Screenshots:** `Godot.exe --path godot -- --view=front --finish=B_Warm_Sand --shot=out.png`.
- The GLB is loaded at runtime (GLTFDocument), so no import cache is required. A packaged .exe would need Godot export templates, which are not installed.
- **Assembly instruction images:** `godot\export_instructions.cmd` writes `docs\assembly\img\step_01..14.png`, `overview_labeled.png` and `parts\<pid>_<Name>.png` (every printed/purchased part), then verifies each file exists and is non-empty (exit 1 otherwise). It drives these CLI modes (UI hidden; add `--shot=<png>`):
  - `--step=<1..14>`: parts of earlier steps faded at their final position, the current step's parts pulled out along `explode_mm` with an orange arrow and a pid label (P printed, B bought, E electronics), later/reserved/alternate parts hidden; title and manifest step text overlaid, camera auto-framed (head steps >= 10 frame the head).
  - `--part=<Name>`: solo preview with pid, name, bbox size, kind and note.
  - `--overview`: exploded robot with a pid label on every part.
  - `--list-parts=printed,purchased`: prints `PARTLIST <pid> <Name>` lines and quits.
