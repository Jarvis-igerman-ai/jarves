> **Historical, superseded by [../ROADMAP.md](../ROADMAP.md).** Index: [DOC_MAP](../DOC_MAP.md).

# Roadmap — ARCHIVE, never auto-execute

**Stage 1 — Windows digital twin. One task at a time:**
- S1.T01a: simple FreeCAD parametric robot silhouette (**ACTIVE**).
- S1.T01b: verify component envelopes/clearances with part data; CAD assembly & provisional disassembly.
- S1.T02: Blender lighting, materials and polished render **derived from CAD**.
- S1.T03: Godot interactive 3D assembly — orbit, exploded view, click-to-inspect; shared face scene.
- S1.T04: Python Core with simulator DevicePort (button/GPIO, camera, mic, speaker, presence, light, network); simple localhost event path.
- S1.T05: integrated simulations of hardware presence/faults, face states, music/action stubs; optional actual cloud speech provider only when user approves.
- S1.T06: single integration acceptance pass and consolidated handoff. Do NOT continuously run all suites.

**Stage 2 — real peripherals connected to Windows (NOT ACTIVE):** ReSpeaker USB, speakers, webcam, optionally USB microcontroller as GPIO/sensor bridge. Real audio/visual tests; same Core contracts.

**Stage 3 — Pi/Linux bench test (NOT ACTIVE):** Raspberry Pi OS + display + camera + audio; implement DevicePort Linux only; check thermal, power, face runtime GPU and startup.

**Stage 4 — 3D print / assembly (NOT ACTIVE):** verified CAD and component drawings -> print mounts first -> full shell -> cable harness, accessible screws, acoustics, end-to-end acceptance.

**Stage 5 — personal assistant extensions (NOT ACTIVE):** calendar/mail with consent, personalized memory, media, smart home, home AI server, remote updates. No autoplay next stage.

Transition principle: hold Core + event contract stable where practical; environment, drivers, and deployment will legitimately need platform-specific work.
