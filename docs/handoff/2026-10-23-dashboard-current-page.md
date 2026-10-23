# JARVES knows which dashboard page is open ("what page is this?")

Follow-up to PR #105 (open a named page and explain it). Approved by the owner ("yes") as the proposed next step.

**Changed**
- `dashboard_static/app.js`: the router reports the page id (only the id, never content) to `the /api/overview poll`.
- `dashboard.py`: `note_ui_page` (via `/api/overview?page=`) stores `{page, at}` on the Ctx only for a known page id (unknown text is dropped); new read tool
  `dashboard_page` returns that page's purpose/controls and `seen_s_ago`; `DASHBOARD_NOT_OPEN` when no tab has reported.
- `app.py`: wires the Core's dashboard state into the skill. `guides/data/dashboard.toml`: say-line and sentence. Tests in
  `test_dashboard_pages.py`, routing row in `test_tool_descriptions.py`.

**Safety:** read-only tool; same explain-only note on sensitive pages; no page content leaves the browser.
**Not verified:** a real browser reporting the page (the CI real-browser job does not cover it) and live voice routing; the
page id is only as fresh as the last navigation (`seen_s_ago` tells the model how old it is).
**Decisions needed:** none.
