# SW-V3 P0.1 report — real-conversation bug fixes (#17)

Branch `worktree-agent-a00155ae2964a0288` (based on and re-merged with `swv2-integrate`). Full table:
[SWV3_P01_BUG_TABLE.md](SWV3_P01_BUG_TABLE.md). Regression tests: `core/tests/test_swv3_p01.py` (25 tests).

## Fixed (with a test that reproduces the failure)
- **B01 quit** — `quit_jarves` (orderly stop after the goodbye); hide / end / quit / dashboard separated.
- **B12 false action claims** — `LIVE_FALSE_ACTION_CLAIM`; a plain "תזכור ש..." is saved by the Core; otherwise a
  correction note. Reproduced in a silent GPT-Live run with the new prompt, so the Core check is needed.
- **B02 lost answer** — delivery watchdog (`LIVE_ANSWER_UNDELIVERED`) hands a completed but unsaid answer over once.
- **B08 end conversation** — `end_conversation`; no more "close the window" advice.
- **B03 approval ≠ AI timeout** — `skills.bounded_ai`; honest timeout replies; terminal confirm yes/y/כן.
- **B04 untrusted taint** — per route, cleared at the first user transcript delta.
- **B05 AI review state**, **B06 history backfill**, **B11 false session cost**, **B09 progress chatter**,
  **B07 requested face close is not a fault**, **B10 caption spacing**.

## Not fixed (reason)
- B16 camera still resolution (camera design decision, other owner), B17 web-search `sources: 0` (needs an API
  check before touching the Live delegation config), B18 stale issue rows in the user's store (close in dashboard).
- `test_setup_wizard.py::test_every_registered_feature_has_a_group_and_a_bundle_declaration` fails on
  `swv2-integrate` itself (`camera_preview` has no wizard group) — other agent's feature.

## Needs live verification
- B01 on the real desktop: the Core process exits and the microphone is released after "שט דאון".
- B02 the watchdog in a real recurrence; B12 the Core-save path in a real occurrence (offline verified).
- B13 / B14 / B15 prompt-only changes.

## Evidence
- Silent GPT-Live runs (virtual mic/speaker, `core/tools/silent_live.py` via a scratch wrapper): quit →
  `quit_jarves`; "תסגור את הסשן" → `end_conversation`, goodbye spoken, session ended; current-score lookup 5.1 s →
  lead-in + answer, no progress note; remember: run 1 reproduced the false claim (detector fired), run 2 delegated.
  About 9 short sessions, ~5 voice minutes: **about $0.35 total** (cap $2).
- `python -m pytest core/tests -q`: all pass except the pre-existing setup_wizard test above.
- Real store: `jarvis diagnose --import-log` backfilled R1 (backup of store.db kept locally, not committed).

## Integration hooks / files touched
`voice/live.py` (owner: me per lead), `skills/__init__.py`, `brain.py`, `chatgpt_brain.py`, `text_session.py`,
`runtime/__init__.py`, `link.py`, `diagnose.py`, `store.py`, `cli.py` (one line). Not touched: dashboard*.py,
scheduler.py, generated.py, guides, test_guides / test_m3_core / test_workspace_memory.
