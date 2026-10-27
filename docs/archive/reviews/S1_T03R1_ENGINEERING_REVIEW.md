# JARVES S1.T03R1 — Pebble A engineering & component review (Issue #3)

**Date:** 2026-10-17. **Model:** Claude Opus 5.5 (`claude-opus-5-5`).
**Status:** research and design only. **Nothing purchased, no SKU locked, no CAD/Blender/Godot redesign.** Stopped for your review.
**Evidence labels:**
- **VERIFIED BY SOURCE**: producer or retailer page, with date.
- **MEASURED**: bounding boxes of the manufacturer STEP files, measured by me in FreeCAD.
- **PROVISIONAL**: an assumption or estimate.
- **REQUIRES PHYSICAL TEST**: only a real test can answer it.

Artifacts:
- `design/review/pebble_fit_study.py`: head packing study using measured parts.
- `design/review/pebble_layout.py` → `design/review/pebble_layout.png`: to-scale section and base plan, with numeric checks.
- The earlier `design/review/head_mount_and_rear_service.png`.

## 1. Bottom line (plain language)
1. **The display you can buy locally changes the shape.** The only round Pi display I found in an Israeli store is the **Waveshare 5″ 1080×1080 HDMI** (Piitel, ₪590 incl. VAT). It measures **Ø150 × 18 mm**. With the camera above it, it fits a Pebble head only if the head grows to **about 215 mm wide**, with a face panel of about Ø190 covering almost the whole front, much like your reference picture.
2. **No far-field microphone array is stocked in Israel** in what I could find. The XVF3800, ReSpeaker Lite and UMA-8 are all **import-only**. Locally available mics (Pirate Audio dual mic, USB-camera mics) have **no on-board echo cancellation or beamforming**, so they are unlikely to work at 3–5 m while JARVES is playing sound. This is the biggest procurement risk. **You need to decide whether one imported part is acceptable.**
3. **The XVF3800 is 100.5 × 101.8 × 9 mm** (measured STEP), not 108 × 108 × 18 as I said earlier; that figure was the cased version. Even so, it **cannot sit flat under the top of a curved head with short sound ports**. The practical fix is a **small raised "crown pod" on top of the head**: about 116 mm wide and +15 mm tall, a separately printed part that holds the mic board right under its holes. Without the pod, the ports become 25–41 mm tubes that resonate in the speech band (about 2–3.5 kHz), which is bad.
4. **Structure:** a continuous head spine → aluminium neck tube → bridge plate → base, as in the earlier diagram, now laid out to scale. All screws can be reached from the rear head cover, the rear door or the underside. Estimated tip-over angle is about 33° without ballast and about 39° with a 400 g plate. These masses are PROVISIONAL, so weigh the real parts.
5. **The base works on paper, but it is tight:**
   - Pi 5 under a bridge plate, ports to the rear door with 29 mm plug room.
   - Two front speaker pods, with only 6 mm clearance to the Pi.
   - Height reserved for the official M.2 HAT+.
   - A small reserved bay for an M.2-in-USB enclosure.
   - Overall about **H299 × W215 × D210 mm**: a modest growth that buys concrete fit.
6. **SSD:** don't build in a 2.5″ drive. Reserve height for an M.2 HAT+ or room for a small USB M.2 enclosure, plus one spare rear USB port. An external USB SSD is the simplest option.

## 2. Display options

| | **Waveshare 5″ HDMI round 1080×1080** (SKU 24369) | Waveshare 4″ DSI round 720×720 (SKU 24603) | Waveshare 3.4″ DSI round 800×800 (SKU 24321) |
|---|---|---|---|
| Outline / depth | **Ø150 × 18.05 mm** total (11.5 mm panel + 85.5×58 mm driver board): MEASURED STEP. Note: retailers list "7 mm", which is wrong for the assembly | **Ø126 × 18.2 mm** (10 mm panel + 85.5×65 mm board): MEASURED STEP | not stated in text (drawing image only): UNVERIFIED |
| Active area | 127.6 × 127.6 mm: VERIFIED (Waveshare wiki) | not stated | not stated |
| Interface | HDMI video + USB-C (touch + 5 V power). Pi 5 has **micro-HDMI**, so a micro-HDMI→HDMI cable is needed | DSI ribbon to Pi 5 22-pin DSI port (12 cm cable included); I2C touch; driver/overlay required | DSI; driver required |
| Power | 5 V, 400 mA typ, 2 W: VERIFIED (wiki) | not stated | not stated |
| Pi 5 | shown in Waveshare's Pi 5 example: VERIFIED | listed as supported: VERIFIED | listed as supported: VERIFIED |
| **Israel** (checked 2026-10-17) | **Piitel**: [link](https://piitel.co.il/shop/5inch-hdmi-round-touch-display-1080-x-1080-ips-10-point-touch-optical-bonding-toughened-glass-panel/), **₪590 incl. VAT**, SKU 74085. Add-to-cart shown, but stock not explicitly stated. UPS ≤5 business days; same-day Sharon/Center ₪90; pickup in Netanya. Warranty "products under warranty", duration not stated | **not found** at Piitel ("round display" / "DSI round" searches); **import-only** | **not found**; **import-only** |
| International | Waveshare US$129.99; The Pi Hut £124.80 (sold out) | Waveshare US$89.99 (**backorder**); OpenELab €94.45 | Waveshare US$84.99 |
| Fit (head study) | fits only the **growth head W215**, with camera above display, mic in pod or ReSpeaker Lite | fits **W205** heads with ReSpeaker Lite; XVF3800 only with long ports or a pod | not checked |

**Recommendation:**
- **5″ HDMI (local)** is the primary candidate. It gives the biggest, most expressive face and can be bought in Israel.
- **4″ DSI** is the import fallback. It packs into a smaller head and its ribbon cable is thinner, but it is backordered and import-only.
- The 5″ costs about 5% more head width and needs a slim, flexible HDMI cable through the neck.

## 3. Microphone array options (3–5 m target)

| | **Seeed ReSpeaker XVF3800 USB** | Seeed ReSpeaker Lite (XU316) | miniDSP UMA-8 v2 (XVF3000) |
|---|---|---|---|
| Mics / geometry | 4 PDM MEMS, circular. Firmware geometry ±33 mm, about Ø93: PROVISIONAL | 2 mics (linear): forward-biased, no 360° | 7 mics, circular |
| Size | **PCB about 100.5 × 101.8, 9.03 mm overall**: MEASURED STEP (with-XIAO variant) | 35 × 86 mm (Core Electronics): VERIFIED BY SOURCE; height PROVISIONAL | about Ø90 × 20 (review site): UNVERIFIED |
| Processing | AEC, beamforming, DoA, noise suppression, de-reverb, AGC; "up to 5 m": **vendor claim** | AEC, noise suppression, AGC, interference cancellation: vendor claim | adaptive beamformer, full-duplex AEC with barge-in: vendor claim |
| Speaker / AEC reference | 3.5 mm out + JST connector "supports 5 W amplified speakers" (Seeed wiki). Playing audio *through* the board gives AEC its reference natively. That last point is my inference: REQUIRES PHYSICAL TEST | USB audio out: reference routing UNVERIFIED | USB audio: UNVERIFIED |
| Pi | Raspberry Pi OS as a USB (UAC2) host: VERIFIED (wiki) | USB UAC2: VERIFIED BY SOURCE | USB |
| Price | **US$54.90, in stock** (Seeed, 2026-10-17) | ~US$29 (Robotshop/Hackster) | €159 (Audiophonics) |
| **Israel** | **not found** locally: **import-only** | **not found**: import-only | not found: import-only |
| Risk | single imported dependency | weaker far-field, no 360° | XVF3000 end-of-life claim (unverified); costlier |

**Local options found** (Piitel, 2026-10-17): Pirate Audio Dual Mic for Pi, ₪107, in stock. USB cameras with dual mic, ₪130+. Neither has documented on-board AEC or beamforming, so I don't recommend them for 3–5 m with playback. They are suitable only as a quiet-room bench comparison (REQUIRES PHYSICAL TEST).

**Recommendation:**
- **XVF3800** is the primary candidate. It is the only option with documented 4-mic far-field processing, AEC and a built-in 5 W speaker output, which simplifies audio wiring. It is **import-only**, and that is an explicit dependency.
- **ReSpeaker Lite** is the cheap import fallback and a bench comparison.
- Before committing the enclosure, bench-test the **bare boards** in your living room (V1–V2 below).

## 4. Head fit study (measured parts, analytic check)
`pebble_fit_study.py` packs the display stack, a connector keep-out, the Camera Module 3 Wide above the display, and the mic board under a flat crown, inside a 3 mm-wall ellipsoid head with a flat face facet. It is a geometric check, not CAD.

| Head | 5″ HDMI + XVF3800 under cap | 5″ HDMI + Lite / pod | 4″ DSI + XVF3800 under cap | 4″ DSI + Lite / pod |
|---|---|---|---|---|
| compact W205 × D~130 | no fit | no fit | no fit | fits, 6 mm port |
| mid W205 × D~150 | no fit | no fit | fits, **41 mm port** ✗ | fits, 6 mm port |
| growth W215 × D~160 | no fit | **fits** (facet Ø192, display 17 mm below centre), 6–7 mm port | fits, **28 mm port** ✗ | fits, 6 mm port |

Conclusions:
- **5″ local display:** use the growth head plus a crown pod (XVF3800) or ReSpeaker Lite.
- **4″ import display:** a W205 head works.
- Under-cap XVF3800 layouts are rejected because of long port tubes.

## 5. Proposed dimensions — compact baseline vs modest growth

| | Compact baseline (import 4″ DSI) | **Local-first (5″ HDMI) — recommended** |
|---|---|---|
| Head | W205 × D~150 × H~173 | W215 × D~117 (+pod) × H~184, face facet ~Ø190 |
| Mic | XVF3800 in crown pod, or Lite under cap | XVF3800 in crown pod (+15 mm), or Lite |
| Base | Ø200 × 75–85 | Ø210 × 85 (wider footprint: tip resistance, SSD bay, 29 mm plug room) |
| Overall (PROVISIONAL) | about H285 × W205 × D200 | **about H299 × W215 × D210** |
| Concrete gain | smaller, lighter | buyable display, far-field mic with short ports, more stable, expansion room |

Depth is driven by the base diameter. Ø190 is possible, but it loses plug room and the SSD bay.

## 6. Structure, layout and stability (see `pebble_layout.png`)
- **Load path:** head spine plate (behind the display stack) → top flange (4×M3) → **aluminium tube Ø40×2** (ID about 36, so plugs can pass) → bottom flange (4×M3) → **bridge plate** over the Pi → posts → base floor → rubber feet.
- **Why metal for the neck:** a vertically printed tube breaks along its layer lines when bent, for example if someone grabs the head. A hardware-store aluminium tube is cheap and much stronger. Flanges and plates print flat, which is their strong orientation.
- **Anti-rotation:** a key or flat on the tube plus clamping flanges, so the head cannot twist loose.
- **Numeric checks** (`pebble_layout.py`, all PASS):

| Check | Result |
|---|---|
| Pi 5 and SSD bay inside the base plan | inside |
| Speaker pods inside the base | inside, **6 mm in front of the Pi (tight)** |
| Plug room behind the Pi | 29 mm |
| M.2 HAT+ stack below the bridge | 52 ≤ 56 mm |
| Neck flange below the base top | below |
| Tip angle | ~33° without ballast, ~39° with ballast (PROVISIONAL masses; target ≥ 30°) |

- **Weak points to test:** heat-set insert pull-out in PETG, flange screw bosses, bridge-plate posts, and head shell around the spine screws.

## 7. Design for assembly and printing
**Parts** (print orientation; material PETG suggested over PLA because of Pi heat; printer, bed size and material **not chosen yet**):

| # | Part | Orientation / note |
|---|---|---|
| 1 | Head front bezel with face window | face-down; largest part about 215 × 200 × 40 mm, so it needs a **≥ 220 mm bed** or a split into 2 parts |
| 2 | Head rear dome | open side down; minimal supports |
| 3 | Crown pod + mic cap with port holes | small and quick to reprint; foam gasket |
| 4 | Head spine plate | flat |
| 5 | Neck flanges ×2 | flat (aluminium tube bought) |
| 6 | Bridge plate + posts | flat |
| 7 | Base shell (puck ring) | |
| 8 | Base floor with feet and ballast pocket | |
| 9 | Speaker pods ×2 | sealed; or reuse cheap ready-made speaker enclosures |
| 10 | Rear door | captive M3 thumb screws |
| 11 | Fabric-band frame | |

**Tolerances (PROVISIONAL, verify with test coupons):**
- 0.2–0.3 mm per side for sliding fits.
- Insert holes sized to the insert brand's datasheet.
- M3 screws everywhere practical, M2.5 for the Pi.

**Assembly order (no buried screws):**
1. Base floor: feet, ballast.
2. Bridge posts.
3. Pi 5 + Active Cooler on standoffs (and an M.2 HAT+ later if wanted); speaker pods; power inlet; spare USB panel port.
4. Bridge plate, then bottom flange + neck tube. Lower the base shell over the tube and screw it from the underside.
5. Feed cables up the tube, leaving about 30 mm service loops.
6. Top flange onto the tube, then the head spine.
7. Display into the front bezel (clamp ring/screws), then the bezel to the spine. Camera, then crown pod + mic board.
8. Connect everything and fit the head rear dome (screws from the back/bottom).
9. Fit the rear door.

Disassembly is the reverse. The display, camera, mic, Pi, speakers and power wiring are each reachable by opening one cover.

**Service caveat:** the Pi 5 microSD slot faces forward, under the bridge. Plan to **boot from an SSD/USB** or re-image over the network, or accept removing the bridge to swap the card.

## 8. Cable routing and power

| Cable | Route | Risk / requirement |
|---|---|---|
| Display video | Pi micro-HDMI → slim/flexible micro-HDMI→HDMI, about 30–40 cm, up the tube | HDMI plug size and bend: **measure the actual cable**; the 25 mm keep-out behind the display is PROVISIONAL |
| Display touch/power | USB-C from Pi USB2 or a 5 V branch | about 0.4 A typ |
| Mic array | USB-C from Pi USB2 | the speaker output goes from the board to the base speakers (2 wires down the tube) |
| Camera | Pi 5 22-pin CSI ribbon (Standard–Mini), about 30+ cm | **length availability and signal integrity: REQUIRES CHECK**; alternative: a USB camera in the head (simpler, uses a USB port) |
| LEDs / misc | thin wires | — |
| Power | USB-C inlet at the rear door, clamped to the base | **strain relief** before any connector |

- **Strain relief:** use P-clips or cable-tie anchors at both tube ends, and a grommet so cables can't rub and air can't leak acoustically.
- **Power plan (simple, no custom PCB):** start with the **official Pi 5 27 W (5 V/5 A) USB-C supply**. Pi 5 USB ports share a peripheral current limit, recalled from Raspberry Pi docs as about 1.6 A with a 5 A supply and 600 mA otherwise: **VERIFY**.
  - Loads: display about 0.4 A, XVF3800 plus its 5 W amp up to about 1 A peak (PROVISIONAL), and an SSD at 0.5–1 A. Together they **may exceed** that limit.
  - **Measure** with a USB power meter. If marginal, feed the display and amp from a separate off-the-shelf 5 V supply branch.
- No mains wiring inside JARVES.
- **USB budget:** Pi 5 has 2×USB3 + 2×USB2. Use them for the mic (USB2), display touch (USB2), SSD (USB3) and **one spare USB3 panel port at the rear**. No hub is needed at first.

## 9. Cooling and acoustics (mechanical)
- **Cooling:** Pi 5 Active Cooler (max 1.09 CFM: VERIFIED raspberrypi.com). A "chimney": low inlet vents in the rear door, outlet vents high at the rear of the base and the head back. PETG shells.
  - **Test:** 20–30 min CPU stress inside a printed base, read `vcgencmd get_throttled` and the shell temperature.
  - Dust: an optional fine mesh on the inlet.
- **Acoustics:**
  - Speaker pods are **sealed** and separate from the Pi air path, with drivers on rubber gaskets.
  - The mic board sits in the pod on foam, about 200 mm from the drivers.
  - Neck grommet seals the head from the base.
  - Port holes Ø1–1.5 mm aligned to the actual mic positions: take them from Seeed's 2D drawing.
  - The pod depth is small (6 mm port path).
  - Back volume of 38 mm pods is small, so bass will be limited (REQUIRES PHYSICAL TEST).

## 10. SSD and expansion — compare, reserve, don't install

| Option | Size / interface | Pros | Cons | Plan |
|---|---|---|---|---|
| **External USB SSD** | rear spare USB3 port | simplest, zero internal space | visible cable | default if storage is ever needed |
| **M.2 2230/2242 on official M.2 HAT+** | stacks 16 mm over the Active Cooler; PCIe 2.0 ×1, 500 MB/s; up to 3 A to the drive: VERIFIED raspberrypi.com | fastest, no cable | extra heat above the Pi; uses PCIe | **RESERVED** height under the bridge (52 ≤ 56 mm) |
| **M.2 in USB enclosure** | about 75 × 28 × 10 (PROVISIONAL) | cheap, removable | uses a USB3 port | **RESERVED** bay beside the Pi |
| 2.5″ SATA + USB adapter | 100 × 69.85 × 7 standard drive, plus adapter | cheap capacity | bulky, cable bend, more power | **not reserved internally**; use externally |

Added mass is 30–60 g, low in the base, which helps stability slightly. The SSD shares the USB power budget (§8).

## 11. Top risks and cheap checks

| # | Risk | Cheap check |
|---|---|---|
| 1 | **Far-field mic is import-only, and performance at 3–5 m with TV and playback is unknown** | bench-test the bare XVF3800 (and Lite) in your room **before** finalising the enclosure (V1–V2) |
| 2 | **Head grabbed or robot tipped**: neck or insert failure, toppling | printed insert-coupon pull test; neck flange print + tube; 30° tilt test on a mock-up with real masses; lift-by-head hold test (gently) |
| 3 | **Power budget over the Pi's USB limit**: brownouts, USB drop-outs | USB power meter at full volume, display bright, SSD busy |
| 4 | **Cables through the neck**: HDMI plug size, bend, CSI length, strain at connectors | print just the neck flange + tube section and pull the real cables through; tug-test the power inlet clamp |
| 5 | **Heat in the closed base**: throttling, PETG softening | stress test in a printed base shell, log temperature and throttling |

## 12. Validation plan — 35–50 m² room, 3–5 m (theory ≠ test)
- **Theoretical feasibility:** XVF3800 vendor claims (4-mic beamforming, AEC, up to 5 m) make 3–5 m *plausible*. That is **not** a result.
- **Setup:**
  - The real room, with its approximate size and furniture noted. JARVES position on a table or sideboard.
  - Talker positions at 1/3/5 m, at 0° and 60°.
  - A fixed 30-phrase list, spoken by a person, or played from a speaker at about 60–65 dB(A) at 1 m (phone SPL app, approximate).
- **Conditions:** quiet; TV speech at about 55–60 dB(A) at JARVES; music; **JARVES's own playback** at the intended volume (barge-in/AEC).
- **Measure:** record raw and processed USB audio (WAV). Score the hit rate by listening or with any offline recogniser (tool choice later; no AI backend now). Note DoA stability.
- **Suggested targets (PROVISIONAL, yours to confirm):**

| Condition | Target hit rate |
|---|---|
| Quiet, 3 m | ≥ 90% |
| Quiet, 5 m | ≥ 80% |
| With TV, 3 m | ≥ 80% |
| During own playback (barge-in), 3 m | ≥ 70% |

- **Stages:**
  - **V1:** bare board + external speaker.
  - **V2:** barge-in test.
  - **V3:** printed crown pod with ports.
  - **V4:** speaker pods (SPL at 1 m, rattle).
  - **V5:** full assembly, repeating V1 and V2.
  - Compare each stage with the bare board: the enclosure must not make results worse.

## 13. Practical buildability checklist (before any full print)
- [ ] Printer model, bed size and material confirmed (≥ 220 mm bed, or split head parts).
- [ ] Display and mic chosen and **bench-tested**; STEP files imported into FreeCAD (not boxes).
- [ ] Insert coupon, neck flange + tube, crown pod and port coupon printed and tested.
- [ ] Real cables pulled through the neck mock-up; strain relief verified.
- [ ] Power measured at worst-case load; thermal stress test passed.
- [ ] Tilt test ≥ 30° with real masses; lift-by-head test.
- [ ] Assembly and disassembly done once without breaking anything; every screw reachable.

## 14. Non-binding cost snapshot (2026-10-17)
- **Local:**
  - 5″ display ₪590 (Piitel).
  - Pi 5 8 GB: e.g. 4project ₪784 incl. VAT, Belshop ₪685 (7 business days). Search-result snippets only; product pages not re-verified.
  - Plonter Pi 5 kit ₪1,098–1,120 (snippet).
- **Imports, delivered cost to Israel UNVERIFIED:** XVF3800 US$54.90 (Seeed), ReSpeaker Lite ~US$29, 4″ DSI US$89.99, M.2 HAT+ from US$12.
- Israeli import VAT and duty thresholds and shipping cost were **not verified**. Check them at order time.

## 15. Decisions needed (ranked)
1. **Accept one imported part** (the far-field mic, XVF3800 recommended), or accept the weaker local-only audio?
2. **Approve the local-first size** (about H299 × W215 × D210 with the 5″ display), or prefer the compact import variant with the 4″ DSI (about H285 × W205 × D200)?
3. **Crown pod look** (a small raised cap on the head), acceptable visually?
4. **Bench-test purchase approval** for the bare mic board(s) and display, only when you choose.
5. **Printer:** model, bed size and material you have or plan to get.

## Sources (accessed 2026-10-17)
- Waveshare 5″ round: [product](https://www.waveshare.com/5inch-1080x1080-lcd.htm), [wiki](https://www.waveshare.com/wiki/5inch_1080x1080_LCD), [STEP zip](https://files.waveshare.com/upload/c/c9/5inch_1080x1080_LCD_3D.zip); [The Pi Hut](https://thepihut.com/collections/waveshare/products/5-hdmi-round-touch-display-1080x1080); [Piitel (IL)](https://piitel.co.il/shop/5inch-hdmi-round-touch-display-1080-x-1080-ips-10-point-touch-optical-bonding-toughened-glass-panel/)
- Waveshare 4″ DSI round: [product](https://waveshare.com/4inch-dsi-lcd-c.htm), [wiki](https://www.waveshare.com/wiki/4inch_DSI_LCD_(C)), [STEP zip](https://files.waveshare.com/upload/d/db/4inch_DSI_LCD_%28C%29_3D.zip), [OpenELab](https://openelab.io/de/products/waveshare-4inch-dsi-round-10); 3.4″: [product](https://www.waveshare.com/3.4inch-dsi-lcd-c.htm)
- XVF3800: [Seeed wiki](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/), [STEP](https://files.seeedstudio.com/wiki/respeaker_xvf3800_usb/3d/respeaker_mic_array_xvf3800_1_with-xiao-0820.stp), [store](https://www.seeedstudio.com/ReSpeaker-XVF3800-USB-Mic-Array-p-6488.html)
- ReSpeaker Lite: [Core Electronics](https://core-electronics.com.au/respeaker-lite-usb-2-mic-array-seeed-studio.html), [Robotshop](https://www.robotshop.com/products/respeaker-lite-usb-2-mic-array-with-onboard-ai-audio-processing-algorithms); UMA-8 v2: [miniDSP](https://www.minidsp.com/products/microphone-arrays/uma-8), [Audiophonics](https://www.audiophonics.fr/en/measurement-microphones/minidsp-uma-8-8-channel-usb-mems-microphone-module-p-16390.html)
- Raspberry Pi: [M.2 HAT+](https://www.raspberrypi.com/products/m2-hat-plus/), [Active Cooler](https://www.raspberrypi.com/products/active-cooler/), [camera docs](https://www.raspberrypi.com/documentation/accessories/camera.html)
- Israeli Pi retail (snippets): [4project](https://www.4project.co.il/product/raspberry-pi5-8g), [Piitel Pi 5](https://piitel.co.il/shop/raspberry-pi-5/), [Belshop](https://www.belshop.co.il/items/6556071-%D7%9B%D7%A8%D7%98%D7%99%D7%A1-%D7%A4%D7%99%D7%AA%D7%95%D7%97-%D7%A8%D7%A1%D7%A4%D7%91%D7%A8%D7%99-%D7%A4%D7%99-5-RASPBERRY-PI-%D7%96%D7%99%D7%9B%D7%A8%D7%95%D7%9F-8G), [Plonter](https://www.plonter.co.il/stores/main.tmpl?shop=Raspberry+Pi), Piitel searches "respeaker", "microphone", "DSI round" (2026-10-17)
