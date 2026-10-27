# JARVES S1.T03R2 — Pebble A visual redesign (Issue #4)

**Status:** review candidate for your **visual feedback**. Not a final design, a final part selection or a manufacturing release. Nothing purchased.
**Model:** Claude Opus 5.5. **Date:** 2026-10-17.

## How to look at it
- **Interactive:** double-click `godot\run_viewer.cmd`.
  - Orbit with left-drag, pan with right-drag, zoom with the wheel; click a part to see its name and size.
  - Toggles: **Outer shell / Structure / Components / Reserved volumes**.
  - A **finish** selector (A/B/C) and **Front / Side / Rear / ¾** view buttons.
- **Images:**

| Before (S1.T02/T03 blockout) | After (Pebble A) |
|---|---|
| ![before](../../godot/previews/JARVIS_viewer_shell.png) | ![after](../../godot/previews/pebble_3q_A.png) |
| ![before render](../../blender/JARVIS_render.png) | ![after render](../../blender/renders/pebble_finish_A_Pearl_Graphite.png) |

- **Multi-angle (Godot):** `godot/previews/pebble_{front,side,rear}.png`, `pebble_3q_{A,B,C}.png` (one per finish), and `pebble_interior.png` (shell hidden, structure + components + reserved volumes shown).
- **Renders (Blender, Cycles):** `blender/renders/pebble_{front,side,rear,interior}.png` and `pebble_finish_{A_Pearl_Graphite,B_Warm_Sand,C_Sky_Pastel}.png`.

## What changed, by layer
- **FreeCAD (source of truth)** — `cad/build_pebble.py` → `cad/JARVIS_pebble.FCStd`, a new file. The old blockout is kept unchanged as the "before".
  - **Head:** an ellipsoid shell (W215) with a flat **face facet** about Ø184, a flat **mic crown** Ø80 and a flat **seat** underneath. It splits into `Head_Front` (face bezel) and `Head_Back` (rear dome = service cover), and carries a separate `Face_Cover` (tinted acrylic lens) and `Mic_Cap`.
  - **The head is now connected.** The continuous load path is: `Head_Spine` + foot → head floor → `Neck_Flange_High` → aluminium `Neck_Tube` Ø40×2 → `Neck_Flange_Low` → `Bridge_Plate` → 4 `Bridge_Post`s → `Base_Floor`. The CAD check measures every contact.
  - **Base:** a rounded puck Ø210 × 85 (`Base_Shell`, `Base_Floor`), a removable `Base_Door` at the rear with vent slots and a USB-C opening, and a fabric `Base_Band` that now **stops at the door**. Also a `Base_LightRing` and a cosmetic `Neck_Collar`.
  - **Envelopes** (high-level, not a part lock):
    - display Ø150 × 18 plus its cable keep-out (from the measured 5″ STEP);
    - Camera Module 3 above the display;
    - a 2-mic-class mic board under the crown;
    - Pi 5 + Active Cooler on standoffs, under the bridge;
    - 2 sealed speaker pods with drivers behind the band;
    - **reserved** volumes for the M.2 HAT+ height and a USB-M.2 SSD.
- **Blender:**
  - Materials come from the CAD meshes with crisp normals (fixes the old smooth-shading gradients).
  - **3 provisional finishes** in `blender/finishes.json`:
    - **A** Pearl white / graphite fabric / silver;
    - **B** Warm ivory / oat fabric / brass;
    - **C** White head / sky-blue base / navy fabric.
  - Eyes and smile are a **visual placeholder** on the face cover. The real face comes later, from the Godot face scene.
  - It writes the `.glb`, a manifest and renders, and syncs them to Godot.
- **Godot:** a viewer driven by the manifest, with the 4 layer toggles, live finish switching from the same `finishes.json`, and view buttons. Existing controls are kept.

## Plain language: what is real vs only visual
- **Checked geometrically (in CAD):**
  - parts don't overlap;
  - components sit inside the shells;
  - the head is mechanically connected all the way down to the floor (no gaps);
  - every printed part fits a 220 mm print bed.
- **Measured from real parts:** display outline and depth, and camera size. The mic board, Pi height, speakers and SSD are **placeholders** (boxes) sized from the research, so they are **not** a fit proof.
- **Only visual (Blender/Godot):** colours, materials, glow, face, fabric look. These are for choosing a direction, not product decisions.
- **NOT verified at all (needs physical tests):**
  - strength of the neck, screws and inserts;
  - tip-over safety;
  - print tolerances;
  - heat/airflow;
  - sound through the band and the mic holes;
  - whether a camera can see through the tinted face cover;
  - real cable bend room.

## Checks run (evidence)

| Check | Result |
|---|---|
| `freecadcmd cad\smoke_test.py` | **PASS**: blockout + Pebble generate, all CAD checks, and the parameter regression (HeadRX 107.5→112 widens the head by +9 mm, still passing) |
| `freecadcmd cad\check_pebble.py` | **PASS**: validity, containment, collisions, 16 load-path contacts, print size; overall **W215 × D218.5 × H291.5 mm** |
| `blender -b --python blender\smoke_blender.py` | **PASS**: 39 parts vs manifest, worst bounding-box deviation 0.28 mm, Godot assets in sync, all renders present |
| `godot\check_viewer.cmd` | **PASS**: 39 parts, mm→m bounding boxes within 0.28 mm, toggles, click picking (Pi 5 through the stack, band, face), all 3 finishes apply |
| Negative test | a deliberate script error in a temp copy → exit 1 (previously Godot would hang); a passing run → exit 0 |

Each check exits nonzero on failure.

## Printing (printer not chosen yet)
- **Largest parts:** Head_Back about 215 × 184 × 107, Head_Front about 215 × 184 × 25, Base_Shell Ø210 × 81. They fit a 220 × 220 bed (Ender-3 / Prusa MK4 / Bambu A1 class), with no glue.
- **Fallback for smaller beds** (for example 180 mm): split the head shells and the base shell at x = 0 into left/right halves, with locating pins and 2 screws per joint. Not modelled yet.
- **Orientations:**
  - face bezel: face-down;
  - rear dome: open side down;
  - base shell: top-down;
  - flanges, plates and spine: flat;
  - neck tube: bought aluminium, **not printed**, because a printed tube is weak along its layers.
- Tolerances and insert holes need small test coupons first.

## Known visual/design issues for your review
1. From the side the head reads as a **half-dome behind a flat face**, because the face is large and the head is shallow. A deeper back or a gently curved face rim would soften it.
2. There is a small **ledge** where the flat mic crown meets the face top.
3. The face cover reads glossy dark grey, reflecting the floor. Tint and finish are open.
4. No visible screws, seams, head vents or a status-LED detail yet.
5. The light ring sits under the base and is mostly hidden; it may need to move up or be dropped.
6. The mic-port pattern is generic: it will be aligned once a mic board is chosen.
7. The display sits 14 mm below the head centre (room for the camera and the seat), so the face is slightly low.

## Where future changes go
- **Shape, size, fit, attachment, splits, vents, screws** → FreeCAD (`cad/build_pebble.py`, `Params`). Re-run the export and Blender, then check Godot.
- **Colours, finishes, glow, face placeholder look** → `blender/finishes.json` / `blender/build_scene.py`. Godot follows automatically.
- **Viewer controls, camera, UI** → `godot/main.gd`.
- **Real hardware choices** (display/mic/speaker SKUs) → a research/decision issue first, then the CAD envelopes.
