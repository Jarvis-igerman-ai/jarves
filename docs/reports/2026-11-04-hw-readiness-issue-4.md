# V7 hardware readiness: where issue #4 stands (consolidated)

V7 future hardware ledger, issue [#4](https://github.com/Jarvis-igerman-ai/jarves/issues/4). Status: current, docs only, written 2026-11-04 from files already on `main`. Nothing was bought, printed, measured, contacted or re-priced. Index: [DOC_MAP](../DOC_MAP.md).

Labels used here (from [PROJECT_PRINCIPLES](../PROJECT_PRINCIPLES.md)): **SOURCE** = a cited page or file says so; **CAD** = measured in CAD or the slicer, not on hardware; **PROVISIONAL** = design assumption; **UNVERIFIED** = nobody has checked; **PHYSICAL TEST** = needs real parts.

## 1. Bottom line

- The design is a complete software baseline: Pebble A, revision R5b (9 printed parts, 7 assembly steps, 26 screws and 13 nuts, CAD to Blender to Godot checks passed on 18 Oct 2026).
- Not one physical gate is met. Nothing has been held, printed, powered or heard.
- The next step needs the owner, not an agent: the Pebble A visual sign-off and the decision to start the hardware stage. The issue itself says "deferred, not now", so this report does not ask for them.
- All prices below are from 17 to 18 Oct 2026 and have not been checked again. Stock and prices must be checked again before any order.

## 2. Gate by gate

| Gate in #4 | State | Evidence on `main` | What is still missing |
|---|---|---|---|
| FreeCAD is the source; changes flow FreeCAD to Blender to Godot | Met for the software baseline (CAD) | `cad/build_pebble.py`, `cad/JARVIS_pebble.FCStd`, `cad/export_meshes.py`, `blender/renders/pebble_r5_*.png`, `godot/previews/`; checks listed in [R5b review section 9](../archive/reviews/S1_T03R5B_IMPLEMENTATION_REVIEW.md) | `ci/cad-smoke.yml` is a draft. It has never run on a GitHub runner, so the chain is checked by hand only |
| Owner visual approval on real screenshots and exploded views | **Open (owner)** | Renders and 12 Godot views exist; [S1_T03R2 visual review](../archive/reviews/S1_T03R2_PEBBLE_VISUAL.md) | Owner sign-off of Pebble A. Colours, materials and finish are not approved |
| Engineering checks: head/neck/base load path, size, connectors, fit, heat, sound, tip-over, mute, cable strain | Partly estimated (CAD), nothing physical | Load path and joint safety factors are estimates (worst M3 bolt SF 3.0, assumes 150 N per nut, UNVERIFIED); 5 cables in the 44 mm neck bore simulated | Heat, sound, camera image behind the face and tip-over are PHYSICAL TEST. A **physical mute** is not in the R5b design or the parts list: UNVERIFIED gap |
| Israel-first stock, alternatives, VAT-inclusive ILS BOM, local print shop, test coupons | Researched once (SOURCE, 17 Oct 2026); not re-checked | [Israel sourcing review](../archive/reviews/JARVES_ISRAEL_HARDWARE_SOURCING_2026-10-17.md), [R5b BOM](../archive/reviews/S1_T03R5B_IMPLEMENTATION_REVIEW.md), [print/QUOTE_PACKAGE.md](../../print/QUOTE_PACKAGE.md) | No real shop quote; no stock confirmed (add-to-cart is not stock); 2 parts are imports; coupon print not designed as its own file set |
| Beginner screw-and-plug assembly, no solder, glue or drilling; print direction and material | Met on paper (CAD) | [ASSEMBLY_GUIDE](../assembly/ASSEMBLY_GUIDE.md), [PEBBLE_PARTS_INVENTORY](../reviews/PEBBLE_PARTS_INVENTORY.md): print direction per part, PETG suggested | Not tried by a person. Optional LED ring still needs a no-solder kit (not chosen) |
| No final SKU, full print, purchase or safety claim without the owner and real tests | Holding | No SKU chosen; no order; no safety claim | Stays closed until the owner opens the hardware stage |

## 3. Open owner decisions (carried from legacy #3, #4, #8)

These are listed, not asked. Each needs the owner; none has a default answer.

1. Pebble A visual sign-off (the only reason legacy #4 stayed open).
2. Start the hardware stage at all (legacy #8 is parked until then).
3. Microphone: R5b uses a 2-mic ReSpeaker Lite under the flat crown; the 4-mic XVF3800 needs a raised cap of about 150 mm. Accept the 2-mic board, or the look change.
4. Size: R5b is about W215 x D217 x H292 mm (CAD), larger than the first 280 x 200 x 160 mm envelope.
5. Voice pickup distance: not decided (room 35 to 50 m²; 3 to 5 m was only exploratory).
6. Printer: a 256 mm-class bed for the 210 mm base, or the split base parts.
7. Approval for a small bench test purchase and a coupon print (no budget approved).

## 4. Purchased parts: last known Israeli prices (not re-checked)

All rows: date checked 2026-10-17 (R5b BOM 2026-10-18), prices as listed on the page, VAT included where the page says so. Stock not confirmed unless the page said "in stock". Current price and stock: UNVERIFIED.

| Part | Seller (source) | ILS listed | Stock on 17 Oct | Notes |
|---|---|---|---|---|
| Raspberry Pi 5 (RAM not chosen) | [Piitel](https://piitel.co.il/shop/raspberry-pi-5/) | 395 to 1,390 by model; 8 GB 685 to 784 (snippets) | not confirmed | RAM to follow software needs |
| Pi 5 Active Cooler | [Piitel](https://piitel.co.il/shop/active-cooler/) | 30 incl. VAT | not confirmed | Heat in closed base: PHYSICAL TEST |
| Official 27 W USB-C PSU (EU) | [Piitel](https://piitel.co.il/shop/27w-usb-c-psu/) | 70 incl. VAT | not confirmed | USB budget about 1.4 of 1.6 A (estimate) |
| Waveshare 5" round HDMI display | [Piitel](https://piitel.co.il/shop/5inch-hdmi-round-touch-display-1080-x-1080-ips-10-point-touch-optical-bonding-toughened-glass-panel/) | 590 incl. VAT | not confirmed | HDMI plug clearance UNVERIFIED |
| Camera Module 3 | [Piitel](https://piitel.co.il/shop/raspberry-pi-camera-module-3/) | 145 to 200 | not confirmed | Wide or standard not chosen |
| 500 mm Pi 5 CSI ribbon | [Piitel](https://piitel.co.il/shop/csi-fpc-flexible-cable-for-raspberry-pi-5-22pin-to-15pin-500mm-suitable-for-csi-camera-modules/) | 22 incl. VAT | not confirmed | Signal over 500 mm: PHYSICAL TEST |
| Slim 0.5 m micro-HDMI to HDMI | [Dagan Multimedia](https://www.daganm.co.il/%D7%9B%D7%91%D7%9C%D7%99%D7%9D-%D7%9E%D7%99%D7%A7%D7%A8%D7%95-HDMI) | about 62 | not confirmed | Alternative: [Delock](https://delock.co.il/products/delock-cable-high-speed-hdmi-with-ethernet-hdmi-a-male-hdmi-micro-d-male-3d-4k-slim-high-quality) 130 |
| 40 mm 4 ohm 3 W speaker (local alternative) | [Hackstore](https://hackstore.co.il/product/%D7%A8%D7%9E%D7%A7%D7%95%D7%9C-1-5-4%CF%89-3w/) | 35 each | page said in stock | Page gives two different sizes; not in R5b |
| WS2812B LED strip 60/m, 1 m (optional ring) | [Hackstore](https://hackstore.co.il/product/%D7%9E%D7%98%D7%A8-%D7%A4%D7%A1-%D7%9C%D7%93%D7%99%D7%9D-rgb-%D7%93%D7%99%D7%92%D7%99%D7%98%D7%9C%D7%99-5v-%D7%A2%D7%9D-60-%D7%9C%D7%93%D7%99%D7%9D-%D7%9C%D7%9E%D7%98%D7%A8/) | 65 | page said in stock | Optional; peak current up to about 2.4 A (estimate) |
| ReSpeaker Lite (2-mic, R5b choice) | Seeed, **import** | about 95 (US$24.90) | no Israeli stock found | Shipping, VAT, warranty UNVERIFIED |
| Seeed mono enclosed speaker 4 ohm 5 W | Seeed or resellers, **import** | about 15 to 25 | no Israeli stock found | Plug type and lead length UNVERIFIED |

Totals from the R5b review (estimates, not quotes): electronics about ₪1,790 to 2,090; printed enclosure ₪1,146 to 1,644 (slicer weights and print-shop price tiers, VAT, support removal and delivery excluded); screws, nuts and feet ₪30 to 60; whole build about ₪2,970 to 3,790.

## 5. Printing facts (CAD/slicer, 18 Oct 2026)

- 15 print jobs, 925 g PETG, 80.8 h at i3-class speed (PrusaSlicer 2.9.6).
- Base cover P03 (210.0 mm) and tray P01 (207.9 mm) do not fit a 250 x 210 bed with brim. Fix: a 256 mm-class printer, or split files P03f/P03b and P01f/P01b.
- Colour bands need filament changes; whether a shop does this is UNVERIFIED.
- No real Haifa-area shop has been asked. PROJECT_PRINCIPLES requires 2 to 3 real shop quotes before any print decision.

## 6. Known gaps and risks (ranked, from R5b plus this check)

1. Speaker lead from the head to the base (about 35 cm): plug and length UNVERIFIED.
2. 210 mm base against shop printers.
3. Captive-nut pockets and self-tap bosses: strength assumed, not tested (coupon print about ₪50 to 100, estimate).
4. Heat in the closed base: not simulated.
5. USB power about 1.4 of 1.6 A: estimate only.
6. Sound leaking up the neck (no grommet modelled), grille loss.
7. Not modelled yet: camera bosses, rear-door hooks, speaker strap.
8. LED ring snap fit untested; no-solder LED kit not chosen.
9. HDMI cable stiffness in the neck bore.
10. New in this check: **physical mute** (an evidence gate in #4) is not in the R5b parts list or CAD. UNVERIFIED gap for a later design round.
11. New in this check: `print/QUOTE_PACKAGE.md` still mentions a fabric band, acrylic face cover and aluminium tube, which R5b removed. Regenerate it with `print/build_print_package.py` before sending it to any shop.

## 7. Next steps when the owner opens the hardware stage (not started)

1. Owner visual sign-off and the decisions in section 3.
2. Re-check every price and stock row in section 4 (new date, VAT-inclusive, delivery, warranty).
3. Fix the small CAD gaps (section 6, items 7 and 10), then regenerate the print package.
4. Design a coupon set (nut pocket, self-tap boss, snap ring, neck plug test) and get 2 to 3 real shop quotes.
5. Bench test the bought parts: power, heat log, speaker and mic chain, camera behind the face.
