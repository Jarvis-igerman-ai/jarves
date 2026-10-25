# V6 item 8: phone and car capability rows (#215), 25 Oct 2026

- Done: optional extra targets `phone` and `car` in `capabilities.py` (`declare_extra`, `EXTRA_TARGETS`); every other feature keeps its two columns. Rows for `drive_mode`, `journey`, `drive_briefing`, `audio_route`, `mobile_access` in `V6_ROWS`, each naming its own real test files (checked by `tests/test_v6_capability_rows.py`). Hebrew in `hebrew_parts/2026-10-25-v6-capability-rows.json`.
- Honest rules (in `problems()`): a phone/car row on an untried device is `implemented_unverified` with a blocker that starts with NOT-RUN, or `planned`; `verified_on_device` needs evidence. Nothing is verified: no real phone or parked-car session exists (owner-gated, V6 plan G11).
- Not done: the dashboard Features table still shows only Windows and Pi columns (the phone/car rows are in the API `targets`); a column is a UI follow-up. No guide: this is a matrix change, not a feature.
- Questions for the user: none.
