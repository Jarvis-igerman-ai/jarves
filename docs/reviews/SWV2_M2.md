# SW-V2 WP-M2 — Appearance (6.4, D08) and Skill creation with approval (6.2, D15)

Branch: `worktree-agent-ae7b56a8c181f3d5f` (based on `swv2-integrate`, re-merged at 8178c15). Issue: #17; Pi gaps: [#21](https://github.com/igalgerman/JARVIS/issues/21).

## Appearance
- **What exists:** four presentations, Face / 2D avatar / low-poly 3D avatar / Hidden, switchable at runtime through `appearance.set {mode}`. Three ways to switch: the voice tool `set_appearance` together with the offline Hebrew command ("תחליף לאווטאר תלת מימד", "תחזור לפנים"); the dashboard **Appearance** page; or `appearance.json`. The choice persists and is resent at every face connect.
- **One contract:** `face.gd` drives the behaviour in every mode, and the avatars draw `face.pose()`. Blink counts in the avatars equal the face's (25/25 vs 24 per 2 min). Head motion is 0.58 % r in 2D and 2.24° in 3D. Core and voice are not touched by a switch.
- **Art:** I made it myself with procedural primitives and no assets. Each avatar has a `STYLE` constant, and a `.glb` can replace the 3D primitives later.
- **Code:**
  - `godot/face/appearance.gd`, `avatar_2d.gd`, `avatar_3d.gd`
  - `face.gd` (`pose()`, `set_canvas_visible`), `face_app.gd` (host, `--appearance=`, checks), `assistant_ui.gd` (message)
  - `core/src/jarves/appearance.py`
- **Previews:** 28 PNGs in `godot/previews/appearance/`, captured automatically in an offscreen window with dummy audio (Godot 4.7 `--headless` renders no pixels). These are not an owner visual review.
- **Dashboard captures:** `docs/reviews/swv2_m2/m2_appearance_light.png`, `m2_newskills_{light,dark}.png`.

## Skill creation
- **Flow:** propose (model tool `skill_propose`, which only writes a draft) → static AST/manifest validation → owner approval bound to the sha256 of what was reviewed → installed DISABLED with no grants → owner grants a subset (storage, `network:<host>`, actions) → enable. Rollback and uninstall are also available.
- **Disabled means refused everywhere:** a disabled Skill is refused on every route: registry/voice/model/scheduler/offline (`SKILL_DISABLED`) and the dashboard test button. `run()` re-reads the state on every call.
- **Sandbox:** each call runs in its own child process (`python -I -S`, empty env with no keys, restricted builtins and imports, POSIX rlimits, 10 s timeout). The broker checks grants. Network access is https only, to the granted host only, and only to public addresses. Results count as untrusted output.
- **Format:** `SKILL.md` frontmatter follows the [Agent Skills spec](https://agentskills.io/specification) (`name`, `description`, Markdown body loaded on demand via `<id>_guide`), with one difference: ids use `_` rather than `-` because they prefix tool names.
- **Code:** `core/src/jarves/skills/generated.py`, `skills/sandbox_child.py`, `SkillRegistry.unregister`, dashboard `m2.js`.

## Feature registry / platform status
`features.register(..., targets=...)` is a new optional field, and `status()` now returns it. Registered features: `appearance` and `skill_creation`.

| Item | Windows | Pi |
|---|---|---|
| Face mode | `verified_on_device` | `planned` + #21 |
| 2D / 3D / hidden modes | `implemented_unverified` | `planned` + #21 |
| Skill creation | `implemented_unverified` | `planned` + #21 |

## Tests run (all silent and offline; $0 API spend)
- `python -m pytest core/tests -q`: 615 passed, 3 skipped (final commit).
- New: `test_appearance.py` (34) and `test_skill_creation.py` (42, including 29 malicious drafts, runtime containment, a tampered install and a SSRF refusal).
- Headless Godot `face_app.tscn -- --check`: PASS. Viewer `--check --no-core`: PASS.
- `GODOT_EXE=... pytest tests/test_face_protocol_godot.py` with appearance fuzz: PASS (3/3 reruns). The very first run after adding new scripts failed once (connection closed during Godot's first import). I could not reproduce it.

## Open questions for the owner
1. Visual review of the avatars (previews above): do you like the style, or should one be dropped or restyled? This is USER DECISION REQUIRED.
2. A plain "avatar" in speech maps to the 2D avatar. Should it map to 3D instead?
3. Should `hidden` also hide the window / turn the screen off? That is runtime/display (WP-M1), not done here.

## Residual risks
- AST checks plus restricted builtins are not a full sandbox, and Windows has no rlimits. An OS sandbox on Linux is tracked in #21.
- The 3D avatar on the Pi is unmeasured.
- The 3D twin viewer (`main.gd`) still shows only the face on its glass.

## Integration hooks (files owned by others: minimal edits)
- `core/src/jarves/app.py`: 4 lines after `camera_wire.attach` (`appearance.attach`, `generated.attach`).
- `core/src/jarves/brain.py`: 2 lines adding `appearance.INTENT_RULES`.
- `core/src/jarves/dashboard.py`: `PLUGIN_MODULES` += `jarves.appearance`, `jarves.skills.generated`.
- `core/src/jarves/dashboard_static/index.html`: 2 nav links + `m2.js` script.
- `core/src/jarves/features.py`: optional `targets` field (WP-M4 capability matrix can render it).
- `core/src/jarves/skills/__init__.py`: `unregister()` + docstring line.
