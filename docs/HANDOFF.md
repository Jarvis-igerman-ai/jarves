# Current handoff — 17 October 2026

## Active milestone — S1.T03R3 DELIVERED, awaiting user review

**S1.T03R3 — LED ring, speaker grille, exploded assembly & service review** (round 2 of [Issue #4](https://github.com/igalgerman/JARVIS/issues/4); scope in [NOW.md](NOW.md)).
**Models:** Opus 5.5 for design, CAD, integration and review. A Sonnet subagent handled the Godot exploded view, LED/blank swap and screenshot export, and was verified independently.
**Not covered:** no purchase, no SKU lock, no manufacturing release, no physical validation.

- **Read first:**
  - [docs/reviews/S1_T03R3_ASSEMBLY_REVIEW.md](reviews/S1_T03R3_ASSEMBLY_REVIEW.md): image links, LED ring, speaker fix, assembly, engineering review, checks, known issues.
  - [docs/reviews/PEBBLE_PARTS_INVENTORY.md](reviews/PEBBLE_PARTS_INVENTORY.md): auto-generated printed / purchased / placeholder / reserved / alternate parts, fasteners, 14-step sequence.
- **Images:**
  - `godot/previews/r3/*.png` (13 views: front, side, rear, ¾, top, interior, exploded ¾ / side / front, LED close-up, blank close-up, finish A/B).
  - `blender/renders/pebble_r3_{hero_C,rear_C,exploded_3q,exploded_side}.png`.
- **Run:**
  - `godot\run_viewer.cmd`: new toggles for **Exploded view** (with slider) and **Blank cover instead of LED ring**; views include Top and LED close-up.
  - `godot\export_previews.cmd` re-exports all review images.
- **Rebuild pipeline:**
  1. `freecadcmd cad\build_pebble.py`
  2. `freecadcmd cad\export_meshes.py` (also regenerates the inventory)
  3. `blender -b --python blender\build_scene.py` (`-- --no-render` for a fast export, `-- --only-exploded` for 2 renders)
  4. `godot\export_previews.cmd`
- **Checks actually run** (all PASS, nonzero on failure):

| Check | Result |
|---|---|
| `cad\smoke_test.py` | PASS |
| `cad\check_pebble.py` | PASS: containment, collisions, 16 load-path contacts, LED connector reachable via rear door, blank cover = exact drop-in, 220 mm bed; W215 × D218.5 × H292 mm |
| `blender\smoke_blender.py` | PASS: 44 parts, worst 0.28 mm, Godot sync |
| `godot\check_viewer.cmd` | PASS: explode error 0.000 mm, LED/blank swap, picking |
| `export_previews.cmd` | 13/13 images |

- **Problem → owning layer → fix:**
  - LED ring hidden under the base → FreeCAD → groove on the upper shoulder with snap-in halves, backer ring, wire hole, connector behind the door, blank cover.
  - Wheel-like speakers → FreeCAD → perforated grille windows behind the fabric, cone drivers at band height.
  - Parts sinking below the floor in the exploded view → Blender/Godot → +100 mm lift.
  - Finish C as default → Blender (`finishes.json`).
- **Changed:**
  - `cad/{build_pebble.py,check_pebble.py,export_meshes.py,JARVIS_pebble.FCStd}`
  - `blender/{build_scene.py,smoke_blender.py,finishes.json,JARVIS.blend,JARVIS.glb,JARVIS_manifest.json}`
  - `godot/{main.gd,README.md,export_previews.cmd (new),assets/*}`
  - `docs/reviews/{S1_T03R3_ASSEMBLY_REVIEW.md,PEBBLE_PARTS_INVENTORY.md}` (new)
  - `docs/{NOW,HANDOFF}.md`
- **UNVERIFIED:**
  - strength of the neck seat, set screws and inserts;
  - snap tabs, LED diffusion and power;
  - speaker pod volume (about 0.06 L) and grille acoustics;
  - HDMI plug through the tube, CSI length;
  - heat;
  - microSD access (blocked).
- **Cleanup suggestions** (not done, need approval): the R2 images `godot/previews/pebble_*.png` and `blender/renders/pebble_{front,side,rear,interior,finish_*}.png` are superseded history.
- **CI:** still not active (#2).

**Future work parked:** #2 CI; #5 library evaluation; #6 minimal Core integration; #7 face/voice; #8 hardware prototype preparation.

**Agent transition (Claude Code ↔ Codex):** one writer at a time. Commit, record changed paths, checks and blockers here, then the receiving agent pulls. Feedback goes to the owning source layer, followed by only the necessary downstream rebuilds.

## Decision queue (updated 17 Oct 2026 after S1.T03R3)

| Question | Recommendation / reason | Status |
|---|---|---|
| Overall appearance and proportions | Keep Pebble A | **ANSWERED**: user likes it |
| Side profile | Keep current | **ANSWERED** |
| Finish | C is the default; A/B kept only for comparison | **ANSWERED**: C, visual preference only |
| Light ring location | Upper base shoulder, removable halves + blank cover (implemented R3) | **ANSWERED** (user chose upper rim); look **USER DECISION REQUIRED** in R3 review |
| R3-1: Speaker look | The grille behind the fabric should remove the "wheel" look | **USER DECISION REQUIRED**: confirm in viewer |
| R3-2: Exploded view / inventory clarity | Enough detail for this stage? | **USER DECISION REQUIRED** |
| R3-3: Neck seat | Recommend a larger seat adapter ring plus anti-rotation flat (CAD change, small) | **USER DECISION REQUIRED**: approve for the next round |
| R3-4: microSD not reachable | Recommend USB/SSD boot or network updates rather than redesigning around the slot | **DEFERRED**: software/hardware prototype stage (#8) |
| Mic crown / XVF3800 pod | Keep flat crown provisionally | **DEFERRED**: mic research gate |
| Imported mic vs local | Not approved | **DEFERRED**: hardware research gate |
| 5″ display envelope | Provisional CAD assumption only | **DEFERRED**: hardware fit/sourcing gate |
| Bench-test purchases | None authorised | **DEFERRED**: purchasing gate |
| Printer / bed / material | Keep 220 mm compatibility + split fallback | **DEFERRED**: print planning gate |
| Room placement / listening range | Undecided; no 3–5 m promise | **DEFERRED**: acoustics test planning |

## Questions for the user (R3, grouped)
1. Does the new **LED ring** on the upper base edge look right (see `r3_led_closeup.png` / `r3_3q.png`), and is the **blank cover** look acceptable when the LEDs are not fitted?
2. Do the **speakers** now look clean? The fabric band is unbroken outside; the grille shows only in exploded and shell-hidden views.
3. Is the **exploded view + parts inventory** clear enough, or do you want labels or a step-by-step picture series next?
4. May the next round add the **neck seat adapter ring + anti-rotation flat**, and model the screw bosses and snap tabs (still no physical claims)?

---

## User visual feedback (17 Oct 2026, from S1.T03R2 screenshots — recorded by management)
User likes the appearance very much, prefers **finish C**, and approves the current side head profile; do not propose head reshaping or A/B again without a concrete reason. Observations to investigate: wheel-like speaker openings, rear service and ventilation access, neck cable/strength/fastener path; requested a printable-part inventory and an exploded assembly with sequence (both delivered in R3). Future visual milestones must auto-export real snapshots.

## User decision — optional status-light ring (17 Oct 2026)
Improve the ring's position on the upper rim of the base rather than removing it. Keep LED lighting a **fully removable option**: nonstructural module, accessible detachable wiring and a printable blank cover. No LED board, diameter/power commitment or purchase yet. Sourcing reference only: a locally listed WS2812B ring (16 LEDs) or a 5 V addressable strip; size, Pi GPIO signal level, 5 V current and thermal limits must be verified. **R3 implements the geometry only.**

## Archived S1.T03R1 research handoff (historical reference)
See [docs/reviews/S1_T03R1_ENGINEERING_REVIEW.md](reviews/S1_T03R1_ENGINEERING_REVIEW.md). Summary:
- **Measured from STEP files:** 5″ display Ø150 × 18.05; 4″ display Ø126 × 18.2; XVF3800 PCB about 100.5 × 101.8 × 9.03.
- **Sourcing:** Piitel ₪590 for the 5″ display (2026-10-17). No local far-field mic array found.
- **Findings:**
  - The 5″ display needs a W215 head.
  - The XVF3800 needs a raised crown pod.
  - The Pi 5 USB power budget may be exceeded.
- **Open hardware questions** (all DEFERRED in the queue above): imported mic, display variant, bench purchases, printer, room/listening range.
