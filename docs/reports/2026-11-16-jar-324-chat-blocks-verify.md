# Checklists, cards and tables in the chat: checked on main (JAR-324)

Checked 16 Nov 2026 against `jarves/main` 8b056e0. Requirement R36, issue #365.

## Story

As the owner I can see checklists, cards and tables rendered in the chat.

## Finding

Already on main (S1 #444 / PR #450, S2 #445 / PR #478, S3 #446 / PR #484,
S4 #447 / PR #647, hardening #485). No code change was needed for this ticket.
Asking for a list, table or options works without a switch; the assistant
picking the format by itself is the beta switch `smart_format`, OFF by default.

| #365 asks for | Where on main | Test |
|---|---|---|
| Fixed, versioned block schema, validated on the server, size limits | `core/src/jarves/ui_blocks.py` `validate_block(s)`, `KINDS` | `test_ui_blocks.py::test_bad_blocks_rejected`, `test_message_limits_drop_but_keep_good`, `test_oversize_total_rejected`, `test_table_and_choices_reject_matrix` |
| Checklist with real tick boxes (mouse and Space key) | `dashboard_static/chatview.js` `blockView` | `test_chat_markdown_browser.py::test_ui_blocks_render_as_text_and_work_with_keyboard` (headless browser) |
| Cards one by one: Add / Edit / Skip | `chatview.js` `cardsView` (cards come only from the app, `task_triage`) | `test_cards_add_edit_skip_with_keyboard_and_one_send`, `test_cards_skip_all_refusal_and_rtl` |
| Table (read-only) and choice buttons | `ui_blocks.py` table / choices, `chatview.js` | `test_table_gets_server_ids_pads_rows_and_cleans`, `test_choices_ids_and_numbered`, `test_table_and_choices_render_as_text_and_choice_sends_a_message` |
| No HTML or JS from the model; text nodes only | renderer | `test_renderer_has_no_html_injection_apis`, the `<i onclick>` case in the browser test, `test_clean_strips_zero_width_and_direction_marks` |
| Terminal: numbered list, type 1,3 or all | `ui_blocks.numbered`, `parse_numbers` | `test_numbered_fallback_is_plain`, `test_choices_ids_and_numbered` |
| Owner's selection returns as a user action; writes go through the normal approval | `BlockStore.take`, `run_action` | `test_checklist_send_posts_ids_only_and_locks`, `test_checklist_send_refusal_reopens_and_names_the_reason` |
| Message text never creates acting controls | context taint | `test_context_taint_outlives_the_untrusted_turn_then_decays` |
| Not sent to phones | web chat filter | `test_webchat_hides_blocks_from_phones` |
| Assistant picks the format (beta, OFF) | `ui_format` tool, `smart_format` feature | `test_smart_format_is_a_beta_feature_off_by_default_that_owns_only_ui_format`, `test_real_core_offers_ui_format_only_after_the_switch` |
| Hebrew / RTL, 44 px targets | `dir=auto` labels, `chatview.css` (`min-block-size: 44px`) | `test_cards_skip_all_refusal_and_rtl`, `dir=auto` check in the browser test |
| Guide EN+HE and CLI parity | `guides/data/smart_chat.toml` | `test_guides.py` |

## Proof

From `core/`: `python -m pytest -q -o pythonpath=src tests/test_ui_blocks.py tests/test_chat_markdown_browser.py tests/test_guides.py -rs`
gave 100 passed, none skipped (the browser tests ran).

## Not covered (UNVERIFIED)

- The "short form" block from #365 is not built (code note in `ui_blocks.py`:
  cards have an inline Edit instead). #365 stays open for it or for an owner
  decision to drop it.
- Voice: the tool result tells the model the block was shown; there is no test
  that voice speaks only a short summary.
- Not run on the installed product on the owner PC, nor on a Raspberry Pi.

## Next

An owner test on the PC dashboard: ask "Show me a short checklist", "compare
these in a table", tick with mouse and Space, and check Hebrew text. Then decide
on the short form so #365 can close.
