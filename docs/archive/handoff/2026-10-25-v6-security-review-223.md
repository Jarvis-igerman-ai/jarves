# V6 item 16: Opus security/privacy review and 6.0 bug table (#223) - 25 Oct 2026

Branches `v6-223` (docs) and `v6-223-fix` (PR #327, `no-automerge`).

- Done: review of the whole V6 surface on `jarves/main` c1bcb81d: `docs/reviews/V6_SECURITY_REVIEW_2026-10-25.md`. No P0 to P2.
  Five new P3 findings filed: #322 (R1, briefing failure text), #323 (R2, model relaxes passenger=present), #324 (R3, briefing deferred
  while driving), #325 (R4, LAN device can flip drive_mode), #326 (R5, briefing route has no shared limit; action 429 without Hebrew).
- 6.0 bug table: `docs/reviews/BUG_TABLE_V60_2026-10-25.md` (format of the 5.0 table; `release_gate.bug_table_open` lists only the
  P2 process rows #219, #223, #224).
- Code: PR #327 fixes #318 (phone-route Gate fallback allows reads only, via `drive_mode.fallback_allows`) and #322, with tests.
- Local prepush: all PASS except `perf` (#307, environment-dependent, CI green).
- Open questions for the owner (safe defaults in the issues): R2 apply `effective_mode` on the model path; R3 refuse the briefing
  without queueing while driving; R4 block `drive_mode` from paired dashboard devices.
