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

- `capabilities.py` (WP-M4): issue #25 added to KNOWN_ISSUES (created with `gh issue create`).
- After the swv2-integrate merge: WP-K's `home.js` calls `window.guidesSetupCard()` (one line); the guides link
  WP-K's Home privacy switch, Connections page and the M1 Controls page (`#/runtime`); the privacy probe reads
  WP-K's privacy flag file.

## Tests and evidence
- `core/tests/test_guides.py` (24 tests, offline): every registered feature has exactly one guide (fails for a new
  feature without one), required topics, Hebrew present in every title / sentence / step / phrase / problem, every
  link / page / action / check valid, find by Hebrew/English/alias, live state follows switches and probes, platform
  status from feature targets, briefing cap + hot update + TOOLS.md index, Test now checks (backup verify, Pear,
  camera, AI key, admin key with a fake Costs API), dashboard API, scripted help conversation (Google: step 1 ->
  "done" while not connected = "not yet" -> connected -> step 2 opens Connections -> finished), risky step needs
  on-screen approval, setup_hint on FEATURE_OFF and on MUSIC_PLAYER_OFFLINE, suggestions (once per conversation, not
  again within 7 days, again after, snooze, dismiss, quiet hours, "stop suggesting", failing feature first), the
  running Core (help tools registered, TOOLS.md has the guide index, backend instructions have the briefing),
  configurable workweek.
- Full suite after merging swv2-integrate: 882 passed, 3 skipped, 4 failed. 2 failures fixed (capabilities: #25
  added). The other 2 (`test_core.py::test_push_to_talk_music_flow`, `test_features.py::test_core_follows_switches`)
  only fail when nothing listens on Pear's port 127.0.0.1:26538 (the user's Pear was closed during the run); with a
  dummy listener on that port both pass. They existed before this package (music probe = TCP to Pear).
  Before the merge: 790 passed, 3 skipped.
- Headless captures (automatic, not a hands-on check), `python tools/guides_screens.py`:
  `docs/reviews/swv2_guides/guides_index_he.png`, `guides_index_en.png` (dark), `guides_guide_music_he.png`,
  `guides_guide_google_calendar_he.png`, `guides_guide_camera_en.png` (dark), `guides_guide_api_budget_he.png`,
  `guides_home_checklist.png` (WP-K Home + checklist), `guides_features_links.png`.
- Real silent GPT-Live run (`tools/silent_help.py`, virtual mic/speaker, nothing played):
  run 1: "איך אני מחבר את גוגל?" -> backend `explain_feature` -> "תפתח טרמינל ותקליד jarvis Google start ... תוודא
  בעמוד Connections שגוגל מסומן כ-OK" (PASS); "מה אתה יכול לעשות?" -> answered sensibly but the script's keyword list
  was too narrow (FAIL on the list only; widened). Run 2: both PASS ("...חיפושי מידע, תזכורות, פתקים, קבצים וזיכרון
  ... רק צריך לחבר את יומן גוגל"). Results: `docs/reviews/swv2_guides/silent_help_run1.json`, `silent_help_run2.json`.
  Cost: $0.0486 + $0.0486 = **about $0.10** (0.93 voice min each, 4 small backend responses each). Nothing else paid.

## Windows / Raspberry Pi
- Guides, help Skill, suggestions, hints: pure Python + stdlib TOML, no platform code. Windows:
  implemented_unverified (offline tests + one silent live run, no hands-on user check yet). Pi:
  implemented_unverified, follow-up https://github.com/igalgerman/JARVIS/issues/25 (also the hands-on checks and a
  real admin key).

## Open questions for the user
1. Suggestion frequency: at most one per conversation and the same one at most once a week (`SUGGEST_EVERY_DAYS` =
   7). OK, or rarer?
2. Monthly API budget: the guide button sets the suggested $20 (your "yes for now"); it is NOT set automatically.
   Should a fresh install set $20 by itself?
3. Guides open in Hebrew by default (English toggle). OK?
4. "ימי חול" default Sunday-Thursday (Israel), switchable to Monday-Friday on the Jobs page. OK?

## Not done / limits
- The front voice model gets only a short note (what needs setup + "delegate help questions"); the full briefing is
  in the backend instructions, fixed per conversation by GPT-Live (a switch flipped mid-conversation reaches the
  next one; TOOLS.md and tool errors with setup_hint follow at once).
- "Test now" never plays music or takes a picture; wake word / chimes only check the device and package.
- Music's Windows / Pi status shows "not yet verified" because the music feature declares no targets (owner WP-E).
