# JARVIS — Hardware Validation Gates

**Status:** REFERENCE ONLY · not an active task  
**Read only when:** an approved task involves final part selection, prototype printing, wiring, bench integration, or installing hardware in the enclosure.  
**Do not change `docs/NOW.md` or start any validation based on this file alone.**

## Purpose
Prevent three expensive failures: a printed enclosure that does not fit, damaged electronics from unsafe connections, and a device that fails after closing the case. Simulation alone **cannot certify** physical fit, electrical safety, acoustics, or thermal behavior.

## Baseline rules
- **One gate at a time; user approval before advancing.** Minimal, focused evidence; no recurring full-system tests.
- Use **exact purchased part numbers and manufacturer documentation**. Mark guessed dimensions, connector orientation, power draw, and tolerances as `UNVERIFIED`; do not claim 1:1 fidelity.
- Use ready-made, low-voltage, appropriately rated adapters and connectors. **Do not design or wire 230 V mains**, batteries, custom charging circuits, or unverified power distribution as part of the initial build.
- **Power OFF before changing wiring.** Never apply 5 V to Raspberry Pi GPIO signal pins (3.3 V logic). Never connect a bare passive speaker directly to GPIO.
- If a connector, polarity, voltage, current, pinout, or safe power arrangement is unclear, **STOP** and ask the user for a datasheet/photo or qualified technician review.
- Any unexpected heat, odor, smoke, damaged cable, or unstable power: **disconnect power immediately** and do not retry until the cause is identified.

## Gate G1 — CAD fit / BEFORE full enclosure printing
**Prerequisite:** the physical parts or their verified mechanical drawings are available.

- [ ] BOM lists each exact model/revision: Pi board + cooler, 5-inch round display, microphone array, active speakers/audio board, camera, power supply, connectors and cables.
- [ ] CAD mounting dimensions, connector protrusions, cable bend/plug clearance, ventilation, speaker cavities, microphone sound openings, and removable service panel checked against that BOM.
- [ ] FreeCAD assembly has separate removable parts; access to screws and connectors demonstrated in a simple exploded/open view.
- [ ] Print **only one small critical fit sample** (e.g. display bezel or speaker mount), using the actual printer/material; test with the real component and adjust tolerances if needed. No universal tolerance assumption.

**Evidence:** BOM/datasheet links + one dimensioned CAD view + fit sample photo.  
**PASS:** critical geometry matches real parts, sample fits without force, no inaccessible connector.  
**BLOCKED:** missing verified dimensions, sample does not fit, or interference remains. **No full-case print.**

## Gate G2 — Electrical & audio bench / BEFORE enclosure installation
**Prerequisite:** hardware is assembled *outside* the case on a non-conductive surface.

- [ ] Single-page connection map: port → cable → device, supply voltage/current/polarity, USB power source; all verified against manufacturer docs.
- [ ] Budget **total current and peak load**, including Pi, display, microphone array, speaker amplification, camera, and any hub. Check **shared Pi USB current limit**; use separately powered USB hub/active speakers if needed. Do not assume one Pi power cable can supply every device.
- [ ] For any non-USB wiring, verify supply voltage/polarity with a meter before applying power. Use appropriately insulated connectors; no exposed shorts.
- [ ] Power on incrementally: Pi only → display → USB microphone/audio → speakers → camera → any GPIO sensor. Confirm each behaves normally before adding the next.
- [ ] One short integrated check: display and touch, microphone capture, clear playback, camera frame, and one GPIO/sensor event **only if that component is actually in scope**. Test that speakers do not trigger uncontrolled voice feedback.

**Evidence:** signed connection/power table + one short bench result.  
**PASS:** no power warnings/resets, connections match documented ratings, required I/O works.  
**BLOCKED:** unknown ratings, overheating, unexpected resets, or wiring mismatch. **Do not enclose or leave unattended.**

## Gate G3 — Enclosed-device acceptance / AFTER approved assembly
- [ ] Device closes without pinching cables; mounting is secure, ventilation clear, camera/mic apertures unobstructed, speakers isolated from loose parts.
- [ ] Real-device adapter produces the **same high-level event contract** as the simulator (e.g. `button_pressed`, `camera_frame`, `presence_changed`); only device-specific drivers/configuration differ.
- [ ] One representative end-to-end scenario: cold boot → face display → speech input/output → a simple skill → clean shutdown/restart.
- [ ] One **bounded** thermal/audio check at normal intended volume and workload (initially 20–30 minutes): inspect logs for power/throttling issues and measure/inspect case temperature. Compare with component specifications; do not invent pass temperatures.
- [ ] Rear service cover opens, and one module can be disconnected/reinstalled **with power off** without damaging wiring or reprinting the entire enclosure.

**Evidence:** 1 short run note + temperatures/log finding + 2 photos (closed and service-open).  
**PASS:** features work in the case, no abnormal heat/power issues, serviceability demonstrated.  
**BLOCKED:** unsafe warmth, audio feedback, throttling, loose mount, inaccessible part. Fix locally and repeat **only the failed check**.

## Minimal gate report (max 6 lines)
```text
Gate: G1 / G2 / G3     Status: PASS / BLOCKED / NOT RUN
Parts/revisions checked:
Evidence (files/links):
Failures/UNVERIFIED:
One required action (if any):
Approval requested for next gate: YES / NO
```

## Agent execution limits
- This is an **approval checklist**, not an instruction to run tests now.
- Read only this document and the relevant part datasheet when a gate is explicitly assigned.
- No new test framework, continuous bench loop, unrequested redesign, multi-agent review, or purchasing without approval.
- At most **two distinct troubleshooting attempts per blocker**, then report and stop.
- After a gate, write the 6-line report in `docs/HANDOFF.md`, notify the user, and **STOP**.

## Official references (check exact hardware revision)
- Raspberry Pi hardware/power and USB budget: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- Waveshare 5-inch round 1080×1080 LCD: https://www.waveshare.com/wiki/5inch_1080x1080_LCD
- ReSpeaker XVF3800 introduction: https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/
- ReSpeaker XVF3800 FAQ (speaker outputs): https://wiki.seeedstudio.com/respeaker_xvf3800_faq/
- Prusa: design, clearances, and print-fit trials: https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135
