# SW-V2: privacy strip and "What does he see?" (Issue #17, follow-up #28)

Owner report: "in the UI of JARVES I can't see if the mic or camera is on; also add a button to show what he sees".

## What changed
- **Core `privacy.state`** (`core/src/jarves/runtime/privacy_state.py`): one message `{mic, camera, speaker, cloud_audio}`, derived from the real state every 250 ms (software mute, audio health, live session + its echo gate, push-to-talk capture, wake mic, sensor open, still in progress). Sent on change, at once to a new face, and as a heartbeat every 5 s. Also in every `status` snapshot (`privacy`). Unreadable = conservative ("listening" + "sending"). Schema: `docs/FACE_RUNTIME.md` "Privacy strip".
- **Camera** (`camera/service.py`, `frames.py`, `wire.py`, `switches.py`): "What does he see?" preview session. Gated by `camera` + the new `camera_preview` switch (default on, needs camera; registered in `features.py`, capability matrix in `capabilities.py`, guide `guides/data/privacy_indicators.toml`). 3 fps, long side 320 px JPEG (PNG fallback without OpenCV), at most 60 s per opening, frames only in RAM and only on the local link (never logged, saved or sent to the cloud). `photo` state while a still is taken (held 2 s), `last_seen` = the last camera_describe answer.
- **Link** (`link.py`, `app.py`): face may send `camera.preview` and `camera.set_enabled` (on = on-screen `confirm.request` first, spawned so the reply is not blocked); cli may send `camera.preview` (dashboard poll). `app.py`: snapshot `privacy` + one dispatch line.
- **Godot** (`godot/face/privacy_strip.gd`, `camera_preview.gd`, `assistant_ui.gd`, `core_link.gd`, `face_app.gd`): strip on its own CanvasLayer (face, avatar and room windows; never hidden by kiosk/Options; moves beside the assistant panel; shrinks for narrow windows). Okabe-Ito colours + fill/outline/slash + words (Hebrew, English with the locale). Mic chip = software mute, camera chip = switch, eye = preview window with orange frame, red dot, countdown, auto-close 20 s, last description. Inbound WebSocket buffer raised to 1 MB for frames.
- **Dashboard** (`runtime/__init__.py`, `dashboard_static/strip.js`, `strip.css`, `home.js`, `index.html`): the same strip at the top of Home (live via `/api/runtime`), mic/camera buttons, preview via `/api/camera/preview` (start / poll / close); paired phones get 403 (no camera images over plain-HTTP Wi-Fi).
- Docs: `docs/FACE_RUNTIME.md`, `docs/DEVICE_CONTRACT.md` (frames now cross the local link only for the user-opened preview).

## Tests and evidence
- `core/tests/test_privacy_state.py` (11 tests, fake camera, virtual audio): state matrix incl. gated/held greeting/unreadable gate, camera states, preview gates, rate limit, small JPEG, no image file written, auto-close, cap/lease, switch off mid-preview, photo + last description, real-WebSocket Core wiring (privacy.state on connect and mute, camera on needs confirmation, role allow-lists), dashboard API incl. 403 for a paired phone, end-to-end preview frames to a face client.
- Godot `face_app.tscn -- --check`: PASS (headless and 1000x700 window), incl. 19 states with distinct labels, malformed/stale/offline -> unknown, clicks, PNG + JPEG frames, malformed frames, refusal, auto-close. `main.tscn -- --check`: PASS.
- `GODOT_EXE=... pytest tests/test_face_protocol_godot.py`: 2 passed (new valid + malformed privacy.state / preview messages).
- Screenshots (offscreen, generated test pattern, no camera): `docs/reviews/swv2_privacy/` — strip states (he/en, avatar, 480 px, beside the panel, room window), preview window, `face_live_core.png` (real face against a real Core), `dashboard_home_preview.png` (Playwright click: camera switch on via confirm, then preview showing frames from a real Core with the fake camera). Demo: `core/tools/privacy_strip_demo.py`.
- Full suite: 1020 passed, 3 skipped, 6 failed; all 6 also fail on `swv2-integrate` before this work (google_contacts has no guide/matrix entry, Google setup step tests, M3 reminder, workspace setup doc; the base run also had a flaky launcher-help failure).
- Real API spend: $0. No real webcam, mic or speaker used.

## Open questions / UNVERIFIED (#28)
- Real webcam preview on Windows and Pi Camera (picamera2) never run; Pi JPEG decode cost unmeasured.
- On the future round 1080x1080 screen the top strip is cut by the circle at small sizes: needs a round-screen layout decision (USER DECISION: where the strip goes on the device).
- Colours/words: owner review by eye (colour-blind safety argued from Okabe-Ito + shape + words, not user-tested).
- "speaking" follows the face state / session speaking window; music playback in Pear is not reflected in the speaker chip.
