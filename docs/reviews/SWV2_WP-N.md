# SW-V2 WP-N — Avatar restyle (2D richer, 3D no longer creepy)

Issue: #17. Pi gaps stay in #21. Owner feedback on the WP-M2 avatars was "2D is good but can be better; 3D is creepy".

## What changed
- **2D** (`godot/face/avatar_2d.gd`, NEW `avatar_2d.gdshader`): the canvas primitives are replaced by one anti-aliased SDF shader. It has:
  - a soft-shaded shell and a glass face plate;
  - cartoon eyes with a gradient iris, catchlights, lid shadow and a soft resting lower lid;
  - rounded brows and a mouth with inner fill and tongue;
  - expression shapes: `^ ^` crescents, heart eyes, tears with a wet lid line, an `o` mouth, and closed arcs.
- **3D** (`godot/face/avatar_3d.gd`, NEW `avatar_3d_head.gdshader`): the faceted low-poly human-like head is replaced by a cute rounded robot:
  - a smooth, wider-than-tall head with a matte shell and a dark glossy visor;
  - glowing pill eye panels with no pupils, so there is no stare;
  - glow brows that appear only when the face is expressive;
  - ear pods and an antenna;
  - gentle three-point lighting. The body was dropped because it read as a bright blob.
- Both avatars still own **no motion**. They draw `face.pose()` only, so blink counts match the face (25/25 vs 24 per 2 min). Head motion is unchanged: 0.58 % r (2D) and 2.24° (3D).
- `export_appearance_previews.cmd` now also exports cry / sad / surprised / love. The previews in `godot/previews/appearance/` were regenerated (40 PNGs).
- The style knobs are documented in `docs/APPEARANCE.md` ("Style" section).

## Evidence
- Before/after sheets:
  - `docs/reviews/swv2_avatar/avatar2d_before_after.jpg`
  - `docs/reviews/swv2_avatar/avatar3d_before_after.jpg`
- I made 4 visual iterations, each from offscreen screenshots that I looked at myself. Fixes along the way:
  - 2D frown/smile sign;
  - steep-arc SDF seams that showed as vertical light streaks in 3D happy/laugh, fixed with an exact polyline distance;
  - visor specular smudges;
  - the bright body blob;
  - a halo notch under squinted eyes;
  - the resting lid flattening the surprised eyes.
- These are automatic captures, not an owner review.

## Tests (all silent and offline; $0 API spend)
- Headless `face_app.tscn -- --check`: PASS.
- `GODOT_EXE=... pytest tests/test_face_protocol_godot.py`: 2 passed.
- `python -m pytest core/tests -q`: 766 passed, 3 skipped.
  - The first run had 35 failures caused by the disk being full (other processes); the rerun after space came back was clean.
  - Note: the disk is nearly full, so I ran with the main checkout's venv and `PYTHONPATH=<worktree>/core/src`.

## Platform status
| Item | Windows | Pi |
|---|---|---|
| 2D restyle | `implemented_unverified` (automatic captures only) | `planned` (#21) |
| 3D restyle | `implemented_unverified` | `planned` (#21: GPU cost unmeasured; MSAA 4x may need 2x) |

## Open questions for the owner
1. Is the new 3D robot (glowing pill eyes, no pupils) the direction you want, or should 3D be dropped in favour of 2D only?
2. Should the 2D eyes keep their realistic-looking iris/pupil, or switch to glowing panels like the 3D for one shared look?

## Integration hooks (files owned by others: minimal edits)
- `godot/face/face_app.gd` `_appearance_checks`: 4 lines now read `a3.eye_h` / `a3.eye_col` instead of the removed `a3.eyes[0].scale.y` / `a3.eye_mat`. The check logic is unchanged.
