# Dashboard issues triage (2026-10-26)

Source: installed JARVES 4.0.1.760 (`issues`, `health`), read-only. Codes, areas, severities, counts and dates only.
Result: 26 open + 5 information. Health 0/100, 28 findings. Fixed items: PR #371 (counter/expiry), #387 (queued) do not change these entries.

Classes: BUG = real bug, ENV = environment, OWN = owner action, STALE = old, DUP = duplicate.

| Item (code / area / sev / count / seen) | Class | Root cause (code) | Status |
|---|---|---|---|
| AUTONOMY_SYNC_FAILED ProgrammingError store.rows (core P2 x38, 2026-10-25) | BUG | operations.py `_after_tick` -> store.issues; sqlite handle closed/misused while Core runs | issue #430 |
| New error code AUTONOMY_SYNC_FAILED (health P2 x38) | DUP of above | same | #430 |
| CHAT_TURN_FAILED TypeError brain.respond (core P2 x3, 2026-10-23) | BUG (maybe STALE) | TypeError in brain.respond | issue #431 |
| CHAT_TURN_FAILED x3 (health P2) + New error code CHAT_TURN_FAILED x3 | DUP | same | #431 |
| claimed an action no tool performed (P1 x2), He claimed an action (P2 x1), AI review: note created after delete (P1 x1) | BUG or false positive | claims detector / delete-note path | issue #432 |
| LIVE_EXTRA_REJECTED (health P2 x19, 5 runs) | ENV (optional extras, handled in voice/live.py:1227) | warn only, session stays healthy | rule change, #433 |
| WAKE_MIC_QUIET (health P2 x38, 6 runs) | ENV/OWN (mic level hint, app.py:1261) | quiet mic | rule change, #433 |
| False wakes x40 of 47 / too many false wakes x33 (P2) | ENV/tuning | wake threshold; autonomy.py proposes a higher threshold | #433 (info until proposal ignored) |
| Tool whatsapp_chat failed 5 of 9, mostly WA_UNKNOWN_CONTACT (P1 x5) + Conversations tool_fail x3 (P1) | ENV (user-input outcome) | unknown contact is not a tool failure | rule change, #433 |
| Tool music_play failed 3 of 7, MUSIC_NOT_CONFIRMED (P2 x3) | OWN (Pear player not running / not confirming) | skills/music.py:790-920 | #433 note; owner: start Pear |
| JARVES stopped without a clean shutdown 4x (P1) + began clean stop, not finished 1x (P2) | ENV (updates/restarts of this dev PC, version changed 4.0.1.724 -> 760) | installer restarts / forced close | #433 (info when an update/restart preceded it); needs a log check by an agent |
| Nightly canary voice_stack failed twice (P1 x2) | BUG? (3 canaries blocked) | canary environment (voice) | new, check next canary run |
| Conversations: dead air 9 s (P1 x3), stalled 21 s (P1 x2), filler "#1" said 4x (P1 x1) | BUG (latency/quality; tool_ms 2.16x worse 521 -> 1125) | tool latency regression | covered by performance lane, new issue if it persists |
| Speed: core_start_ms, openai_roundtrip_ms, dashboard_first_load_ms (P2/P3 x1) | ENV (single run) | existing issue #258 | #433 rule: info until 3 runs |
| Daily limits selftest (P3 x4), test idea from owner (P3 x1) | OWN | limits set by owner | none |
| Information items (5): Pear not running, no camera, Godot face not connected (x2), new version seen | ENV, already info | issue_text.py | OK |

Counts: BUG 4 groups (7 items), ENV ~9, OWN 3, DUP 3, STALE 0 proven.

Top fixes: 1) #430 store.rows ProgrammingError (also clears 2 items), 2) #433 rule changes so expected states are info (clears about 8), 3) #432 claims detector review.
