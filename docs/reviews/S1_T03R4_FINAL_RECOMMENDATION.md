# JARVES — final engineering recommendation after independent review (S1.T03R4)

**Status:** recommendation for your approval. **No CAD redesign, purchase, printing or supplier contact has been done.**
**Process:**
- An independent **read-only reviewer** (Claude Opus-class, fresh context) formed its own alternatives first, then read the earlier ChatGPT proposal (Issue #4) and my R4 proposal, and treated both as mere candidates.
- I (Claude Opus 5.5, single writer) challenged its findings and make the final call below.
- This document **supersedes** [S1_T03R4_SIMPLE_BUILD_PROPOSAL.md](S1_T03R4_SIMPLE_BUILD_PROPOSAL.md) where they differ.

## 1. What the review found (and I confirmed)

| Proposal | Verdict |
|---|---|
| **Current design** | Not beginner-friendly: soldering-iron inserts, cut + filed metal tube, laser acrylic, glued fabric, about 40 screws, 14 steps |
| **My R4 proposal** | Right direction, but **flawed** (see below) |
| **Earlier ChatGPT proposal** | A useful checklist (pre-wired speakers, LED as optional, bed margins, Pi-location question), **not a buildable design**. Its demand for a **metal neck** is not justified for a joint only about 23 mm long: by calculation a thick printed column has a large strength margin (assumption, to be tested), while metal adds a supplier and parts |

**Flaws in my R4 proposal:**
1. **Mic contradiction.** I put the USB mic "under the flat crown". The project's own fit study shows the 100 × 102 mm XVF3800 **cannot** sit there with short sound ports.
2. **Neck joints.** The neck seat straddled the head's front/back seam, and the neck hung from the thin 3 mm base top.
3. **Clumsy closing.** It required plugging 5 cables into the Pi while holding a 1.2 kg top, flipping the robot to reach screws underneath, and every service trip kept the top tethered by its cables.
4. **USB load and sourcing.** The USB camera adds load to the Pi's shared USB limit (1.6 A), and its stock was unverified. "Speakers with a plug matching the mic board" was an assumption.

**Missed by everyone:**
- The LED ring needs a 5 V / logic connection without soldering, so it stays a later add-on with a blank ring for now.
- An open neck bore lets speaker sound leak to the mics, so it needs a foam plug.
- A panel-mount USB-C extension may break the Pi's 5 A power negotiation, so the official power cable should run straight through.

## 2. Recommendation — "head module + passive base" (reviewer's option B, which I endorse)

![concept](../../design/review/simple_build_concept.png) *(older concept sketch: the Pi location differs, see text)*

- **Head = the complete electronic module.** The Pi 5 (with push-pin cooler), the display, a Camera Module 3 on its **short** standard ribbon, and the mic all sit in the head.
  - The head's front/back seam moves back (about y = +50), so the **front "head body" is one strong piece**: face, crown and neck seat together.
  - The **back is a vented service lid** held by 3–4 screws. A beginner assembles and **tests the head on the table** before closing it.
- **Neck = a printed column** on the base floor tray (printed separately, bolted from underneath). The head body screws onto its top from inside the head. The load goes head → column → floor, not through a thin shell.
- **Base = a passive speaker stand.**
  - It holds a floor tray with two speakers in proper enclosures, drop-in ballast washers and cable clips.
  - The **base cover drops over the column and screws on from below before the head goes on**, while the base is still light and easy to flip.
- **Only 2 cables go down the neck:** the official power supply's own cable, passed straight through, and one speaker lead. All the fragile, high-speed cables (HDMI, camera ribbon, USB) stay short inside the head.
- **Fastening:** M3 screws into captive nuts in side-entry pockets wherever a joint gets reopened; self-tapping screws only for joints that are rarely opened. **No heat-set inserts, glue, soldering, cutting or filing.**
- **Appearance kept:**
  - The navy band and a dark face ring come from a **colour change during printing**, with no extra parts. This needs a shop that can do it; otherwise use a single colour.
  - The LED ring starts as a **printed blank ring**; LEDs come later as a plug-in option.
- **Expected outcome (estimates):**
  - **Printed:** 5–6 parts (head body, head lid, display retainer, floor tray, neck column, base cover), plus a crown pod if the XVF3800 is chosen.
  - **Fasteners:** about 18 M3 screws, about 10 nuts, 4 small Pi screws.
  - **Tools:** Phillips #1 and #2.
  - **Assembly:** about 7 steps, **1.5–2.5 h** for a beginner.
  - **Printing:** about **₪900–1,800**, dominated by the two big shells. This is an estimate from 3D Lab Haifa's published weight tiers, **not a quote**.

**My quick packing check** (a hand calculation with the fit study's ellipsoid equation; no script run):
- A Pi 5 with its cooler, standing behind the display stack, uses about **0.69 of the cavity limit** at its worst corner.
- USB plugs pointing down get about **38 mm** of clearance above the head floor.
- **Plausible on paper only**; it must be confirmed with real STEP models (gate G4).

## 3. Trade-offs you should know

| Trade-off | Detail |
|---|---|
| **Heat and fan noise in the head** | The Pi (up to about 10 W) sits about 8 cm from the mics. Mitigation: intake low at the chin, exhaust low at the back, keep the mic in a separate crown pocket or pod. **Must be tested (G4).** |
| **Head is about 100 g heavier and fuller** | The tip-over angle drops slightly (about 38° with ballast is estimated). Pi ports are reached by opening the head lid, which is easy. |
| **Base size vs the Haifa shop** | The approved **Ø210 base still does not fit** 3D Lab's stated 250 × 210 mm limit. Options: (a) find a shop with 256 mm printers (preferred, no visual change); (b) split base parts; (c) **shrink the base to about Ø194**, which option B makes room for but changes proportions slightly — **your call**. |
| **Not yet proven plug-and-play** | A **verified pre-wired speaker chain** for the chosen mic board, and the LED add-on. Until they're proven, the honest plan is: LEDs later; speakers only after a bench test. |

**Rejected fallback A′** (Pi in the base, corrected R4) is still possible if the head-heat test fails. It costs more neck cables, a harder closing step and tethered service.

## 4. Gates before any full print or purchase
1. **G1 Shops:** real bed size (≥ 256?), PETG, mid-print colour change, support-removal cost, VAT. Decides between Ø210, a split base or Ø194.
2. **G2 Bench test** of the bare electronics: Pi 5, display, XVF3800, camera and speakers with the official PSU. Covers speaker connector/pinout, mono vs stereo, and USB current (meter).
3. **G3 Measure real parts:** display mounting holes and connectors, mic positions, speaker size, PSU plug and cable.
4. **G4 Head packing + thermal:** CAD fit with real STEP files, then a 30-minute stress test in the first printed head. Pass criteria: no throttling, shell below about 50 °C, fan noise acceptable at the mics.
5. **G5 Mic choice:** XVF3800 + a small raised crown pod (best pickup) vs ReSpeaker Lite under a flat crown (keeps the silhouette, weaker at distance), then a port and echo test.
6. **G6 Test coupons** (about ₪50–100): captive-nut pocket, self-tap boss used 10×, neck column + floor joint (lift by the head at 2× weight).
7. **G7 Stability:** tilt test ≥ 30° with real masses.
8. **G8 Print order:** head pair first, dry-fit, then the base.

## 5. Decisions requested
1. **Approve architecture B** ("head module + passive base") as the direction for the next CAD round (recommended)?
2. **Base size:** keep Ø210 and look for a 256 mm shop (recommended), or accept a slightly smaller Ø194 base?
3. **Mic direction for the CAD envelope:** XVF3800 with a small crown pod, or ReSpeaker Lite with today's flat crown? Both stay unpurchased until G2/G5.
4. **Colours by print colour change** (navy band, dark face ring), with a single colour if the shop can't do it?
5. **LED ring:** blank first, LEDs as a later plug-in add-on (recommended)?
