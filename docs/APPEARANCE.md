# Appearance — face, 2D avatar, 3D avatar, hidden (SW-V2 M2, D08)

## What the user can choose
| Mode | What shows | Windows | Raspberry Pi 5 |
|---|---|---|---|
| `face` | the original shader face (`godot/face/face.gd` + `face.gdshader`) | verified_on_device | planned ([#21](https://github.com/igalgerman/JARVIS/issues/21)) |
| `avatar2d` | soft-shaded robot buddy, one SDF canvas shader (`avatar_2d.gd` + `avatar_2d.gdshader`) | implemented_unverified | planned (#21) |
| `avatar3d` | cute rounded robot head with glowing eye panels on a glass visor, own SubViewport (`avatar_3d.gd` + `avatar_3d_head.gdshader`) | implemented_unverified | planned (#21: 3D on the Pi GPU unmeasured) |
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

## Style (WP-N restyle)
The owner's feedback was "2D is good but can be better; 3D is creepy". Before/after sheets for all 12 expressions:
`docs/reviews/swv2_avatar/avatar{2d,3d}_before_after.jpg`. Previews: `godot/previews/appearance/` (idle, listening,
thinking, speaking, happy, laugh, love, surprised, sad, cry, error, offline, searching).

- **2D:**
  - A round shell with soft sphere shading (wrapped light from the upper left plus an accent rim light) and a dark glass face plate.
  - Big white cartoon eyes: gradient iris, two catchlights, lid shadow, and a slightly raised resting lower lid so the eyes do not stare. Rounded brows.
  - The mouth gets an inner fill and a tongue when it opens.
  - Expressions: `^ ^` crescents for happy/laugh, heart eyes for love, sliding tears and a wet lid line for cry, a small `o` for surprised, calm closed arcs for offline/blink.
  - Drawn with anti-aliased SDFs in one shader. No textures.
- **3D:** it no longer has a human face. It is a cute rounded robot:
  - A smooth head, wider than tall, with a matte off-white shell (lambert-wrap light, soft rim) and a dark glossy visor.
  - Glowing pill eye panels with one soft glint each. No eyeballs and no pupils, so nothing stares.
  - A simple light mouth. Glow brows appear only when the face is expressive (sad, cry, error, thinking, surprised).
  - Ear pods and an antenna that glow while listening or thinking.
  - Gentle three-point light: warm key, cool fill, blue back rim. The faceted low-poly head and its tooth-like mouth segments are gone.
- **Idle life is still the driver's own:** a breath swell plus an antenna bob (2D), a breath lift (3D), and the face's blinks and slow gaze. Nothing in an avatar runs on its own clock, except the tear slide, which only appears during the cry reaction.

### Knobs
Each knob is a `STYLE` entry in its script, pushed to the shader as a uniform with the same name.

| Knob | 2D (`avatar_2d.gd`) | 3D (`avatar_3d.gd`) |
|---|---|---|
| Size / shape | `head_r`, `plate_r`, `plate_y`, `plate_round` | `head_r`, `head_scale` (wider x = friendlier), `visor_r`, `visor_y` |
| Colours | `shell_lo` / `shell_hi` (shadow / lit shell), `plate`, `sclera`, `blush_col`, `tongue`, `heart_col` | `shell`, `visor`, `trim` (ear pods, stalk), `blush_col`, `heart_col` |
| Eyes | `eye_pos`, `eye_r`, `iris_r`, `pupil_r`, `relax_low` (resting lower lid: soft look, not a stare) | `eye_pos`, `eye_half` (pill half size), `eye_energy` (glow), `relax_low`, `look_shift` |
| Mouth | `mouth_y`, `mouth_scale` | `mouth_y`, `mouth_scale` |
| Motion budget | `look_head`, `look_iris`, `breath_y` | `yaw_per_look`, `pitch_per_look`, `breath_y` |

The expression colour, blink, gaze, lids, smile, mouth opening and reaction weights always come from `face.pose()`.
The knobs only change the look.

## Swapping the art later
A Blender `.glb` head can replace the 3D sphere if it keeps:
- the contract: `face` + `apply_pose(pose)`;
- the derived values the self-check reads: `eye_open`, `eye_h`, `eye_col`, `head_rot`, `mouth_gap` (3D) and `eye_open`, `head_off`, `mouth_curve`, `mouth_gap` (2D).

Pi performance is unmeasured (#21):
- 2D is one full-screen fragment pass, like the face.
- 3D is one sphere with the visor shader plus 6 small meshes, at MSAA 4x. Drop to 2x on the Pi if needed.

## Dashboard languages and direction (SW-V3 P0.2c, #17)
The web dashboard is multi-language. **English is the default.** The first load shows English unless the owner has
picked a language with the **language picker** (a dropdown in the top bar, `localStorage jarves-lang`). The browser
language never picks one. `#/page?lang=he` forces a language for screenshots.

| Locale | Direction | Catalog | Coverage |
|---|---|---|---|
| English `en` | LTR | `dashboard_static/locales/en.js` (every source key) | source |
| Hebrew `he` | RTL | `locales/he.js` | 100 % (test-enforced: a missing key fails) |

The owner chose **English and Hebrew only** (SW-V4 K, 20 Oct 2026): the Arabic and Russian catalogs were removed (the repository history keeps them). A saved `ar` or `ru` choice falls back to English.

**Adding a language** takes one catalog file and one registry entry:
1. Copy `locales/he.js` to `locales/<code>.js`. Set `code`, `name` (the language's own name), `dir` and `bcp47`.
2. Translate the `dict` values. The keys are the English texts from `en.js`.
3. Add `<script src="/static/locales/<code>.js" defer></script>` to `index.html`, before `i18n.js`. The script order is the picker order.

Text that is missing from a catalog shows the English text. Run `core/tests/test_dashboard_locales.py` to see the coverage.
Server texts (feature descriptions, issue titles, settings labels) get a `<field>_<locale>` sibling from
`jarves/locales.py`. To translate them into a new language, add a module like `hebrew.py` and one line in
`locales.TRANSLATORS`. Until then the dashboard shows the English field, translated by the client catalog where it can.

**RTL rules** apply to every RTL locale through `html[dir="rtl"]`. No rule targets one language.
- Layout uses logical CSS only: `margin-inline`, `padding-inline`, `inset-inline`, `border-inline-start` and `text-align: start`. A test fails on `margin-left` / `right:` and similar.
- The drawer, the search results, the status chips and the usage strip mirror on their own.
- Arrows are written mirrored in the RTL catalogs ("Open →" becomes "פתיחה ←"). An English text left untranslated in an RTL page gets its arrows flipped.
- An English text left in an RTL page is wrapped as one isolated LTR run (U+2068…U+2069). Its punctuation stays at the end ("What should I do first?", not "?What should I do first").
- Server detail texts get `dir` from their own letters (`JL.textDir`). A Hebrew sentence that starts with "Pear Desktop" still reads right-to-left.
- Code, paths, URLs, model ids, numbers (`.num`), `.kv` values, the URL/e-mail/number/date inputs and charts stay LTR islands (`unicode-bidi: isolate`).
- Wide tables (the system log) scroll inside their card, never the page.
- Module names in the log are data. They are never translated.

Evidence: `core/tools/v3_locale_screens.py` (Playwright) captures en/he at 1440 px and 390 px and checks each page automatically: lang/dir, no horizontal scroll, picker value, drawer side, search-results side, LTR islands. Output: `docs/reviews/swv3_p02c/` and the [report](reviews/SWV3_P02C_REPORT.md).
