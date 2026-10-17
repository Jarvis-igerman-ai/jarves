# Current handoff — 17 October 2026

## Active milestone — S1.T03R5 decision made, CAD implementation not started

User chose (17 Oct 2026): improve the simple Pi-in-base build. Engineering decision recorded in [S1_T03R5_DESIGN_DECISION.md](reviews/S1_T03R5_DESIGN_DECISION.md). Next agent: implement it in CAD (see its "Next" section). The R4 notes below are history.

## Previous: S1.T03R4 design-direction gate

The user clarified mid-milestone that the target is a **consumer-style build**: place ready-made modules, plug in ready-made cables, close with a screwdriver. That means no soldering, glue, drilling, cutting, filing, custom cables or specialist tools. They asked for a simplest-design recommendation **before** any further CAD redesign.

- **Read first:** [docs/reviews/S1_T03R4_SIMPLE_BUILD_PROPOSAL.md](reviews/S1_T03R4_SIMPLE_BUILD_PROPOSAL.md), with its concept diagram `design/review/simple_build_concept.png`.
- **Verdict:** the current architecture is **not** beginner-friendly:
  - heat-set inserts (soldering iron);
  - an aluminium tube that must be cut and filed;
  - an acrylic cover from a laser shop;
  - a glued fabric band;
  - 16 printed + 11 bought mechanical parts, about 40 fasteners, 14 steps.
- **Recommendation:** a **6-part printed build** (head front holding the display directly, head back with the mic pocket, one printed neck, base shell, open electronics tray, snap-in rear door), plus optional navy band and LED ring halves. Ready-made plug-in electronics: USB camera and USB mic, speakers with plugs, official PSU. About 27 screws and 13 nuts, 0 inserts, one Phillips screwdriver, 8 home steps.
- **Evidence gathered this milestone:**
  - **Vendors:** [3D Lab Haifa](https://3dlab.co.il/print-service.html), verified by me 2026-10-17: max 250 × 210 × 220 mm per part; "from ₪30" up to 50 g, ₪100–300 for 50–250 g, "from ₪300" larger; PETG; 2–5 business days; WhatsApp files; VAT not stated. Also found by research: Shaked Lab / Ofer Systems (Haifa & Krayot, quote only, 14-day lead), DFUS3D and The 4th Dimension (national, quote only). No vendor was contacted.
  - **Slicer:** PrusaSlicer 2.9.6 CLI, portable ZIP in %LOCALAPPDATA%\Programs, SHA-256 checked.
    - Current 16-part set: 972 g PETG, 83 h at i3 reference speed, with organic supports.
    - Organic supports halved the large parts: Head_Back 525 → 281 g; Base_Shell 381 → 291 g.
    - Tier estimate ₪1,232–1,978. Simple build estimate about ₪1,000–1,500 (range ₪900–2,000), **not a quote**.
  - **Bed fit:** the Ø210 base shell and floor **do not fit 250 × 210 with margin** (they need a 256-class printer or a split); the head parts fit.
- **Work done before the clarification** (committed, checked, now the "current architecture" baseline):
  - CAD R4 refinements: saddle top flange, D-flat tube with keyed flanges, 2 anti-rotation pins, seat screw holes, 4 head-seam boss pairs, 3 base floor bosses, M3 standoffs, part IDs P/B/E/R/A, print orientations.
  - `print/build_print_package.py`, which writes `print/QUOTE_PACKAGE.md` + `print/print_report.json`; the STLs are regenerated and git-ignored.
  - Godot instruction-image tooling: `--step/--part/--overview/--list-parts` and `godot/export_instructions.cmd`.
  - The 42 generated LEGO-style images were **deleted** as premature (regenerable with one command).
- **Checks run:**

| Check | Result |
|---|---|
| `cad\smoke_test.py` | PASS |
| `cad\check_pebble.py` | PASS: 41 parts, load path, bed |
| `godot\check_viewer.cmd` | PASS: 44 parts, incl. step-mode check |
| Slicer runs | all 22 jobs sliced without error |

- **Next (not started):** after approval, a FreeCAD redesign to the simple architecture with real module mounting, then re-slice, quote package, and a beginner guide.

## Previous milestone — S1.T03R3 (delivered; superseded in part by the R4 direction review)

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
| R3-3: Neck seat | Superseded: the R4 proposal replaces the tube/flange neck with one printed neck | **SUPERSEDED** by R4-1 |
| **R4-1: Architecture** | **B "head module + passive base"** (Pi in the head, printed neck column on the floor tray, 2 cables down the neck); fallback A′ (Pi in base) if the head heat test fails. Earlier 6-part proposal superseded. | **USER DECISION REQUIRED** |
| R4-2: Navy band / face ring | Mid-print colour change (0 extra parts); single colour if the shop can't (clip-on halves rejected: rattle risk over speakers) | **USER DECISION REQUIRED** |
| R4-3: LED ring | Printed blank ring now, plug-in LED add-on later (needs a no-solder 5 V / data solution) | **USER DECISION REQUIRED** |
| R4-4: Camera | With B, Camera Module 3 on its short standard ribbon (no USB load); USB camera only for fallback A′ | **ANSWERED by engineering** (follows R4-1) |
| R4-5: Base size / printing | Keep Ø210 + 256 mm shop (preferred) vs Ø194 base (fits 250 × 210, small visual change) vs split | **USER DECISION REQUIRED** (after shop gate G1) |
| R4-7: Mic envelope | XVF3800 + raised crown pod vs ReSpeaker Lite under flat crown | **USER DECISION REQUIRED** (then gate G5) |
| R4-6: Contact vendors | Prepare quote requests only after the design is approved; no contact without separate OK | **DEFERRED**: after R4-1 |
| R3-4: microSD not reachable | Recommend USB/SSD boot or network updates rather than redesigning around the slot | **DEFERRED**: software/hardware prototype stage (#8) |
| Mic crown / XVF3800 pod | Keep flat crown provisionally | **DEFERRED**: mic research gate |
| Imported mic vs local | Not approved | **DEFERRED**: hardware research gate |
| 5″ display envelope | Provisional CAD assumption only | **DEFERRED**: hardware fit/sourcing gate |
| Bench-test purchases | None authorised | **DEFERRED**: purchasing gate |
| Printer / bed / material | Keep 220 mm compatibility + split fallback | **DEFERRED**: print planning gate |
| Room placement / listening range | Undecided; no 3–5 m promise | **DEFERRED**: acoustics test planning |

## Questions for the user (R4 FINAL — answer these first; supersede the earlier R4 list)
Context: [S1_T03R4_FINAL_RECOMMENDATION.md](reviews/S1_T03R4_FINAL_RECOMMENDATION.md). An independent read-only reviewer formed its own alternatives first; I challenged them and made the final call.
1. Approve **architecture B: "head module + passive base"** as the direction for the next CAD round?
   - The Pi, display, camera and mic sit in the head, which can be tested on the table before closing.
   - The base is a speaker stand with a printed neck column.
   - Only 2 cables go down the neck. (recommended)
2. **Base size:** keep Ø210 and find a shop with 256 mm printers (recommended), or accept a slightly smaller Ø194 base that fits 250 × 210 printers?
3. **Mic envelope:** XVF3800 with a small raised crown pod (best pickup), or ReSpeaker Lite under today's flat crown (keeps the silhouette)?
4. **Colours by mid-print colour change** (navy band, dark face ring), with a single colour if the shop can't do it?
5. **LED ring:** a printed blank first, LEDs later as a plug-in add-on? (recommended)

## Questions for the user (R3, still open but lower priority)
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
