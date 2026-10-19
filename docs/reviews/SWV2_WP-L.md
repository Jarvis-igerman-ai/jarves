# SW-V2 WP-L: guides for every feature, and JARVES as his own guide (Issue #17, follow-up #25)

User asks: "in the dashboard need same guides for each feature" + "JARVES himself must know ALL his features and
operations and guide the user" + guides for the real API bill (admin key), the monthly API budget and the scheduler
settings (quiet hours, missed-job max age, what "ימי חול" means).

## What was built

**One source of truth: `core/src/jarves/guides/data/<id>.toml`** (33 guides, Hebrew + English). Each guide: one plain
sentence, steps (each with an optional button action `feature_on:` / `open:` / `command:` / `test` / `set_budget:`
and an optional live check `enabled:` / `available:` / `active:` / `probe:`), example phrases (he + en), what to
expect, how to test, cost, privacy, common problems + fixes, related guides, the dashboard pages that link to it.
Nothing about the CURRENT state is written in the text: on / off / setup needed / why, and the Windows / Raspberry Pi
status, come live from `jarves.features` (`status()`, `targets`) and a few local probes (AI key, admin key, budget,
backup, privacy). `core/src/jarves/guides/__init__.py` loads them (hot reload on file change).

Guides: help, voice_ai (connect the AI), wake_word, custom_wake_phrase (planned), greeting, chimes, voice_change, music,
web_search, background_tasks, google_calendar (+ calendar_write), gmail (read + send), memory (+ LLM pass), persona
(+ assistant_identity), notes_files, scheduler (+ reports; max age 6 h, ימי חול = Sun-Thu, both configurable), alerts
(quiet hours 22:00-07:00), autonomy, diagnosis, camera, face_recognition, camera_when_away, appearance,
skill_creation, usage, api_bill (admin key), api_budget ($20 suggestion, 75/90/100 %), backup, runtime (background,
show/hide, tray, autostart), updates (update_check), dashboard, lan_dashboard (dashboard_lan), privacy.
Features registered by packages not merged yet (calendar_write, dashboard_lan, update_check, assistant_identity) are
already covered; until they merge the guide says "not in this version yet".

**Dashboard** (`dashboard_static/guides.js` + `guides.css`, API in guides/__init__.py):
- `GET /api/guides` (index + "What should I do first?" checklist + setup-needed count + suggestion settings),
  `GET /api/guides/<id>`, `POST /api/guides/<id>/test` (Test now), `POST /api/guides/<id>/step/<n>` (step buttons),
  `POST /api/guides-help` (snooze / dismiss / reset).
- Guides index (by category, state pill, red border = setup needed), per-feature page (state + why, the feature
  switches, steps with buttons, Copy for terminal commands, What to say, Test now, cost / privacy, problems,
  Windows / Pi table, see also), Hebrew / English toggle (Hebrew default).
- "Guides" nav entry with a setup-needed badge, "Guide" link on every Features row (`#/guide-for/<feature>`) and on
  each feature's own page (Usage, Backup, Jobs, Notes, Files, Alerts, Autonomy, Memory, Workspace, New Skills, Tasks,
  Issues, Appearance, Home, Connections), and the "What should I do first?" card on Home.
- Google: the guide links to WP-K's `jarvis google start` and Connections page; no second wizard.

**JARVES** (`guides.attach(core)`, called in Core.start after every Skill):
- Capability briefing in his backend instructions (recomputed per request / session, capped at 2600 chars, grouped by
  reason so one missing key is one line) + a short front note for GPT-Live (help questions are delegated; what needs
  setup now). The guide index is in TOOLS.md (rewritten whenever a switch changes).
- Help Skill: `explain_feature`, `whats_not_set_up`, `setup_next_step` (ONE step, waits; `done=true` runs that step's
  live check, so "done" with Google still unconnected answers "not yet: Google account not connected"),
  `run_guide_step` (safe steps only: low-risk switch, check, open page, $20 budget; medium/high-risk switches go
  through the on-screen approval `registry.confirm`, else a dashboard request), `open_guide` (opens the dashboard on
  the guide page; the one-time key never reaches the model), `setup_snooze`, `stop_suggestions`.
- Proactive suggestions (`Suggester`): at most one per conversation, inside the session instructions ("one short
  sentence, after answering or at a natural pause, never while the user speaks"), order = features the
  self-diagnosis saw failing (open `tool_fail:` issues), then the first-run checklist, then other switched-on but
  unavailable features. Each guide at most once per 7 days, persisted snooze / dismiss (`guides_state.json`), never in
  quiet hours (the alerts engine's quiet hours), feature switch `proactive_help` ("תפסיק להציע").
- Setup hints: `SkillRegistry.setup_hint` adds a machine-readable `setup_hint` (guide, reason, next step, dashboard
  page, what to do) to FEATURE_OFF and to error codes a guide owns (MUSIC_PLAYER_OFFLINE, MUSIC_NOT_PAIRED, ...).

**Extra guides asked by the user**
- Real API bill: where to create a read-only Admin key (platform.openai.com/settings/organization/admin-keys), new
  `jarvis key --admin` (checks `sk-admin-`, saved in secrets.env, `--admin --remove`), Test now = one read-only Costs
  API call (free, no model call) through WP-M5's `usage.Monitor.refresh_costs`.
- Monthly API budget: one button sets the suggested $20 (WP-M5 `usage.set_settings`), how the 75/90/100 % alerts
  are delivered (75/90 quiet line, 100 spoken; once each per month), and that only OpenAI's own limit really stops
  charges.
- Scheduler settings: new setting `scheduler.workweek` ("0-4" Sun-Thu default, or "1-5"), used by `parse_when`;
  the Jobs page now has "missed job max age (hours)" and "ימי חול" controls; quiet hours stay on the Alerts page.

## Integration hooks (files owned by other packages, smallest possible change)
- `skills/__init__.py` (WP-D): `SkillRegistry.setup_hint` + `_hinted()` on FEATURE_OFF and failed results.
- `app.py`: `self.guides = guides.attach(self)` after `ops_attach`; `self.guides.attach_live(live)` next to
  `feat.attach_live`; guide index appended to TOOLS.md; `open_dashboard(page="")` opens `url + "#/..."`.
- `dashboard.py` (WP-B): `"jarves.guides"` in PLUGIN_MODULES. `index.html`: guides.css/js tags (the nav entry is
  added by guides.js). `app.js`: Guide link in pageFeatures rows, `guidesSetupCard()` on pageHome, nav highlight.
  **WP-K's new Home (home.js / overview.py) should also call `window.guidesSetupCard()`** (one line) after merge.
- `cli.py`: `jarvis key --admin`. `scheduler.py` / `userdata.py` / `m3.js` (WP-M3): `scheduler.workweek` setting,
  `parse_when(..., workweek)`, `api_jobs` returns max_age_s + workweek, Jobs page settings row.
- New feature `proactive_help` (default ON, low risk) registered by guides/__init__.py.
- Rule for everyone: a new `features.register(...)` needs a guide in `guides/data/` or
  `test_every_registered_feature_has_exactly_one_guide` fails.

## Tests and evidence
See the bottom of this file (filled in after the runs).
