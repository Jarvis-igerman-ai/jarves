# JARVIS decisions — archived, not auto-read

## Firm decisions
- Name **JARVIS**. `ROBI` is legacy only.
- Stationary living-room personal assistant; no movement, servo, wheels or navigation in current scope.
- Build a **realistic product-focused digital twin**, not a game asset pretending to be printable.
- Windows first: CAD + rendered/interacting simulator + simulated hardware. Approve each microtask individually.
- Layered design: share Python behavior; substitute device adapters as simulation -> Windows real USB -> Pi Linux hardware.
- CAD is single mechanical source of truth. FreeCAD parametric -> Blender for polished visuals -> Godot interactive. Printed STLs originate from CAD only.
- User wants high-quality visual parity with original image **and** feasible disassembly / component fit. Image is reference, not manufacturable dimensions.
- Limited context & cost: minimal files, bounded checks, stop after a task, no unrequested features.
- AI: OpenAI cloud now; configurable self-hosted server (Ollama or other) later. Server optional; robot keeps basic UI/media functionality without it.

## Provisional engineering decisions (revisit only if evidence requires)
- Core Python; C++/Rust only for measured bottlenecks. Do not per-sample stream audio in Python.
- Godot for Windows simulator, face scene potentially reused on Raspberry Pi with Godot ARM64 after actual benchmark. Fallback renderer only if justified by measurement.
- Blender is **not** a physical-product runtime.
- Raspberry Pi 5, ~8 GB is initial **candidate client**, not a committed purchase. 16GB not useful for cloud-only client; local large-model plan should target separate network server.
- Computer/robot communicate over ordinary home network, optionally cloud OpenAI. No forced server dependency.
- Mains power for first physical build; reserve optional future battery space, no battery engineering now.
- No soldering preferred; use ready USB devices, screw-terminal or pre-wired components.
- User wants easy shell teardown with screws, interchangeable mounts, spare slots for future upgrades.

## Not resolved
Selected speaker/amplifier SKUs, confirmed screen mechanical drawing, detailed USB/power budget, mic firmware, exact camera (CSI vs USB), final enclosure materials, print tolerances, headset/speaker AEC results, any AI pricing at purchase time.
