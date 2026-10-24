# Integration batch 1 (WhatsApp + voice), issue #17

Branch `integration/wa-voice-batch-1`, one pull request by the owner's decision (exception to "small PRs", slow merge
queue). Each branch merged with a normal merge commit, in this order, on top of origin/main 55fc7ac.

| # | Branch (tip) | Merge commit |
|---|---|---|
| 1 | fix/wa-chat-name-search (d05df94) | 7fcfb90 |
| 2 | feat/wa-easy-search (cefee99) | f2dfe37 |
| 3 | fix/wa-message-order (401a5d8) | 62beced |
| 4 | feat/wa-archive (eddafd0) | f1f30e4 |
| 5 | feat/ask-full (1f37a95) | b26a950 |
| 6 | fix/voice-behaviour (1f56eea) | 506913a |
| 7 | fix/issue-registry-noise (1d43b4d) | 829c644 |
| 8 | fix/voice-audio (cb6a04f) | c07b01a |
| 9 | fix/reliability (dc26ff4) | 7077ff6 |
| - | origin/main (d6ce2ec, newest at the end) | 85b3382 |

Integration fix commit: 49f1b89 (test only, see below).

## Conflicts and how they were resolved
1. wa-chat-name-search: `tests/test_whatsapp_surfaces.py` (both sides appended tests at the end of the file): kept both.
2. wa-easy-search: no conflicts.
3. wa-message-order, `whatsapp_bridge.py`: (a) tool list: kept `whatsapp_find_chat` and the `whatsapp_search` of #3,
   with the description now saying it searches message TEXT, not chat names, newest first, dated. (b) `BACKEND_RULE`:
   kept the order sentence ("Lists: chat/summary oldest first, inbox most recent chat first, search newest first")
   followed by the find_chat / synchronous / no "still searching" text.
4. wa-archive (the big one), locale catalogs resolved with `catalog_add.py --resolve`:
   - `connect.py`: kept the else-branch `READ_LATER` note (setup-enables-feature) and the `KEEP_NOTE` line after it.
   - `dashboard_static/whatsapp.js`: kept the archive cards and search UI; the inbox heading keeps "(most recent first)".
   - `guides/data/whatsapp_bridge.toml`: `links` has `whatsapp_archive` and `error_codes` keeps `WA_BAD_PATTERN`.
   - `whatsapp_admin.py`: imports of both `wa_namesearch` and `whatsapp_archive`; `ACTIONS` has `find`, `search`, `archive`;
     help text has both; `enable` prints the keep note after either answer; `status --json` has `archive` and
     `reading_switch_fix`.
   - `whatsapp_bridge.py`: both imports; `HE` has `WA_BAD_PATTERN`, `WA_ARCHIVE_OFF`, `WA_BAD_DATE`; one
     `whatsapp_search` tool (text only, newest first, dated, optional who/since/until).
   - Semantic reconcile of the order rule from #3: `search_archive` now sorts with `newest_first`, builds views with
     `message_view(m, names, now, True)` (date in `when`, plus `date`), returns `order: newest_first`, `source: archive`
     and the `ORDER_SEARCH` rule, and its text says "from newest to oldest". The CLI/dashboard `search()` in
     `whatsapp_admin.py` does the same (`order: newest_first`) and `jarvis whatsapp search` prints "(newest first)".
5. ask-full, voice-behaviour, issue-registry-noise, voice-audio: merged without textual conflicts.
6. reliability: `app.py` `stop()`: used reliability's `step("tasks", ...)` wrapper around voice-audio's bounded wait
   (`STOP_TASKS_TIMEOUT_S`, `CORE_STOP_TASKS_STUCK` warning kept); `CORE_STOPPED` keeps reliability's reason and
   failed_steps fields, and also calls `_restore_stop_signals()` before `stopped_evt.set()`.
   `skills/__init__.py`: kept both `_read_cache` (voice-behaviour) and `ask_cap_s` / `last_ask_outcome` (reliability).
7. origin/main (at the end): `app.py` `stop()`: kept the CORE_STOPPED block above and added main's `_auto_update.stop.set()`.

## Semantic fix (49f1b89)
`tests/test_conversation_quality.py::test_no_bare_ack_word_in_instructions_or_canned_phrases` forbids the words
"סגור/סוגר/בוצע" in the voice instructions, but fix/voice-behaviour deliberately lists the banned filler words in the
prompt (`phrases.banned_list_for_prompt()`). The test now removes that ban list before checking.

## Tests
See the final report for the full-suite result. Targeted sets run after each merge (whatsapp_*, wa_*, guides,
cli_parity, dashboard_locales, hebrew_texts, p_perf, plus each branch's own test file) were green.
No tool-description budget change was needed (test_p_perf passes without touching `perf_budgets.toml`).

## Not verified here
- Tests that need playwright (`test_dashboard_a11y.py`), godot or network are covered by CI only.
- `test_voice_presence.py::test_desktop_waits_for_actual_wake_mic_frames` (wake listener unavailable in the sandbox)
  and wake-related tests are reported as failing in this sandbox independent of this batch (fix/voice-audio reports
  the same on origin/main).
- Real WhatsApp bridge, real microphone/speaker and Windows shutdown behaviour.
