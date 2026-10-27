# SW-V2 WP-K: easy Google connection, simple dashboard Home/Connections, privacy switch (#17)

User asks: "just type `jarvis google start` and it helps me out; info in the dashboard; everything must be simple",
plus a clear privacy switch on the dashboard. Follow-up for real-account / Pi checks: #24.

## What changed

| Area | Files |
|---|---|
| Guided wizard `jarvis google start` (first connect, weekly reconnect, already-connected check), live self-check, error fixes, Connections API | `core/src/jarves/google_setup.py` (new) |
| CLI: `jarvis google start / test` (+ `--calendar-only --client --downloads --send --restart`), all hints now say `jarvis google start`; Hebrew voice lines "תריץ במחשב jarvis google start" | `core/src/jarves/google_auth.py` |
| Voice when not connected: tool `google_connect_help` (says exactly how to connect; picks up a connection made meanwhile and registers the real Calendar/Gmail Skills) | `core/src/jarves/skills/google.py` |
| Feature probe read a wrong file (`google_token.json`): now `<home>/google/token.json`; the reason tells him what to say | `core/src/jarves/features.py`, `core/tests/test_features.py` |
| Privacy switch (live, cross-process via `logs/privacy.json`, saved to config.toml), mask history, delete conversations | `core/src/jarves/privacy.py` (new), `core/src/jarves/logs.py`, `core/src/jarves/diagnose.py` (1 line) |
| Home page API (status, one hint, tiles) | `core/src/jarves/overview.py` (new) |
| Dashboard: Home is the start page, Connections page, privacy top bar, nav with Hebrew labels | `dashboard_static/home.js` (new), `index.html`, `style.css`, `app.js` (2 lines), `dashboard.py` (PLUGIN_MODULES) |
| Docs | `docs/GOOGLE_SETUP.md` now leads with `jarvis google start` |
| Screenshots tool | `core/tools/connections_screens.py` (new) |

### `jarvis google start` flow
1. State check: connected → only the live check; `needs_reconnect` → only sign-in; a client file already in
   `google/`, the data dir or Downloads → straight to sign-in.
2. Otherwise 7 console steps, one at a time (6 with `--calendar-only`): `projectcreate`,
   `flows/enableapi?apiid=calendar-json.googleapis.com`, `...gmail.googleapis.com`, `auth/branding`,
   `auth/audience` (test user), `auth/scopes`, `auth/clients` (links checked 19 Oct 2026 against Google's Calendar
   Python quickstart; `?project=<id>` added when known). Each step: Hebrew + English checklist, value copied to the
   clipboard (project name, JARVES, your address, the scope list), page opened, `Enter / s / q`.
3. gcloud: only if installed AND the user says yes: `gcloud projects create` + `gcloud services enable` (the only
   automatable steps; consent screen and Desktop clients have no gcloud path for personal accounts).
4. Watches Downloads for `client_secret*.json` (15 min), validates it is a Desktop client, stores
   `{client_id, client_secret}` owner-only in `google/client.json`, deletes the download. Contents never printed.
5. Sign-in (existing loopback + PKCE), then read-only self-check (calendarList 1 entry, Gmail profile), features
   `google_calendar` + `gmail_read` switched on (`gmail_send` only with `--send`), spoken-style summary with examples.
6. Fixes for access_denied / not a test user (also the "Access blocked" timeout), redirect_uri_mismatch / Web
   client, invalid_client, invalid_grant (weekly), org_internal, missing scopes, API not enabled; each opens the
   console page that fixes it. Progress in `google/setup.json` (owner-only; the address never served or logged).

### Dashboard
- **Home** (new start page): big status, ONE "what to do next" hint, tiles (connected, features on, today's cost,
  open issues, last talk), Connections list, what is on/off, Privacy card, last conversation.
- **Connections**: status checklist (client file, signed in, calendar/mail check, sending, days until the weekly
  reconnect, last problem + fix with a deep link), Start / Reconnect / Test / Disconnect, the 7 steps with
  "Open in Google Cloud", "Copy" and "Mark done" (collapsed once all done), plain-language explanation.
- **Privacy** top bar on every page + Home card: one click toggles `log.privacy`; plain state text in Hebrew and
  English; effect at once for new log lines and history rows (also in the Core when the dashboard runs separately:
  `logs/privacy.json` is polled at most once a second; a flag older than the process start is ignored), saved to
  config.toml through config_edit (backup). One-way actions with confirm: "mask existing history" (store turns,
  tool args, event fields, jarves.log*, tasks.jsonl; rewritten in place since the Core holds the log open) and
  "delete all stored conversations" (cost rows kept, detached, so cost totals stay true; log words masked too).
  Typed chat text reaches the log/store only with privacy OFF (REPLY `utterance`/`text` are content fields; tested).

Screenshots (automatic headless Edge captures, not a hands-on check), `docs/reviews/swv2_connections/`:
`wpk_home_light.png`, `wpk_home_connected_dark.png`, `wpk_home_privacy_on_light.png`,
`wpk_connections_not_connected_light.png`, `wpk_connections_connected_light.png`,
`wpk_connections_reconnect_dark.png`.

## Tests
`python -m pytest core/tests -q`: **789 passed, 3 skipped** (after merging swv2-integrate; 562 before). New: `tests/test_google_setup.py` (17: full first run
with fake Downloads/browser/Google, resume after `q`, weekly reconnect, already connected, calendar-only,
access_denied fix, disabled API, Web client rejected, download timeout, gcloud yes/no, voice help + live pickup,
feature reason, Connections/Home API, no secrets/address served), `tests/test_privacy.py` (6).

## Cost
$0 (no paid API calls; no Google login).

## Status per platform
| Item | Windows | Pi |
|---|---|---|
| `jarvis google start` | implemented_unverified (real account run by the user) | implemented_unverified (#24) |
| Connections / Home pages | implemented_unverified (headless screenshots only) | implemented_unverified (#24) |
| Privacy switch | implemented_unverified (unit + HTTP tests) | implemented_unverified (#24) |

## Open questions
1. Publishing the app ("In production", unverified) might remove the weekly reconnect for Calendar; Google does not
   clearly document it for restricted Gmail scopes. Recommendation: stay in Testing until the user tries
   Calendar-only in production. USER DECISION REQUIRED (later).
2. A first-time connect still needs a Core restart for voice conversations started before it (the typed/agent
   path picks it up at once via `google_connect_help`). Recommendation: acceptable; say so in the summary (done).

## Integration hooks (files owned by others, smallest change)
- `dashboard.py`: `PLUGIN_MODULES` += `jarves.google_setup`, `jarves.privacy`, `jarves.overview` (WP-B).
- `dashboard_static/app.js`: default page `conversations` → `home` (2 lines); `index.html` nav + `topbar` +
  `home.js`; `style.css` appended block (WP-B).
- `logs.py`: runtime privacy flag (`watch_privacy`, `write_privacy`, `runtime_privacy`; `is_sensitive` reads
  `privacy()`), `setup()` calls `watch_privacy` (WP-B/core).
- `diagnose.py` line in `review_skip_reason`: also skip when the runtime switch is on (WP-B).
- `features.py` `_google` probe: real token path + reason text; `tests/test_features.py` updated (WP-I).
- `skills/google.py` `register_google` split + `GoogleConnectSkill` (WP-G / WP-M4: if M4 changes calendar scopes,
  the wizard follows `google_auth.CALENDAR_SCOPES` automatically).
- Side effect during development: one manual smoke run of the CLI opened a real browser tab on
  console.cloud.google.com/projectcreate and put "JARVES home" on the clipboard (defaults were bound at import
  time; fixed so tests/fakes always win). No login, nothing submitted.

- Merge with swv2-integrate: WP-M1's runtime page (`pageHome`) is kept as **Controls** (`#/runtime`); WP-K Home is the start page; all nav entries from M1-M6 kept, with Hebrew labels.

