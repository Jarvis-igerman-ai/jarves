# JAR-147 audit: hardcoded English units/dates in the dashboard

Fixed (5 call sites, 3 files, tested by `core/tests/test_dashboard_unit_labels.py`): `browser.js` expiry timer, `modellab.js` latency, `usage.js` backup size.

Not fixed (reported, UNVERIFIED in a browser):
- `app.js:195` "live — updates every 2 s" and `app.js:292` "seen in N conversation(s)" are English-only plural/unit strings built with templates.
- `pages.js:262` "N matching line(s)" same pattern.
- `m4.js:229` "Next 7 days" / "Nothing in the next 7 days." are plain English with no tx().
- `chatview.js:231-233` uses `toLocale*(undefined)` (browser locale), not `JL.locale()`; `updates.js:126` uses `[]`.
- `projects.js:25`, `pillars.js:219` use tx() but need the Hebrew abbreviation checked for RTL number order.
