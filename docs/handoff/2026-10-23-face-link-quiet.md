# Quiet face link when the window is hidden - 2026-10-23

Branch `fix/face-link-quiet` (local, not pushed, no PR).

## Problem
`jarvis start` (background, window hidden by design) reported `components.face_link = unavailable "waiting for the Godot face to connect"` with action `start godot\run_face.cmd`, which made the whole Core "degraded" (exit code 4) although nothing was wrong.

## Fix
- `GodotLink.face_expected` (set by the Runtime): when no face is wanted, `face_link` is `disabled` with detail "face hidden: shown on request" and action `jarvis show`. `disabled` never degrades the Core. A window closed on request (hide / user close) also becomes `disabled`.
- `Runtime.face_expected()`: false when `display_hidden` is set, or in background mode until a show was requested (`jarvis show`, tray, dashboard, voice, `start --show`). Foreground and desktop modes keep the old behaviour (a missing face is still "waiting").
- Asked-for window that fails is loud: Godot not found -> `face_link` unavailable + `display` degraded, text "Godot is not installed ... set GODOT_EXE ... jarvis show", action "start <godot>/run_face.cmd by hand, or run: jarvis diag"; window started but no connection within 30 s (`FACE_CONNECT_WAIT_S`) -> same loud error; window process crashing after show -> face_link unavailable with the exit code.
- `jarvis show` now prints `ERROR: ...` and the run_face.cmd / `jarvis diag` hint on stderr and exits 1 when the show fails.
- Guide `runtime.toml` (EN+HE): two new problems entries; Hebrew catalog part via `tools/catalog_add.py`. No new command or option, so no CLI parity change.

## Tests (offline, fake launcher/link)
`core/tests/test_runtime.py` (new tests at the end), `test_swv3_p01.py::test_b07...` adjusted (requested close is now `disabled`).

## Open / unverified
- `jarvis show` actually launching a real Godot was not run (no Godot here); the launch path (`launch_godot` -> `find_godot` / installed face) is unchanged and covered by the fake launcher only.
- Environment-only failures in this sandbox (unrelated, not touched): pystray test, 3 sandbox-path tests in test_review_c. Note: the venv imports jarves from another worktree; run tests with PYTHONPATH=core/src.
