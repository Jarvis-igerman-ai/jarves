# Conversational dashboard navigation (feature request from JARVES on the owner's behalf)

**Asked:** the user says "open <page>" and JARVES opens that dashboard page, explains its controls and guides the user;
sensitive actions still need the user's own approval.

**Changed**
- `core/src/jarves/dashboard_pages.py` (new): directory of every sidebar page (EN/HE title, purpose, controls, a
  `sensitive` flag) and `resolve()` (id, `#/id`, English/Hebrew title, a few synonyms such as "spending").
- `core/src/jarves/dashboard.py`: `open_dashboard` takes `page`; an unknown page opens nothing
  (`DASHBOARD_PAGE_UNKNOWN`); the result carries the page's controls for the model to explain. Still no access key in the result.
- `core/src/jarves/brain.py`: rule to explain and walk through, never press anything for the user.
- `guides/data/dashboard.toml`: new step and say-line (the CLI path is the existing `jarvis dashboard --page "#/costs"`).
- `core/tests/test_dashboard_pages.py` (new).

**Safety:** the tool only opens a page and returns text. It cannot click; every change is the user's own click and the
consequential pages (approvals, features, settings, connections, WhatsApp, backup, updates...) keep their own confirmation.

**Verified:** `python -m pytest core/tests/test_dashboard_pages.py test_dashboard_wph.py test_tool_descriptions.py test_guides.py test_cli_parity.py` passed.
**Not verified:** live voice routing ("needs live re-run" per docs/reviews/SWV4_PERF.md); the controls text was written from the
page purposes, not from a click-through of each page; `agent/<id>` detail pages are not openable by name.

**Decisions needed:** none. Follow-up (not started): a per-page "what is on screen now" read, if wanted.
