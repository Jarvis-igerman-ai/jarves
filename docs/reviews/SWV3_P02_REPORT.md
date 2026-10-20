# SW-V3 P0.2 — Dashboard UI/UX redesign: report

**Branch:** `v3-p02-dashboard` (based on `swv2-integrate` @ e95eb6f) · **Issue:** #17 · **Date:** 20 Oct 2026 · **Model:** Claude Opus 5.5

## What changed

| Area | Before | After |
|---|---|---|
| Language | Mixed "English · עברית" labels everywhere; Guides/Setup had their own toggle | **Hebrew is the default (RTL, `<html lang="he" dir="rtl">`)**, with one global **English** switch in the app bar (`localStorage jarves-lang`, `#/page?lang=en` for captures). Labels show one language at a time. Guides and Setup now follow the global switch. Dates and times use `he-IL` / `en-GB`. |
| Navigation | 24+ flat entries, 4 more injected by scripts | **7 groups**: Overview (Home, Controls & runtime, Connections, Devices & access) · Activity (Conversations, Issues, Background tasks, System log) · Everyday (Calendar, Jobs, Reports, Alerts, Notes, My files) · Assistant (Identity & voice, Appearance, Memory, Persona files) · Capabilities (Setup & add-ons, Features & switches, Skills & health, New Skills, Autonomy) · Usage & cost (Usage & limits, Costs) · System & help (Guides, Settings, Backup). **Every page is kept.** A test checks that every page registered in `PAGES` or routed in `app.js` has a nav entry. |
| Layout | Sidebar became a wrapped row of links on phones | Below 900 px the sidebar becomes an off-canvas **drawer** (menu button, scrim, Esc, focus moves to the active link, slides from the right in RTL). Sticky app bar. Content is centred up to 1240 px. Phone card layout for the Features matrix kept, with translated field labels. |
| Search | none | **Global search** in the app bar (`/` or Ctrl+K): pages (Hebrew and English names plus keywords), every feature (opens its Guide), every guide, and "search conversations / system log for …" actions. Supports arrow keys, Enter and Esc. |
| Status on every page | Core dot in the sidebar; privacy switch in a top bar; mic/camera only on Home | App-bar chips: **Core** (running / stopped / unknown) · **Device** (host OS from the new `/api/runtime` `host` field) · **Pi readiness** (`N/M ready`, links to `#/features/not_ready_on_pi`) · **Microphone / Camera / Audio to the cloud** (the same states and colours as the Home privacy strip; "Unknown" while the Core is down) · **Privacy on/off** switch. |
| Usage / cost | Footer strip: ChatGPT plan + OpenAI API meters | The same strip, kept on every page, plus a **Today (estimate)** cost chip. Unknown states stay labelled "Unknown" / "not reported" / "estimate". On phones the strip is one swipeable row. |
| Mixed direction | — | Code, paths, model IDs, `.kv` values and charts stay LTR (`unicode-bidi: isolate`). English server details inside Hebrew pages use `unicode-bidi: plaintext`, so their punctuation reads correctly. Log and matrix attention bars are mirrored in RTL. |
| Theme | Text button "Theme: auto" | Icon button ◐ / ☀ / ☾ with a translated label; light and dark both checked. |

## Implementation (no framework, CSP unchanged)

- `dashboard_static/i18n.js` (new, loaded first): the locale, `tx(he, en)` / `txo(obj, key)` helpers and a source-string catalog (about 450 English→Hebrew entries, plus prefix and regex rules for dynamic text). The catalog is applied to new DOM nodes by a MutationObserver. It never touches user content (`dir="auto"`), code, logs, file text or chips. Mixed "English · עברית" strings keep only the chosen language. Text goes through `node.data` and `textContent` only, never `innerHTML`.
- `shell.js` / `shell.css` (new): drawer, search, status chips, language switch.
- Edited: `index.html` (grouped nav), `app.js` (theme icon, date locale, nav mapping), `home.js` (Home and Connections rewritten to single-language `tx`), `strip.js` (follows the dashboard locale; compact chips for the header), `guides.js` / `setup.js` (global locale; the Guides badge is attached to the static nav entry), `m2.js` / `m4.js` (localised titles and Features header), `usage.js` / `usage.css` (today's cost, follows the sidebar width), `runtime/__init__.py` (`host: {os, machine}`), `setup_wizard.py` (dashboard prerequisites include the new files).
- Plugins (`PLUGIN_MODULES` in `dashboard.py`) and their API routes are unchanged.

## Verification

- **Tests:** `cd core; $env:PYTHONPATH="$PWD\src"; .venv\Scripts\python -m pytest tests/test_dashboard.py tests/test_dashboard_lan.py tests/test_dashboard_phone.py tests/test_dashboard_wph.py tests/test_dashboard_p02.py tests/test_guides.py tests/test_setup_wizard.py tests/test_usage.py tests/test_feature_setup.py`: **123 passed**. The new `tests/test_dashboard_p02.py` checks: Hebrew-first shell, scripts served and free of `innerHTML`/`eval`, i18n loaded before app.js, every page still in the nav, nav labels separated by language, and the `host` field.
- **Real browser, interactive (Playwright, Chromium), against the actual dashboard server with demo data:** desktop 1440 px and phone 390 px, Hebrew and English. I exercised search (typing "מיקרו" ranks *Controls & runtime* first), the phone drawer (open, active item, Esc), the language switch, and the Features matrix on a phone. The first review pass found three issues, all fixed: the empty search popup was visible (`[hidden]` was overridden), the app bar wrapped to two rows, and the usage strip took three rows on phones. The only console error is a 404 for `/favicon.ico` (it existed before this change).
- **Reproducible headless captures (Edge):** `uv run python tools/v3_dashboard_screens.py` writes 16 PNGs to [`docs/reviews/swv3_p02/`](swv3_p02/): Home in he/en, light/dark, plus conversations, transcript, costs, features, runtime, usage, settings, logs, identity and devices. The narrow captures are 500 px, because headless Edge will not go narrower; the 390 px phone shots are the `pw_*.png` files from the Playwright session. These are automatic captures, not an owner hands-on check.
- The capture tool raises `dashboard_auth.RATE_PER_MIN` for its run only. Sixteen fresh browser loads (about 30 requests each) hit the 240/min loopback limit. Normal use stays well under it.

## Known gaps (truthful)

1. **Server-provided text is still English-only in Hebrew mode.** This covers feature `about` / evidence / blockers, issue titles, usage meter descriptions, settings field labels, backup notes, voice descriptions and the Google Cloud step texts. It is readable (plaintext bidi) but not translated. The fix belongs at the owning layer: add `_he` fields in `features.py`, `diagnose.py`, `usage.py`, `config_edit.py`, `backup.py` and the guides data. Follow-up proposed, not started.
2. Some strings rarely seen on less-used pages (context trace, new Skills review, autonomy proposals) may still show in English. The catalog is a single file, `i18n.js` `DICT`.
3. The Pi readiness chip counts `pi_ready` from `/api/features`: 1 of 40 is ready today, which is the true current state. The device chip shows the host that runs the dashboard. The Pi itself has not been tested (no device).
4. There is no owner hands-on review of the new look yet. Light and dark, desktop and phone were checked only by automated and agent screenshots.

## Questions for the owner

- **Group names and order** (Overview / Activity / Everyday / Assistant / Capabilities / Usage & cost / System & help): **USER DECISION REQUIRED**. These are a provisional recommendation.
- **Translate server texts into Hebrew** (gap 1) as a P0.2 follow-up before P0.5: recommended yes. **USER DECISION REQUIRED** on priority.
