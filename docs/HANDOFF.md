# Current handoff — 17 October 2026

## Active milestone — DELIVERED, awaiting user visual review

**S1.T03R2 — Pebble A visual redesign, [Issue #4](https://github.com/igalgerman/JARVIS/issues/4).** Review candidate delivered (Claude Opus 5.5); commit `9919e59` plus merges. No purchase, no SKU lock, no manufacturing release, no microphone distance decision; S1.T04 and #2 not started.

**Source of truth:** [NOW.md](NOW.md) controls current scope and approval; [ROADMAP.md](ROADMAP.md) is planning only; [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md) and [AGENTS.md](../AGENTS.md) govern execution.

- **Read first:** `docs/reviews/S1_T03R2_PEBBLE_VISUAL.md`. It covers before/after, multi-angle previews, what is real vs only visual, check evidence, printing, known issues, and where changes go.
- **Run:** `godot\run_viewer.cmd`. Toggles for shell / structure / components / reserved volumes; finish A/B/C; view buttons.
- **Rebuild pipeline:**
  1. `freecadcmd cad\build_pebble.py`
  2. `freecadcmd cad\export_meshes.py`
  3. `blender -b --python blender\build_scene.py` (about 8 min with renders; `-- --no-render` for a fast export)
  4. Godot assets sync automatically.
- **Checks actually run** (all PASS; each exits nonzero on failure):

| Check | What it covers |
|---|---|
| `cad\smoke_test.py` | blockout + Pebble, including the HeadRX regression |
| `cad\check_pebble.py` | containment, collisions, 16 load-path contacts, 220 mm bed |
| `blender\smoke_blender.py` | 39 parts vs CAD manifest, worst 0.28 mm, Godot sync |
| `godot\check_viewer.cmd` | parts/scale, toggles, picking, finishes |
| Negative test | a broken script exits 1 instead of hanging |

- **Problem → owning layer → fix:**
  - Floating head → FreeCAD → continuous head → spine → seat → flange → Al tube → flange → bridge → posts → floor path.
  - Fabric band covering the rear door → FreeCAD → band stops at the door.
  - Smooth-shading gradients → Blender → normals smoothed by angle.
  - Godot check hanging on a script error → Godot → frame limit plus PASS-line check.
- **New files:**
  - `cad/{build_pebble.py,check_pebble.py,JARVIS_pebble.FCStd}`
  - `blender/{finishes.json,JARVIS_manifest.json,renders/pebble_*.png}`
  - `godot/assets/{JARVIS_manifest.json,finishes.json}`, `godot/previews/pebble_*.png`
  - `docs/reviews/S1_T03R2_PEBBLE_VISUAL.md`
- **Changed:**
  - `cad/{export_meshes.py,smoke_test.py}`
  - `blender/{build_scene.py,smoke_blender.py,JARVIS.blend,JARVIS.glb}`
  - `godot/{main.gd,check_viewer.cmd,README.md,assets/JARVIS.glb}`
  - `README.md`, `docs/{HANDOFF,NOW}.md`
  - The old blockout files and S1.T02 renders are kept unchanged as "before".
- **Overall size (PROVISIONAL):** W215 × D218.5 × H291.5 mm.
- **UNVERIFIED:** strength, tip-over safety and tolerances; heat; acoustics; camera behind the tinted cover; cable bends. Mic, Pi, speaker and SSD are placeholder boxes.
- **Cleanup suggestions (not done, need approval):** the S1.T02 renders and `godot/previews/JARVIS_viewer_*.png` are now "before" images. The blockout pipeline (`cad/build_blockout.py`, `check_layout.py`) could be archived later.
- **CI:** still not active (#2).

**Future work parked:** #2 CI; #5 evidence-based library evaluation before software implementation; #6 minimal Core integration; #7 face/voice; #8 hardware prototype preparation. Do not open duplicate speculative issues.

**Agent transition (Claude Code ↔ Codex):** one writer at a time. Finish and commit current work, update this handoff with the actual changed paths, checks and blockers, then have the receiving agent pull the latest. Feedback travels back to the owning source (FreeCAD/Blender/Godot/Core), followed by *only necessary* downstream rebuilds and focused tests.

## Questions for the user
1. **Overall look:** does Pebble A now feel cute, friendly and premium enough? What should change first: head shape, face size/position, base height, or neck?
2. **Side profile:** the head reads as a half-dome behind a flat face. Do you prefer (a) a deeper, rounder back head, (b) a gently curved face rim, or (c) keep it as is?
3. **Finish direction:** A Pearl/graphite/silver, B Warm ivory/oat/brass, C White/sky-blue/navy, or a mix (name which parts)?
4. **Light ring:** keep it under the base, move it up (e.g. to the top of the base), or drop it?
5. **Mic crown:** is a small flat top cap OK? If the 4-mic XVF3800 is chosen later, a raised crown pod of about +15 mm is needed (see the S1.T03R1 review).
6. **Still open from S1.T03R1 (below):** acceptance of an imported mic, local 5″ display vs the compact 4″ variant, bench-test purchase approval, and printer model/bed/material.

---

## Archived S1.T03R1 research handoff (historical reference; superseded as active status)

- **Task:** S1.T03R1, Pebble A component selection & engineering fit review (Issue #3). **Status:** review delivered, awaiting user decisions. Research and design only.
- **Read:** `docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md`. Reproduce with `freecadcmd design\review\pebble_fit_study.py` and `freecadcmd design\review\pebble_layout.py`.
- **Evidence:**
  - Measured from manufacturer STEP files: 5″ display Ø150 × 18.05; 4″ display Ø126 × 18.2; XVF3800 PCB about 100.5 × 101.8, 9.03 thick.
  - Israel stock, checked 2026-10-17: Piitel lists the 5″ round HDMI display at ₪590 incl. VAT. No local far-field mic array or round DSI display was found (import-only).
- **Key findings:**
  - The local 5″ display needs a W215 head.
  - The XVF3800 needs a raised crown pod.
  - Base layout is tight.
  - The Pi 5 USB power budget may be exceeded (measure).
- **Unverified:**
  - Pi 5 USB current limit (recalled, not fetched).
  - CSI cable lengths and HDMI plug size.
  - Import VAT and shipping.
  - All masses, strength, thermal and acoustic figures.
- **Decision questions** from that round (still open):
  1. Imported mic (XVF3800 recommended) vs ReSpeaker Lite vs local-only.
  2. Local 5″ size (about H299 × W215 × D210) vs compact 4″ (about H285 × W205 × D200).
  3. Crown pod look.
  4. Bench-test purchase approval.
  5. Printer.
  6. Room details (voice distance now explicitly undecided).
