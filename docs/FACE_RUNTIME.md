# Shared face runtime contract — Godot

**Design intent:** one face scene, two presentation modes; NO duplicate animation logic.

### Mode A — Windows simulator
Full interactive 3D body in Godot; the face scene is shown on a virtual circular screen. Camera orbit/exploded-view UI is Windows-only.

### Mode B — physical JARVIS (future)
Raspberry Pi OS 64-bit, the same face scene/state machine occupies the 1080x1080 circular display. No need to run the complete 3D room. Godot Pi/ARM64 graphics & startup speed require physical validation before selection is final. If too slow, supply alternate face-display adapter consuming identical states; Core unchanged.

### State events
`face.set_state`: `idle`, `listening`, `thinking`, `speaking`, `happy`, `error`.
Optional future inputs: gaze target, amplitude envelope, blink. Do not pre-implement unrequested inputs.

### Visual fidelity
Reference is `assets/JARVIS_visual_reference.png`. Create animatable screen content, not pre-rendered eyes painted into shell texture. CAD governs screen opening & placement; Godot/Blender must preserve dimensions. Color/lighting can vary; shell geometry must match CAD.

### Current scope
**NOT NOW.** Do not start Godot or face implementation during the active FreeCAD blockout.
