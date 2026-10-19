# Appearance — face, 2D avatar, 3D avatar, hidden (SW-V2 M2, D08)

## What the user can choose
| Mode | What shows | Windows | Raspberry Pi 5 |
|---|---|---|---|
| `face` | the original shader face (`godot/face/face.gd` + `face.gdshader`) | verified_on_device | planned ([#21](https://github.com/igalgerman/JARVIS/issues/21)) |
| `avatar2d` | stylized robot buddy drawn with canvas primitives (`avatar_2d.gd`) | implemented_unverified | planned (#21) |
| `avatar3d` | low-poly faceted head built from primitive meshes in its own SubViewport (`avatar_3d.gd`) | implemented_unverified | planned (#21: 3D on the Pi GPU unmeasured) |
| `hidden` | no renderer drawn (black); Core and voice keep running | implemented_unverified | planned (#21: backlight off not wired) |

Switch by voice ("תחליף לאווטאר תלת מימד", "תחזור לפנים"; tool `set_appearance` or the offline command rule),
on the dashboard **Appearance** page, or by editing `<state_dir>/appearance.json`. The choice persists.
`hidden` is only "no picture"; hiding/closing the window and the tray are the runtime package (WP-M1).

## One contract for every renderer
- The Core sends **`appearance.set {mode}`** (Core -> face) at every face connect and on every change; nothing else
  changes for a switch. All other events are unchanged and shared: `face.set_state`, `speaker.level`, `mic.level`,
  `face.emotion`, `face.set_gaze`, `camera.indicator`, `assistant.activity`, `status`.
- `face.gd` is the single **behaviour driver** in every mode (blinks, saccades, breathing, reactions, speech-level
  mouth, camera gaze). Its `pose()` is the expression output; `avatar_2d.gd` / `avatar_3d.gd` only draw it. So the
  avatars cannot blink or move more than the face (user feedback: calm, natural, no over-blinking).
- The activity pill and the truthful status dot stay visible in the avatar modes.
- Malformed / unknown modes are ignored (current presentation stays). Avatars are built on demand and freed when left.

## Tests and evidence
- `face_app.tscn -- --check` (headless): every mode applied, face state unchanged by a switch, malformed modes ignored,
  happy/speaking/error/offline reach both avatars, avatar blinks = driver blinks (8–16/min), head motion < 2 % r (2D)
  / < 3.5° (3D). `core/tests/test_face_protocol_godot.py` fuzzes appearance messages against the real face.
- `core/tests/test_appearance.py`: words -> mode, offline Hebrew command, persistence, voice tool, push on connect,
  dashboard change reaches the face.
- Previews: `godot/face/export_appearance_previews.cmd` -> `godot/previews/appearance/` (offscreen window, dummy audio;
  Godot 4.7 `--headless` cannot render pixels). Automatic captures, not an owner visual review.

## Swapping the art later
Each avatar keeps its look in a `STYLE` constant (colours, proportions). A Blender `.glb` head can replace the
primitives in `avatar_3d.gd` as long as it keeps `face` + `apply_pose(pose)`. Pi performance is unmeasured (#21).
