# S1.T03R5b — CAD implementation review (Issue #4)

_18 Oct 2026. Implements the approved R5b architecture ([architecture review](S1_T03R5_ARCHITECTURE_REVIEW.md)): Pi in the base, structural ribbed base cover, no chassis. No purchase, print or supplier contact was made. **Every "measured" below means measured in CAD or by the slicer, not on hardware.**_

## 1. Summary for the user

- **What you receive:** 9 printed parts (P01–P09). The navy band and the dark face ring are printed in by filament change, so they are not separate parts.
- **What you buy:** Pi 5 + cooler + PSU + microSD, the 5″ round display, Camera Module 3 + 500 mm ribbon, ReSpeaker Lite + its Seeed speaker, 4 ready-made cables, 26 screws + 13 nuts, 4 feet.
- **How you assemble it:** 7 steps, one Phillips screwdriver, about 1–1½ h. See the [assembly guide](../assembly/ASSEMBLY_GUIDE.md).
- **Estimated total ₪3,000–3,800** (not a quote):

  | Share | Estimate | Basis |
  |---|---|---|
  | Enclosure (print service) | ₪1,150–1,650 | slicer weights + 3D Lab Haifa tiers |
  | Electronics | ₪1,800–2,100 | Israeli listings, 2 imports |
  | Screws, nuts, feet | ₪30–60 | estimate |

- **Main open risks:**
  - Speaker lead length and plug type.
  - The Ø210 base needs a 256 mm-class printer, or the split option.
  - Nut-pocket strength and the snap-ring fit need a ₪50–100 test print.
  - Heat in the closed base.

## 2. What was built in CAD (`cad/build_pebble.py`, FreeCAD 1.1.4, all parametric)

| Area | Implemented |
|---|---|
| Base cover P03 | Ribbed 6 mm structural top. 3 neck bosses with side-entry captive-nut slots. 4 floor bosses with nut slots. Front speaker grille (11 × 5 holes Ø3.5). LED groove, backer and wire hole. Door opening. Navy colour-change zone z 18–62. |
| Tray P01 | 4 Pi 5 bosses (M2.5 self-tap). Speaker cradle. Intake slots under the Pi. 4 cover screw holes. |
| Neck P02 | Ø60 / Ø44 bore column. Flange r45 bolted into the cover (3 × M3 at r38). **45° saddle flare** (r30→45, prints without supports) carrying the head on 3 × M3 at r33 with hidden nut pockets. The saddle clearance follows the head parameters. |
| Head body P07 | Shell split at y = 50. Seat C-ring. Face opening r66 and camera window. Dark face-ring zone. 4 retainer bosses. 2 mic bosses under the flat crown with 2 mic ports. 2 seam bosses with nut pockets. 2 hook ledges. Cable hole. |
| Service lid P08 | 2 bottom hooks, 2 counterbored M3×16. Rear vents. |
| Display retainer P06 | r66–74.5 ring, 4 screw tabs. |
| Rear door P09 | Vents, power notch, 1 screw. |
| LED ring | 2 blank halves (default) **or** 2 translucent halves + strip (optional; kept, not cancelled). |
| Components (envelopes) | Display (measured STEP), driver, Pi 5 + cooler, M.2 HAT reserve, camera, ReSpeaker Lite 86 × 35, Seeed speaker 50 × 45 × 22, reserve amp, keep-outs for display plugs and the 5 neck cables. |

## 3. Status of each critical check

| Check | Result | Status |
|---|---|---|
| Solids valid, no collisions, every part inside its shell | PASS (`check_pebble.py`) | **measured (CAD)** |
| Load path: every installed part touches its support | PASS | **measured (CAD)** |
| Parameter regression (head 107.5 → 112 still valid) | PASS (`smoke_test.py`) | **measured (CAD)** |
| microSD reach from the door | 39 mm in from the door plane | **simulated** (geometric) |
| Pi side plug room / USB room | 82 mm / 60 mm | **simulated** |
| 5 cables through the Ø44 neck bore | largest plug diagonal 25 mm, one plug at a time | **simulated**; real plug sizes **unverified** |
| LED connector reachable through the door | PASS | **simulated** |
| Blank ring = exact drop-in for the diffuser | PASS | **measured (CAD)** |
| Mass / centre of mass | 1,436 g (head 795 g); COM 128 mm high | **estimated** (slicer grams + vendor/estimated parts) |
| Tip-over angle | 34° (42° with optional 400 g ballast) | **estimated** |
| Joints: 20 N push at the top of the head | worst M3 bolt 50 N → SF 3.0 (head), 3.3 (neck), 6.8 (tray) | **estimated**: assumes 150 N per captive nut, **unverified** |
| Joints: tipped 45° and caught / 5 N touch / lifted by the head | SF ≥ 10 / ≥ 24 / 36 | **estimated** |
| Printability (slicer) | all 15 jobs sliced; organic supports | **measured (slicer)** |
| Snap-ring retention, nut-pocket fit, self-tap bosses | — | **unverified (needs test print)** |
| Acoustics, thermal, camera image behind the face | — | **unverified (hardware)** |

Joint method (`cad/mech_estimates.py`): a rigid joint tipping about the edge of its contact patch, worst of 360°, with bolt tension T = M·l / Σl². It ignores friction, so it is conservative. An earlier layout with the bolts only behind the display gave SF 1.2. That result is why the 45° saddle flare was added.

## 4. Electronics compatibility (sources)

| Item | Fact used | Source / status |
|---|---|---|
| Pi 5 USB budget | 1.6 A total from USB ports with the official 5 A supply | Raspberry Pi Pi 5 product brief / power docs — **source** |
| Display | ~400 mA typical from USB; 381 g | Waveshare spec / The Pi Hut listing — **source** |
| ReSpeaker Lite | 2 mics, XU316, USB-C, 86 × 35 mm, on-board 5 W amplifier with speaker connector + 3.5 mm jack | Seeed wiki — **source** |
| Speaker | Seeed "Mono Enclosed Speaker for ReSpeaker Lite", 4 Ω 5 W, 50 × 45 × 22 | Seeed listing — **source**. Plug type and lead length **unverified**: it may need an extension. Fallback: the mic board's 3.5 mm jack into the reserved amp volume R01. |
| Camera ribbon | Pi 5 22→15-pin, 500 mm | Piitel listing ₪22 — **source** |
| **Power budget** | display 0.4 A + mic board/amp up to ~1 A at full volume → about 1.4 A of the 1.6 A | **estimated**: cap the software volume and measure later |

**Cables head ↔ base (5 runs, all ready-made, 50 cm):**
1. micro-HDMI → HDMI (display video)
2. CSI ribbon (camera)
3. USB-A → display touch/power
4. USB-A → USB-C (mic board)
5. Speaker lead (mic board in the head → speaker in the base)

## 5. Printing (PrusaSlicer 2.9.6; details in [print/QUOTE_PACKAGE.md](../../print/QUOTE_PACKAGE.md))

- **Totals:** 925 g PETG, 80.8 h at i3-class speed (about half that on CoreXY machines).
- **Settings:** 0.2 mm layers, 3 perimeters, 15 % gyroid infill, organic supports.
- **Bed fit:** the Ø210 base cover P03 (210.0 mm) and tray P01 (207.9 mm) **do not fit a 250 × 210 bed** with brim and margin.
  - Option A: a 256 mm-class printer, e.g. Bambu Lab X1/P1 class (256 × 256).
  - Option B: the split files P03f/P03b and P01f/P01b, each about 210 × 105 mm. These fit 250 × 210 lengthwise, but add a seam and 2 more joints.
  - All head parts fit 250 × 210.
- **Multicolour on a single-extruder printer:** feasible geometrically. Both colour zones are flat Z-bands in the print orientation:
  - The face ring is the first layers of the face-down head print, so one filament change.
  - The navy band is a horizontal band of the upside-down cover, so two changes.
  - Whether the shop will do pauses is **unverified**. If not, print single-colour.
- **Cost:** ₪1,146–1,644 per-part tier estimate. The cross-check by weight gives ₪1,221–2,036. VAT, support removal and delivery are not included. **Not a quote.**

## 6. BOM — purchased vs printed

| Group | Items | Estimate (₪, incl. VAT where listed) | Source |
|---|---|---|---|
| **Printed** | P01–P09 (+ optional A02/A03) | 1,146–1,644 | slicer + 3D Lab tiers |
| Pi 5 (RAM not chosen; 8 GB assumed) | 1 | 685–784 | Israeli listings (snippets, 2026-10-17) |
| Active Cooler | 1 | 30 | Piitel |
| Official 27 W PSU | 1 | 70 | Piitel |
| Waveshare 5″ round display | 1 | 590 | Piitel |
| Camera Module 3 (Wide) | 1 | 145–200 | Piitel |
| 500 mm CSI ribbon | 1 | 22 | Piitel |
| ReSpeaker Lite | 1 | ~95 ($24.90) | Seeed — **import** |
| Seeed mono speaker | 1 | ~15–25 | Seeed / resellers — **import** |
| Import shipping | — | 50–100 | estimate |
| microSD 32–64 GB | 1 | 30–50 | estimate |
| Slim micro-HDMI → HDMI 50 cm, 2 × USB 50 cm | 3 | 60–120 | estimate |
| **Electronics subtotal** | | **≈ 1,790–2,090** | |
| Screws (26), nuts (13), feet (4) | | 30–60 | estimate |
| **Total** | | **≈ 2,970–3,790** | |

The full printed/purchased list with part IDs is in [PEBBLE_PARTS_INVENTORY.md](PEBBLE_PARTS_INVENTORY.md). No SKU is locked or purchased.

## 7. Visuals

| Source | Images |
|---|---|
| Blender (`blender/renders/`) | `pebble_r5_hero_C.png`, `pebble_r5_rear_C.png`, `pebble_r5_exploded_3q.png`, `pebble_r5_exploded_side.png` |
| Godot (`godot/previews/r5/`, 12 views) | front, side, rear, ¾, top, interior, interior side, exploded ¾ / side / front, LED blank close-up, LED ring close-up |
| Assembly (`docs/assembly/img/`) | numbered overview, steps 1–7, part images |

## 8. Remaining risks (ranked)

1. **Speaker lead:** the plug type and length (head → base, about 35 cm) are unverified. It may need a ready-made extension, or the 3.5 mm + reserve-amp fallback.
2. **Ø210 base vs shop printers:** confirm a 256 mm printer at quote time, or use the split.
3. **Captive-nut pockets and self-tap bosses:** strength is assumed (150 N), not tested. A ₪50–100 coupon print is recommended before the full print.
4. **Heat:** the Pi 5 + cooler sits in a closed base with intake slots and door vents. This is not simulated. Plan a stress test with a temperature log.
5. **USB power:** about 1.4 A of the 1.6 A budget. Cap the volume in software and measure.
6. **Acoustics:** the back volume is the Seeed enclosure itself. Sound leaking up the neck bore (no grommet modelled) and grille losses are untested.
7. **Not yet modelled:** camera mounting bosses (vendor hole pattern), rear-door hooks, speaker strap. All are minor CAD items.
8. **LED ring snap retention:** 0.2 mm clearance, retention untested. The LED strip needs a no-solder kit that has not been selected.
9. **Cable bundle in the neck:** 5 cables in a Ø44 bore is fine geometrically, but stiffness of the HDMI cable is untested.

## 9. Checks run (18 Oct 2026)

| Command | Result |
|---|---|
| `freecadcmd cad\smoke_test.py` | PASS (incl. HeadRX regression) |
| `freecadcmd cad\check_pebble.py` | PASS; W215 × D217 × H292 mm |
| `freecadcmd cad\mech_estimates.py` | MECH DONE (numbers above) |
| `print\build_print_package.py` | 15 jobs, 925 g, 80.8 h |
| `blender -b --python blender\smoke_blender.py` | PASS, 28 parts, 0.00 mm |
| `godot\check_viewer.cmd` | PASS, 28 parts, 3 finishes |

**CI:** these checks are now in `ci/cad-smoke.yml` (FreeCAD 1.1.4 AppImage, Blender 5.2.2, Godot 4.7.2 jobs). The workflow is **not active** and has **not run on a GitHub runner**: activating it needs the `workflow` token scope (Issue #2).
