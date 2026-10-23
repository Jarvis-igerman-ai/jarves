# Dashboard Settings page: save bar and technical names — 23 Oct 2026

Follow-up from the UX review of the navigation tree (see 2026-10-23-dashboard-home-followups.md). Python dashboard, static UI only, no API change.

## What changed (`pages.js` Settings page, `tree.css`, Hebrew catalog)
- Save changes / Undo last save are a bar that stays at the bottom of the window, with an "N unsaved change(s)" count. They used to sit in the page header, out of view on a long page.
- Raw config key names (e.g. `voice.backend_deadline_s`) are hidden; a "Show technical names" checkbox shows them (choice remembered in this browser only).
- The two intro cards (Setup and tour, Switches) are one short line of links, so the real settings start higher.
- Fixed: after a save, the old changed values stayed in memory and were sent again on the next save; they are cleared now.
- Disabled Save/Undo buttons are visibly disabled (contrast), not pale.

## Verified
- `python core/tools/prepush.py`: all eight checks passed. Dashboard, locale, guides and CLI-parity tests pass locally. A screenshot of the Settings page (light, English) was checked by eye.

## NOT verified
- Hebrew/RTL and dark mode on a real screen; the browser accessibility tests run only in CI (the sandbox cannot launch them).

## Still open
- Merge the three overlapping health pages (Health, Health & speed, Self-check) into one tabbed page (the biggest change; owner wanted to see the beta first).
