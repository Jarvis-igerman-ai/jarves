# JARVES — R5 architecture review: one recommended low-risk design (before CAD)

**Status:** engineering recommendation. This is a bounded review: no CAD, purchase, printing or vendor contact. It refines [S1_T03R5_DESIGN_DECISION.md](../archive/reviews/S1_T03R5_DESIGN_DECISION.md) (Pi in the base stays) and **replaces its chassis/fastener plan**.
**Model:** Claude Opus 5.5, single engineer. No subagents were used: this was a reasoning task, so delegating would have cost more than it saved.

## 1. Challenging R5, element by element

| R5 element | Verdict | Reason |
|---|---|---|
| **Chassis "table"** (legs + plate over the Pi) | **Remove** | It existed only because the neck column was taken down to the floor *through* the Pi area. It doesn't need to go there. The **base cover itself is the stiffest structure available**: a Ø210 × 3 mm cylindrical wall is about 8× stiffer in bending than the Ø60 neck. Make the cover **top** structural instead (6 mm thick, radial ribs, printed top-down so the ribs need no supports) and stop the column at the cover top. Load path: head → column → ribbed cover top → cover wall → 3 screws → tray. The Pi then sits freely in the centre with nothing above it. |
| Neck column through the cover to the floor | **Shorten** | The column now spans only the visible neck (cover top to head seat), plus a Ø90 flange under or over the cover top. |
| Head-to-neck joint | Keep, **3 screws** (was 4) | 3 screws form a stable 3-point joint. They are driven from inside the head through the service lid, into captive nuts in the column top. |
| Separate mic cap | **Remove** | The mic board goes in from inside (through the lid) and screws to bosses under the crown. The sound ports are printed into the head body. |
| Head lid, 4 screws | **2 screws + 2 printed hooks** | The hooks engage at the bottom and the screws sit at the top. Hooks are robust lips, not fragile snap-fits. |
| Display retainer, 4 screws | **3 screws** (self-tapping) | Still SKU-agnostic, because it doesn't depend on the display's own holes. |
| Separate USB amplifier + 2 speakers | **Remove the amp** | The 2-mic board has its **own 5 W amplifier + speaker connector** (Seeed wiki, verified 2026-10-17). One ready-made enclosed speaker plugs into it, so there is no extra USB load, and playback through the mic board is the natural echo-cancellation reference path. Cost: **mono sound**, which suits a voice assistant; a stereo upgrade remains possible via the 3.5 mm jack. |
| Speaker cradles, 2 screws each | **1 cradle + 1 strap screw** | |
| Rear door, 2 screws | **2 hooks + 1 screw** | |
| LED blank halves | Keep, **snap-in, 0 screws** | They are the only way to fill the groove, since a ring can't pass over the head. |
| Pi orientation | **Rotate: microSD edge faces the rear door** | The card becomes serviceable through the door. USB/Ethernet face forward, toward the internal cables; power and micro-HDMI face the side, with plenty of room. |

## 2. Recommended design — "R5b: structural base cover"

**Printed (7 + 2 tiny snap pieces):**
1. Head body: face, crown with printed mic ports, neck seat, display and camera bosses.
2. Head lid: vents, 2 hooks.
3. Display retainer ring.
4. Neck column: Ø60, Ø44 bore, Ø90 flange; prints flange-down without supports.
5. Base cover: sky blue with a navy colour-change band, grille, ribbed 6 mm top, rear opening, LED groove.
6. Tray: Pi bosses, speaker cradle, cable clips, feet pockets, intake slots.
7. Rear door.
8–9. LED blank halves (snap-in).

**Bought:**
- Pi 5 + Active Cooler + official 27 W PSU + microSD.
- 5″ round HDMI display + ready-made micro-HDMI→HDMI cable + USB-A→USB-C cable.
- Camera Module 3 + ready-made Pi 5 camera cable (about 500 mm).
- 2-mic XU316 USB board + USB cable.
- 1 enclosed speaker with a plug matching the board.
- One bag of **M3×12 screws + M3 nuts**, plus a few M2.5 self-tapping screws, and stick-on feet.

**Connections, all plug-in:** HDMI, camera ribbon, 2× USB and the speaker plug. 4 of them run down the neck bore (the speaker stays in the base).

**Fasteners, about 25 screws and 12 nuts:**

| Joint | Screws | Nuts |
|---|---|---|
| Pi | 4 (M2.5) | — |
| Speaker strap | 1 | — |
| Cover → tray | 3 | 3 |
| Column → cover | 3 | 3 |
| Head → column | 3 | 3 |
| Display retainer | 3 (self-tapping) | — |
| Mic board | 2 (self-tapping) | — |
| Camera | 2 (self-tapping) | — |
| Head lid | 2 | 2 |
| Rear door | 1 | 1 |

**Two screw types; tools: Phillips #1 + #2.**

**Assembly (about 7 actions, 1.5–2 h):**
1. **Tray:** Pi (4 screws), speaker into its cradle (1 strap screw).
2. **Base top unit:** column onto the cover (3 screws + nuts), while upside-down and light. Snap in the LED blanks.
3. **Close the base:** set the cover onto the tray, flip the light base, 3 screws from underneath.
4. **Head on the table:**
   - Display + retainer (3 screws); camera (2); mic board under the crown (2).
   - Plug all cables into the modules and bundle them.
5. **Mount the head:** feed the cables down the neck, set the head on the column, 3 screws from inside the head.
6. **Close the head:** lid hooks in, 2 screws.
7. **Connect:** through the rear door, plug HDMI, camera ribbon and 2 USB into the Pi and the speaker into the mic board lead; power cable into the notch; door on (1 screw); feet; power on.

**Service:**
- **Door, daily-level:** microSD, Pi ports, power.
- **Lid:** display, camera, mic.
- **Full base:** 3 head screws + 3 underneath.

## 3. Comparison

| | Original 6-part (R4) | R5 (corrected, with chassis) | **R5b (recommended)** |
|---|---|---|---|
| Printed parts | 6 (+band, +LED) | about 9 | **7 + 2 snap blanks** |
| Screws / nuts | about 27 / 13 | about 40 / 23 | **about 25 / 12** |
| Neck load path | thin 3 mm shell top ✗ | table legs ✓ (extra part) | **ribbed 6 mm cover top + cover wall ✓** |
| Neck seat on the head seam | yes ✗ | fixed | **fixed** (seam moved back) |
| Mic | XVF3800 doesn't fit ✗ | 2-mic under crown ✓ | **2-mic under crown ✓** |
| Plugging while holding the top | yes ✗ | fixed (rear door) | **fixed** (rear door) |
| Speaker chain | unverified ✗ | separate USB amp (extra load) | **board's own 5 W amp, 1 plug** (connector type still to confirm) |
| microSD access | blocked | blocked | **through the rear door** |

**Why R5b is the best compromise:** it is essentially the original simple concept with each failed point fixed at its root cause, without adding a structural part. The two ideas that make that possible are a structural cover top and an off-floor neck. It also removes the extra amplifier and the mic cap.

## 4. Unresolved (to settle by specs or in CAD; no purchase needed)
1. **Speaker connector type** on the mic board (not stated on the wiki). Settle from the board's schematic or the drawing before choosing a speaker.
2. **Mono output** is assumed from the single speaker connector; confirm from the schematic.
3. **Mic board mounting holes and mic positions:** taken from Seeed's 2D/3D files when CAD starts.
4. **Display mounting:** the retainer avoids depending on the display's holes; confirm rear connector clearance from the STEP file (already measured envelope).
5. **USB power:** display (0.4 A typ, verified) + mic board + speaker amp, against the Pi's 1.6 A shared limit. Likely fine without a USB camera; confirm the board's current draw at full volume from its spec.
6. **Physical tests, later:** nut-pocket and self-tap coupons, column/cover joint load, tip test, heat in the base, port acoustics.
7. **Printing:** the Ø210 cover and tray still need a 256 mm-class shop or a split (gate G1). Estimated **₪1,000–1,500** for the printed set, **not a quote**.

## 5. Recommendation
Approve **R5b** as the architecture to implement in CAD next. It covers the CAD round, re-slicing, renders, an exploded view, a parts list and the checks, then a stop for review.
