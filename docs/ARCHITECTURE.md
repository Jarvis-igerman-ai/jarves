# JARVES Stage 1 — minimal architecture contract

## Responsibilities
- **FreeCAD**: authoritative parametric manufacturing geometry and named independent parts (`.FCStd`); future STEP/STL per part.
- **Blender**: visual polish of geometry exported from CAD; materials, textures, lighting; `.blend`, `.glb`. NEVER source for printed geometry.
- **Godot**: Windows desktop 3D interactive twin (camera orbit, inspect, exploded view, click virtual devices), plus self-contained face scene shared with later on-device build (subject to Pi performance validation).
- **Python Core**: state and orchestration, skills, AI provider interface; independent of Windows/Raspberry Pi drivers.
- **DevicePort**: one boundary between behavior and I/O. Stage 1 uses simulated devices; later adapters wrap Windows USB / Raspberry Pi Linux devices.

## Actual runtime (when we reach integration, not NOW)
`Godot UI <-> localhost WebSocket/JSON for **control/events** <-> Python Core <-> DevicePort <-> Simulated devices`.
Audio *streams* and raw camera frames MUST use appropriate streams/references, not bulky JSON payloads. Simple localhost messaging only; do not invent a broker/microservice network.

## Geometry pipeline
`FreeCAD .FCStd -> STEP/mesh export -> Blender .blend/.glb -> Godot scene`.
For manufacturing: `FreeCAD -> STL/3MF -> print/slice`; not `Blender -> STL`.
FreeCAD unit: mm; Godot: meters (`1000 mm = 1 m`). Stable, readable part IDs across exports.

## Bidirectional review and downstream consistency

**Production flow:** FreeCAD → Blender → Godot; separately, FreeCAD → manufacturing export → slicer/print. Python Core → DevicePort integrates behavior and devices when that work is authorized.

**Review flow:** A defect discovered anywhere may require a correction in an earlier owning layer. Diagnose first, correct the **source of truth**, then regenerate and verify affected downstream consumers:
- Mechanical form, structural mount, dimensions, cable clearance: **FreeCAD → Blender → Godot** (and manufacturing outputs if they exist).
- Surface finish, colors, material presentation: **Blender → Godot** only.
- Camera navigation, viewer interaction, runtime face/UI: **Godot** only, except when underlying assets or geometry are actually wrong.
- Behavior or device contracts: **Core/DevicePort** and affected UI/integration checks only.

**Never reverse-export visual mesh edits into authoritative FreeCAD dimensions.** Avoid unnecessary full rebuilds. Record the owner, changed dependencies, validation and still-provisional assumptions in the issue/handoff. Physical proof requires real tests.

## Product transition (not current implementation)
`Python Core` and device event semantics remain; active device adapter changes. Godot 3D simulator is not required on the product screen, but the face animation scene/state schema should be shared. Godot ARM64 performance on Pi 5 is **UNVERIFIED**; if inadequate, replace display adapter only, preserve behavior contract. FreeCAD/Blender not installed on robot.

## Fidelity
Behavioral/event 1:1 is the design goal. Physical fit requires verified hardware dimensions, print tolerance tests, cable-bend room. Real microphone pickup, echo, heat, power, and acoustic output CANNOT be proven by the digital twin alone.

## Complexity guard
No ROS, event broker, plugins platform, movement physics, k8s, full CI pipeline, or separate services until a specific approved task requires it.
