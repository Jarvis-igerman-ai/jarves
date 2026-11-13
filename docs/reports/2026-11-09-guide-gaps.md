# Guide gap audit (JAR-119), 2026-11-09

Method: `test_guides.py` and `test_cli_parity.py` already enforce one guide per feature in `CATALOG` (28), a CLI command or
dashboard/voice marker per feature and tool group, and Hebrew in titles, steps and costs. So there are **no features without
a guide or parity entry**. Remaining gaps are optional sections of the guides rule ("fixing it"). Scan = a throwaway script
over `guides.load()` (110 guides).

## Filled in this PR (problems EN+HE)
greeting, background_tasks, persona, alerts, autonomy.

## Still missing `[[problems]]` (fixing it)
help, custom_wake_phrase (status planned), model_lab, context, face_recognition, camera_when_away, appearance,
skill_creation, usage, api_budget, dashboard, privacy, companion_api.

## Missing `say` examples
custom_wake_phrase, context, text_chat, ask_full, workflow_console, workflow_autopilot.

## Missing `test_he`/`test_en` ("try it now" text) 
About 55 guides (e.g. help, greeting, chimes, web_search, memory, scheduler, alerts, usage, runtime, updates, privacy,
agent_orchestrator, security_scans). Not required by the tests; owner decision whether worth filling.

## Missing `pages` link (no "Guide" link from a dashboard page)
About 55 guides, e.g. voice_ai, wake_word, music, gmail, telegram, timers, lists, camera, privacy, lan_dashboard.
Needs a page-by-page check, so not started.

## Not checked
Hebrew wording quality (no native review), and guides without `features`/`probe` (model_policy, audio_route,
release_hosting, beta_features, install_latest, release_run, android_companion, ask_full, workflow_*): intended by design?
UNVERIFIED.
