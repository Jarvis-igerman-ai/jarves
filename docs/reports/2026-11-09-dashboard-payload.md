# Dashboard payload (JAR-135), 2026-11-09

Measured offline with `core/tools/dashboard_payload.py` (files an entry page references; gzip level 6 = what dashboard.py sends). Baseline = after: no cut applied.

| page | first-load requests | raw bytes | gzip bytes |
|---|---|---|---|
| dashboard (/) | 49 | 977363 | 300814 |
| pair (/pair) | 4 | 26136 | 7802 |
| phone PWA (/m/) | 16 | 216581 | 69141 |

## Findings
- Dead assets: none. camera.js, modellab.js (pages.d lazy scripts) and usage.css (loaded by usage.js) are lazy, not dead. `test_no_dead_assets` guards this.
- Server already sends ETag/304, `no-cache` and gzip (dashboard.py `_static`), so repeat loads cost ~0 bytes.
- Biggest first-load items (raw / gzip): he.js 149 KB / 46 KB, en.js 122 KB / 28 KB, pages.js 48 KB, app.js 45 KB, m3.js 43 KB. Locales are ~28% of the gzip total.

## Proposals (not applied: behavior change, need UI verification)
1. Load only the active locale (en or he) and fetch the other on language switch: about -28 KB (en) or -46 KB (he) gzip on first load.
2. Lazy-load rarely used page scripts (whatsapp, projects, browser, signoff_steps, selfspeed, selfcheck, wizard): about -50 KB gzip, fewer than 49 requests.
3. Bundle + minify JS/CSS in the release build: usually -30-40% raw; touches packaging (Tier R), owner decision.
