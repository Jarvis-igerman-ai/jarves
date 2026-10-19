# Shared face runtime contract — Godot

**Design intent:** one face scene, two presentation modes; NO duplicate animation logic.

### Mode A — Windows simulator
Full interactive 3D body in Godot; the face scene is shown on a virtual circular screen. Camera orbit/exploded-view UI is Windows-only.

### Mode B — physical JARVIS (future)
Raspberry Pi OS 64-bit, the same face scene/state machine occupies the 1080x1080 circular display. No need to run the complete 3D room. Godot Pi/ARM64 graphics & startup speed require physical validation before selection is final. If too slow, supply alternate face-display adapter consuming identical states; Core unchanged.

### State events
`face.set_state`: `idle`, `listening`, `thinking`, `speaking`, `happy`, `error`.
`appearance.set {mode: face|avatar2d|avatar3d|hidden}` picks the presentation; face.gd stays the one behaviour driver and every avatar draws its `pose()` (see `APPEARANCE.md`).
`speaker.level` carries a bounded normalized RMS envelope from streamed playback (no audio content). Mouth closes after 250 ms without an update. Shared face shows connection/activity labels and status color in both presentations. Natural blinks remain; idle eyes do not wander or simulate speech.

### Visual fidelity
Reference is `assets/JARVIS_visual_reference.png`. Create animatable screen content, not pre-rendered eyes painted into shell texture. CAD governs screen opening & placement; Godot/Blender must preserve dimensions. Color/lighting can vary; shell geometry must match CAD.

### Current scope
SW-V1 software corrections are authorized by `docs/NOW.md`. Face/connection cues run in the Windows twin and face app; physical display performance remains UNVERIFIED.
