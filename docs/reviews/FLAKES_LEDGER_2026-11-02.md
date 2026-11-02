# Flakes ledger, 2 Nov 2026 (tracker #778)

Base: `jarves/main` at the time of writing. Source of truth for quarantine is `core/tests/quarantine.toml`
(8 entries). Status of PRs and issues read from GitHub on 2 Nov 2026. Nothing was lifted by this change.

## Quarantined tests

| Test id | Showstopper issue | Fix PR | Can the entry be lifted now? |
|---|---|---|---|
| test_core.py::test_logs_have_correlation_ids_and_no_private_text | #548 (closed; dups #708, #481 open) | Open: #801 (de-flake, unquarantine), #846 (private-text), #795 (#481/#488) | No: fix PRs unmerged |
| test_dashboard_mic_stop_browser.py::test_mic_menu_narrow_width_stays_on_screen_and_hebrew | #549 (closed; #709, #488 open) | Open: #792, #829, #849, #795 (#788 itself merged in #791 for a different test) | No: fix PRs unmerged |
| test_perf_probes.py::test_slow_cli_probe_never_blocks_features_list_and_its_result_lands | #550 (closed; dup #710 open) | Open: #803 (earlier #196 and #137 merged but did not end the flake) | No: #803 unmerged |
| test_say_engine.py::test_default_output_is_one_line_without_espeak_on_windows | #551 (closed; #711, #422 open) | Open: #804 (earlier #475 merged, flake returned) | No: #804 unmerged |
| test_runtime.py::test_hide_terminates_a_window_that_does_not_close | #556 (closed) | Open: #820 (unquarantine) | No: #820 unmerged |
| test_helper_approvals.py::test_dangerous_command_needs_the_code_and_timeout_kills | #660 (open) | Merged: #752 (process-limit headroom counts threads). Open: #827 (removes entry) | Not by this PR: root fix is merged, but entry removal and run evidence belong to #827; lift there |
| test_web_chat.py::test_silent_turns_never_speak_ring_or_light_the_face | #722 (open) | Merged: #723 (cache hardening). Open: #743, #827 (read under write lock, unquarantine) | No: #743/#827 unmerged |
| test_phone_hands_free_e2e.py::test_hidden_page_and_a_refused_wake_lock | #770 (closed) | Open: #827 (says test already tolerant) | No: #827 unmerged |

Result: 0 entries liftable now. 1 (#660) has its root-cause fix merged and awaits #827 for the lift.

## Findings

1. Six showstopper issues (#548 #549 #550 #551 #556 #770) are CLOSED while their quarantine entry still exists and the fix PR is
   unmerged. This breaks the rule "every entry has an open showstopper issue". Proposal for the owner or manager: reopen them,
   or point each entry at the open duplicate (#708, #709, #710, #711, #556 via PR #820, #778). flake_scan may reopen them anyway.
2. Every current entry has at least one fix PR; no entry lacks a fix PR.
3. Flakes without a quarantine entry, tracked by an open issue:
   - #367 (Windows real-browser job, test_mobile_phone_e2e "Plan paused", test_canary dashboard_load 50 s): NO fix PR, no showstopper
     label confirmed. Needs triage.
   - #707 closed (canary journey), fixed by merged #476.
   - #866 test_diag_monitor::test_sampler_and_overhead_budget (real thread_time, 15.6 ms Windows tick): fix PR #868 open. There
     is no quarantine entry for it on main. The brief mentioned a quarantine entry in #849; #849 is actually the mic/stop de-flake
     for #488, so it adds none. If an entry is wanted, nobody has opened that PR.
   - #422 and #481 and #488 duplicates covered by #804, #846/#795/#801, #849/#795.
4. Duplicate issue clusters that #778 should absorb once fixes merge: #548/#708/#481, #549/#709/#488, #550/#710, #551/#711/#422.
5. PR #792, #829 and #849 all target the mic-menu test: three competing fixes, merge one and close the rest.

## #778 checklist state

| Item | State |
|---|---|
| #707 | issue closed, fixed (#476) |
| #709 | open, PRs #792 #829 |
| #770 | issue closed, PR #827 open |
| #367 | open, no fix PR |
| #422 | open, PR #804 |
| #481 | open, PRs #795 #846 |
| #488 | open, PRs #795 #849 |
| #660 | open, #752 merged, #827 open |
| #722 | open, #723 merged, #743 #827 open |
