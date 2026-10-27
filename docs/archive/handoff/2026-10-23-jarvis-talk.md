# Handoff 2026-10-23: `jarvis talk` (live conversation without the wake word)

Why: the owner reports JARVES does not hear them; `talk.down` was only accepted from the Godot face, which is not running.

What: `Core.talk_cli(start)` (app.py) is the one handler. Surfaces:
- CLI `jarvis talk [--stop]` (link `talk.start` / `talk.stop`, role cli, reply `talk.state`).
- Dashboard Home, Controls card, "Talk now" (POST /api/runtime `{talk: start|stop}` -> runtime.set).
- Tray entry "Talk (no wake word)".
- The Godot face already has its own path (Space/button -> `talk.down`, toggles live); unchanged.

Refusals: mic muted (MIC_MUTED), voice mode not live (NO_LIVE_VOICE), Core not running (`jarvis start`; dashboard answers 409).

Docs: wake_word.toml guide step + problem (EN+HE), capabilities wake_word row note, parity via the existing `wake_word` -> `wake` entry, Hebrew dashboard strings via catalog_add.

Status: implemented_unverified. Offline unit tests only (test_core cli talk, test_runtime tray/runtime talk); never run with a real mic or the real Realtime API.

Open: the tray entry needs a look on Windows; `jarvis talk` in push_to_talk mode refuses (no live voice) instead of starting push-to-talk.
