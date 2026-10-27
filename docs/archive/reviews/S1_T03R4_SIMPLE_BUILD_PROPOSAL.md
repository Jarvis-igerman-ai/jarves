# JARVES — simplest viable build: design-direction review (S1.T03R4)

> **Superseded in part (17 Oct 2026):** see [S1_T03R4_FINAL_RECOMMENDATION.md](S1_T03R4_FINAL_RECOMMENDATION.md). An independent review found flaws in this proposal: the mic under the flat crown, the neck seat on the head seam and the thin base top, the closing/service sequence, and the USB camera load. It recommends putting the Pi in the head ("head module + passive base").

**Status:** recommendation for your approval. **No further CAD redesign until you approve.** Nothing purchased, no supplier contacted, nothing printed.
**Models:** Claude Opus 5.5 for the engineering judgement and this review. Delegated research: Haiku for the vendor search and Sonnet for the Godot image tooling, both re-checked by me.
**Appearance preserved:** Pebble A shape, head proportions, side profile, finish C colours, speaker grille look, optional LED ring.

![simple build concept](../../design/review/simple_build_concept.png)

## 0. Honest verdict on the current architecture
**The current design is not beginner-friendly, and a nicer manual would not fix that.**

What it requires today:

| Requirement | What it involves |
|---|---|
| Heat-set inserts | about 15, pushed in with a **soldering iron** |
| Aluminium tube | **cut** to 48 mm and **filed** with an anti-rotation flat |
| Face cover | laser-cut **acrylic**, from a second supplier |
| Fabric band | needs **glue** or tensioning |
| Posts | 4 long metal standoffs |
| Internal structure | a bridge plate, two flanges, a spine, speaker boxes |

**Totals:** 16 printed + 11 bought mechanical parts, about 40 fasteners, 14 assembly steps.

These are the reasons it's hard:
- The neck was a metal tube plus two flanges plus a bridge on posts.
- The head carried an internal spine.
- The face had a separate glass.
- The speakers had printed boxes.

All of that came from a robotics-kit mindset, not a consumer product. **Recommendation: replace it with the architecture below.**

## 1. Recommended simplest design (6 printed parts)
The structure that actually carries load is the **shells themselves**: a thick-walled printed neck and a base shell with a stiff top. Mounts are moulded into the shells instead of using separate brackets.

1. **Head front (bezel):** holds the 5″ round display directly. The display has its own toughened glass, so **no acrylic** is needed. It also has a clip pocket for a small USB camera above the screen.
2. **Head back (dome):** the mic board sits in a moulded pocket under the flat crown, with the sound holes printed in, so there is **no separate mic cap**. Rear vent slots are printed in.
3. **Neck:** **one printed tube** about Ø60 (the same look as today's collar), wall about 6 mm, with chamfered end flanges so it needs no supports. The bore is wide enough to pass plugs. It replaces the aluminium tube, both flanges, the collar, the saddle, the pins, the bridge and the 4 standoffs.
   - A rough calculation puts bending stress around 0.6 MPa if the head is knocked hard, against roughly 20 MPa interlayer strength for PETG. That is a large theoretical margin, but **it must be tested**.
4. **Base shell (sky blue):** includes the printed speaker grille, the rear opening, the neck seat with nut pockets, and the screw bosses. The optional LED groove stays.
5. **Electronics tray (base floor):** everything is **placed openly on this tray**:
   - Pi screw bosses;
   - cradles for two ready-made speakers;
   - cable clips;
   - the power-cable notch;
   - pockets for stick-on feet.
6. **Rear door:** snaps into the opening, with vents. No screws.

**Optional extras:**
- **2 navy band half-rings** that clip around the base, to keep finish C's navy band in a second colour. The alternative is a single-colour base.
- **2 LED diffuser halves**, for a later plug-in LED add-on. The default is a printed blank ring.

**Removed versus today:** head spine and foot, 2 flanges, collar, saddle, bridge plate, 4 long standoffs, 4 Pi standoffs (now moulded bosses), 2 speaker pods, mic cap, acrylic face, fabric band, aluminium tube, all heat-set inserts.

## 2. What the print shop delivers
- **6 PETG parts:** head front and head back in white; neck, base shell and rear door in sky blue; tray in any colour.
- **Optionally** 2 navy band halves and 2 blank/LED ring halves.
- **Supports removed by the shop.** Ask for this in the quote. Only the head back and the base shell need supports, and they use small organic "tree" supports.
- No metal, acrylic, laser or finishing work.

## 3. What you buy (ready-made, plug-in only; no SKU locked yet)

| Item | Why / note |
|---|---|
| Raspberry Pi 5 (4 or 8 GB) + **official Active Cooler** (push-pin, tool-free) + **official 27 W USB-C power supply** + pre-flashed microSD | standard parts, sold in Israel (e.g. Piitel, 4project) |
| 5″ round HDMI touch display (Waveshare, stocked by Piitel ₪590) + **slim micro-HDMI→HDMI cable** (30–50 cm) + short USB cable for touch | plug-in |
| **USB microphone board** (XVF3800 recommended, import) + USB cable | plug-in; replaces the long fragile camera-style ribbon problem |
| **USB camera module** (e.g. locally stocked Arducam USB boards at Piitel) | plug-in USB instead of the Pi CSI ribbon, which needs a long special cable |
| **2 small ready-made speakers with a plug** matching the mic board's speaker socket + a ready-made plug extension lead (head to base) | **connector compatibility must be confirmed before purchase** |
| Small screw kit: **one size of M3 Phillips screws + M3 nuts**, 4 M2.5 screws for the Pi, 4 stick-on rubber feet | hardware store or online |

**Optional later:** a plug-in LED strip/ring kit (needs separate research), and a USB SSD.

## 4. What you do at home (one Phillips screwdriver; about 1–2 hours; difficulty "easy–moderate", like a Pi in a case plus small flat-pack furniture)
1. Press 13 M3 nuts into the hex pockets by hand.
2. **Tray:**
   - Snap the cooler onto the Pi and insert the microSD.
   - Screw the Pi down (4 screws).
   - Drop the 2 speakers into their cradles.
3. **Head:**
   - Screw the display into the bezel (4 screws; depends on the display's real holes).
   - Clip the camera in.
   - Screw the mic board under the crown (2 screws).
4. Pass the head cables down through the neck, then close the head (4 screws). Screw the neck to the head (3 screws).
5. Screw the neck onto the base shell (3 screws).
6. **Plug in:**
   - HDMI and 3 USB plugs into the Pi.
   - Speaker plugs via the extension lead.
   - Clip the cables into the tray clips.
7. Lower the base shell onto the tray (3 screws underneath), snap the rear door in, stick on the feet.
8. Plug in the power supply and switch on.

**Service:** remove 3 screws underneath and lift the top off; everything sits on the open tray.

## 5. Counts, printing constraints, cost

| | Current architecture | **Recommended simple build** |
|---|---|---|
| Printed parts | 16 (+2 optional) | **6** (+2 colour band, +2 LED, optional) |
| Bought mechanical parts | 11 (tube, 8 standoffs, acrylic, fabric) | **0** besides the screw kit and feet |
| Fasteners | about 40 + about 15 heat-set inserts | **about 27 screws + 13 nuts, 0 inserts** |
| Special operations | soldering iron, tube cutting + filing, acrylic order, fabric gluing | **none** |
| Assembly steps | 14 | **8** |
| PETG (slicer, organic supports) | 972 g measured | **≈ 0.85–0.95 kg** (estimate: measured shells + new neck/tray/band estimated) |
| Print time | 83 h at i3 reference speed | **≈ 75–80 h** reference (≈ 35–45 h on a Bambu-class printer) |

- **Printing constraints:**
  - The **Ø210 base shell and tray do not fit** the Haifa shop's stated 250 × 210 mm limit with any margin. They need a **256 mm-class printer** (Bambu P1S/X1C/A1 class), or the fallback of splitting each into front/back halves. The split adds a seam and about 4 screws per part.
  - The 215 × 184 head parts fit 250 × 210 with about 10–15 mm margin per side.
  - No part needs unusual equipment: standard 0.4 mm nozzle, 0.2 mm layers, PETG.
- **Measured slicer data** (PrusaSlicer 2.9.6, current shells, organic supports): `print/QUOTE_PACKAGE.md`, `print/print_report.json`.
  - Head back: 281 g, of which 49 g supports.
  - Base shell: 291 g, of which 42 g supports.
  - Head front: 61 g.
  - Door: 29 g.
  - Floor: 96 g.
- **Cost estimate** (not a quote). Based on 3D Lab Haifa's published weight tiers ("from ₪30" up to 50 g; ₪100–300 for 50–250 g; "from ₪300" larger; checked 2026-10-17; VAT not stated):
  - **about ₪1,000–1,500 for the printed set**, plausible range **₪900–2,000**;
  - **about 60%** of that is the head back and the base shell;
  - today's 16-part set comes to ₪1,232–1,978 by the same method;
  - the simplification mainly saves **assembly effort and extra suppliers**, not much printing money.
- **Savings levers:** a single-colour base (no band halves); 2.4 mm walls; ordering from a shop that prices large parts per gram.

## 6. Still needs professional work or physical validation
- **Professional:** only the printing, including support removal. No metal, laser or electronics work.
- **Must be confirmed or tested before a full print:**
  1. **Display mounting:** the display's real screw positions must be measured; the STEP file is available.
  2. **Mic board and camera** mounting holes or clip fit (depends on the chosen modules).
  3. **Speaker plug compatibility** and lead length.
  4. **USB power budget.** Display, mic amp and camera together may exceed the Pi's USB limit. A bench test is needed; the fallback is one ready-made powered USB hub.
  5. **Printed neck and nut-pocket strength,** plus a tilt test. Recommended: a cheap **test print** of the neck plus one nut-pocket coupon, about ₪50–100.
  6. **Pi heat** inside the closed base, using the vents.
  7. Sound through the printed grille, and mic port alignment.
  8. Confirm the shop's actual bed size and print-in-PETG capability when asking for the quote.

## Decisions requested (in one go)
1. **Approve the simple 6-part architecture** as the direction (recommended), instead of refining the current one.
2. **Navy band:** clip-on navy half-rings (keeps finish C, +2 small parts), or a single-colour base (simplest)?
3. **LED ring:** start with a printed blank ring and add LEDs later as a plug-in option (recommended), or require LEDs in the first build?
4. **Camera:** USB camera module (recommended, plug-in) instead of the Pi ribbon camera?
5. **Base printing:** look for a shop with 256 mm printers (recommended), or accept split base parts?
