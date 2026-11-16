# Tasks from messages, one card at a time: checked on main (JAR-323)

Checked 16 Nov 2026 against `jarves/main` 8b056e0. Requirement R-032, issue #364.

## Story

As the owner I can turn my messages into tasks one by one and approve each.

## Finding

The feature is already on main (S3 #446, S4 #447, hardening #485). It is beta
and OFF by default (`jarvis features on task_triage`, needs Any.do). No code
change was needed for this ticket.

| #364 asks for | Where on main | Test |
|---|---|---|
| Candidate tasks (title, due, person, source), marked "not confirmed" | `core/src/jarves/triage.py` `tasks_from_messages` | `test_triage.py::test_extraction_shows_cards_and_tells_the_model_counts_only`, `test_past_or_bad_due_is_dropped` |
| One card at a time ("Card 1 of 3"), Add / Edit / Skip (owner decision 25 Oct: no add-all) | `dashboard_static/chatview.js` `cardsView` | `test_chat_markdown_browser.py::test_cards_add_edit_skip_with_keyboard_and_one_send` (headless browser) |
| Terminal: numbered list, type `1,3` or `all` | `ui_blocks.numbered`, `parse_numbers` | `test_triage.py::test_cards_numbered_fallback_and_numbers` |
| Chosen tasks go to Any.do only after ONE approval with the exact final titles | `anydo.py` `_ui_create_tasks` (existing #337 path) | `test_triage.py::test_batch_with_edit_and_skip_goes_through_one_approval`, `test_skip_everything_records_skips_and_writes_nothing` |
| Dedupe against Any.do and inside the batch | `triage.similar` | `test_dedupe_against_anydo_and_inside_the_batch` |
| Skipped tasks not offered again (90 days, restorable) | `SkippedStore`, `jarvis tasks skipped list/restore` | `test_skipped_tasks_are_not_offered_again_until_restored`, `test_cli_skipped_list_and_restore` |
| Hebrew titles, RTL | card renderer | `test_cards_skip_all_refusal_and_rtl`, `test_normalize_handles_niqqud_and_punctuation` |
| WhatsApp, mail and notes as sources | `Cand.source` | `test_source_mail_and_note_reach_the_card`, `test_unknown_source_is_refused_not_shown` |
| Message text never drives actions; forged clicks refused | `ui_blocks.BlockStore.take` | `test_injection_strings_are_just_card_text`, `test_forged_ids_overlap_and_replay_are_refused`, `test_forged_edit_over_http_is_refused` |

"Approve each" works as: the owner decides Add / Edit / Skip on every card, then
ONE approval lists the exact chosen titles. #364 allows this batch approval.

## Proof

From `core/`: `python -m pytest -q -o pythonpath=src tests/test_triage.py tests/test_ui_blocks.py tests/test_chat_markdown_browser.py`
gave 114 passed (the browser tests ran, not skipped).

## Not covered (UNVERIFIED)

- Never run with a real WhatsApp, mail or Any.do account, and not on the
  installed product. The guide already says so (`platform` = `implemented_unverified`).
- Voice: the design says voice speaks one short line ("look at the screen"),
  never the titles. Only the tool result wording supports this; no voice test.
- Not run on a Raspberry Pi.

## Next

An owner test on the PC: switch the feature on, read real messages, go through
the cards, approve, and check the tasks in Any.do. Then #364 can close.
