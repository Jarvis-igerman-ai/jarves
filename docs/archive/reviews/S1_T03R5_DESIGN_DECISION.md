# JARVES S1.T03R5 — engineering decision: Pi in the base, corrected (CAD not yet implemented)

> **Refined by [S1_T03R5_ARCHITECTURE_REVIEW.md](../../reviews/S1_T03R5_ARCHITECTURE_REVIEW.md) (R5b): the chassis table is removed and the base cover top made structural; the fastener and amp plan in that file supersedes this one.**

**Status:** decision made by the engineering agent (Claude Opus 5.5) per the user's 17 Oct 2026 direction, which is to improve the earlier simple build rather than switch architecture. **The CAD round has NOT started yet**: the usage limit was reached. Nothing purchased, printed or contacted.

## Decision
**Keep the Raspberry Pi in the base.** Pi-in-head (option B) is not decisively better.
- Its main gain is fewer neck cables, which is manageable with ready-made cables: the bore is about 44 mm, roughly 2× the largest plug.
- Its main cost is fan noise and heat about 8 cm from the mics, which directly hurts the user's priority #1, voice quality.

## Corrections to the earlier simple build

| Problem | Fix |
|---|---|
| Neck seat across the head seam | Move the head seam back (SplitY about +50). The front **head body** holds the face, crown and neck seat in one piece; the back is a service lid (4 screws, captive nuts). The head floor gets a reinforcing ring around the seat. |
| Neck on a thin base shell | One printed **chassis "table"** (thick integrated legs + a 6 mm top plate over the Pi), screwed to the floor tray from below. It replaces the bridge, 4 posts, both flanges, the metal tube and the collar. A printed **neck column** (Ø60 outer, Ø44 bore, bottom flange) bolts to the plate, prints flange-down without supports, and passes through the base cover. Load path: head → column → chassis → tray. |
| Plugging cables while holding the top | Order: tray (Pi, speakers, amp) → chassis → column → base cover, all while light. Then the head module goes on with its cables fed down the bore. Cables plug into the Pi **through the rear door** with the robot standing. |
| Mic doesn't fit the flat crown | Use a **2-mic XMOS XU316-class board** (ReSpeaker Lite class) under the existing flat crown: short ports, silhouette unchanged. Precedent: Home Assistant Voice Preview Edition uses the XU316 2-mic approach for living rooms. Rejected: the 4-mic XVF3800, which needs a raised cap about Ø150 (a large appearance change). |
| Face cover | The display's own glass is the face. The face opening is about Ø132, and the facet is a dark printed colour zone. The acrylic is removed. The display is held by a printed **retainer ring** (4 screws), which works with any display SKU. |
| Speakers | Mic board 3.5 mm line out → ready-made 3.5 mm lead → ready-made USB-powered amplifier → 2 enclosed speaker modules on the tray. The exact output connector stays a sourcing gate (spec check, no purchase). |
| Camera | Camera Module 3 + a ready-made Pi 5 camera ribbon (about 500 mm) down the neck. No extra USB load. |
| USB power | Display touch/power + mic + amp on the Pi's USB (1.6 A shared limit) is marginal. To be checked from module specs; fallback is one ready-made powered hub. |
| Cooling | Pi Active Cooler, floor intake slots, rear-door exhaust vents. A printed cable grommet blocks warm air and sound from rising up the neck. |
| Navy band | A colour-change zone printed in the base cover (no fabric, no extra part); single colour if the shop can't. |
| LED ring | Groove kept; a printed blank ring by default; LED halves are a later plug-in option. |
| SSD | The official M.2 HAT+ space above the Pi stays reserved; the USB-SSD bay is dropped (no room beside the chassis legs). |

## Planned Pi layout (base)
- **Position:** long axis along x, x −42.5..42.5, y −20..36, on 4 printed bosses (M2.5 self-tap).
- **Ports:** the power and micro-HDMI edge faces the rear door (about 64 mm plug room). The USB/Ethernet edge faces +x.
- **Chassis legs:** about (±56, −32) and (±56, 46).
- **Amplifier:** on the left; speakers in the front ±35° positions.

## Expected parts
- **Printed (about 9 core):** tray, chassis, neck column, base cover, rear door, head body, head lid, display retainer, mic cap, plus 2 small blank LED halves.
- **Fasteners:** about 40 screws and about 23 captive nuts; tools are Phillips #1 and #2.
- **No user operations of these kinds:** soldering, glue, inserts, cutting, filing.

## Next (not started)
Implement the above in `cad/build_pebble.py`, update `check_pebble.py`/`export_meshes.py`/Blender/Godot part references, re-slice with `print/build_print_package.py`, render, run checks, and stop for review.
