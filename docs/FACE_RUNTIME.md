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

### Privacy strip and "What does he see?" (owner report 19 Oct 2026, Issue #17)
Always visible at the top of the face, avatar and room windows (`godot/face/privacy_strip.gd`, own CanvasLayer: `--kiosk` / Options never hide it) and on the dashboard Home page (`dashboard_static/strip.js`). Four chips (icon + Hebrew words, English when the Core's locale is not Hebrew): MIC, CAMERA, SPEAKER, AUDIO TO CLOUD, plus the eye button. Colour-blind safe: Okabe-Ito orange = capturing now, vermillion with a white edge = leaving the device, sky blue outline = this device only, yellow dashed = connected/waiting, grey + slash = off/muted/unavailable; the words always say it too.

`privacy.state` (Core -> face, on every change, at once to a newly connected face, heartbeat every 5 s; the same dict is in every `status` snapshot under `privacy`; `core/src/jarves/runtime/privacy_state.py`):
```
{v: 1, locale: "he"|"en",
 mic:         {state: listening|gated|wake_only|muted|idle|unavailable, source: "software", can_toggle},
 camera:      {state: photo|preview|presence|off|disabled|unavailable, can_toggle, preview_available,
               last_seen: {text, mode, ts} | null},
 speaker:     {state: speaking|quiet|unavailable},
 cloud_audio: {on: bool, state: sending|connected|connecting|off},
 ts}
```
Truthful by construction: derived from the real state each time (software mute, audio health, voice session open and its echo gate, push-to-talk capture, wake mic open, sensor open, still in progress), never from a request. `gated` = the session is open but silence replaces the mic while JARVES speaks; `cloud_audio.on` is true only while microphone audio is streamed (push-to-talk speech recognition is local). Unreadable = the conservative answer (an unreadable gate counts as `listening` + `sending`). The face shows "unknown" for a missing/malformed field, before the first message, when the link is down and when the last message is older than 12 s. `mic.can_toggle` / `camera.can_toggle` are hints; the face only sends a toggle for a known, available device.

Clicks (face role): mic chip -> `mic.set_mute {muted}`; camera chip -> `camera.set_enabled {on}` (off at once; on = `confirm.request` on this screen first, declined = listed for approval on the dashboard; answer `camera.enabled {on, active, reason}`); eye -> `camera.preview {open: true, seconds: 20}` / `{open: false}`. Answer `camera.preview_state {open, reason?, seconds_left?, last_seen}` (reasons `CAMERA_UNAVAILABLE`, `CAMERA_DISABLED`, `PREVIEW_DISABLED`, `timeout`, `closed`, `camera_off`); while open the Core sends `camera.preview_frame {seq, mime: image/jpeg|image/png, width, height, data: base64}` at 3 fps, long side <= 320 px, < 180 kB. The face shows a frame only while its preview window is open, ignores malformed/older frames, never stores them, closes after 20 s (Core cap 60 s per opening) and drops the texture on close or link loss. Dashboard (cli role): `{open: true, start: true}` opens, each `{open: true}` poll keeps it open 5 s and returns the latest `frame`; paired phones are refused (local computer only). Preview needs the `camera` switch and `camera_preview` (default on).

Checks: `face_app.tscn -- --check` (states, labels, malformed/stale/offline, clicks, preview frames incl. JPEG/PNG and malformed ones, auto-close); `core/tests/test_privacy_state.py`; `core/tests/test_face_protocol_godot.py` (fuzz). Screenshots: `--privacy=<listening|wake|muted|speaking|photo|unavailable|preview|unknown>[_en] [--preview-demo]` (generated test pattern, no camera), `docs/reviews/swv2_privacy/`.

### Visual fidelity
Reference is `assets/JARVIS_visual_reference.png`. Create animatable screen content, not pre-rendered eyes painted into shell texture. CAD governs screen opening & placement; Godot/Blender must preserve dimensions. Color/lighting can vary; shell geometry must match CAD.

### Current scope
SW-V1 software corrections are authorized by `docs/NOW.md`. Face/connection cues run in the Windows twin and face app; physical display performance remains UNVERIFIED.
