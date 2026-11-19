# Evidence pack A: closure evidence for issues fixed on main (JAR-11)

Date: 16 Nov 2026. Tier L, report only. No issue was closed or changed by this report.
Base checked: `main` at `6ad26305`.

Method: for each issue, the fix commit (confirmed reachable from `origin/main`), the
regression test(s) that commit added, a local run of those tests on `6ad26305`, and a
green CI run of the full Python Core suite on that same SHA: merge-queue run
[43916259602](https://github.com/Jarvis-igerman-ai/jarves/actions/runs/43916259602)
(`jarves-checks`, all 3 ubuntu-24.04 and 4 windows-2025 shards `success`).

Local reproduce (from `core/`):
`uv run pytest -q tests/test_v5_goal_plans.py tests/test_wake_prod.py tests/test_v6_security_slice3.py tests/test_wakeword.py tests/test_voice_fixes_cv.py tests/test_greeting_retry.py -k "813 or 898 or 565 or calendar_read or passenger_setting or owner_threshold or false_wake or vq5 or wake_greeting"`.
Result seen: 16 passed, 91 deselected.

| Issue | State on GitHub (read 16 Nov) | Fix commit | Regression test(s) | Local | CI |
|---|---|---|---|---|---|
| #898 calibrate counts skipped clips | closed | `2096c9d2` | `test_wake_prod.py::test_898_min_usable_counts_usable_clips_not_skipped_ones` | pass | pass (43916259602) |
| #813 goal plan skip/runner writes overwrite a cancel | closed | `2096c9d2` (later hardened by `b611961`, report `docs/reports/2026-11-16-goal-plans-813-verify.md`) | `test_v5_goal_plans.py::test_813_skip_racing_a_cancel_never_overwrites_the_cancel`, `::test_813_runner_writes_do_not_overwrite_a_cancel` | pass | pass |
| #831 drive mode calendar reads ignore passenger setting | closed | `e866104a` | `test_v6_security_slice3.py::test_calendar_read_in_drive_mode_follows_the_passenger_setting` (parametrized), `::test_calendar_read_with_a_broken_passenger_setting_fails_closed`, `::test_attach_reads_the_saved_passenger_setting` | pass | pass |
| #603 plan popup escaping (#565 follow-up) | closed | `25117116` | `test_v5_goal_plans.py::test_565_one_show_text_escapes_format_and_control_characters_and_keeps_hebrew`, `::test_565_cut_never_splits_an_escape_and_is_marked`, `::test_565_title_why_and_args_are_escaped_in_every_popup_and_a_long_escaped_step_is_blocked` | pass | pass |
| #519 explicit wake_threshold lowers instant-accept | closed | `ede00087` | `test_wakeword.py::test_owner_threshold_starts_confirm_and_instant_accept_stays_at_wake_accept`, `::test_autonomy_false_wake_tuning_never_makes_false_wakes_easier`, `test_voice_fixes_cv.py::test_vq5_configured_wake_threshold_is_honoured` | pass | pass |
| #520 greeting language, listening-dot Tk thread, language hold streak | **open** | `ede00087` (partial) | `test_greeting_retry.py::test_wake_greeting_follows_ui_language_and_is_silent_in_drive_mode` | pass | pass |

## Gaps and UNVERIFIED

- #520 has three parts. The greeting language part has a test. The `en_streak`
  reset is in `voice/live.py` (`ede00087`) but that commit added no test naming it.
  The listening-dot Tk thread vs approval-popup Tcl init part is not addressed by
  `ede00087`; this report found no evidence for it. #520 is not ready to close on this pack.
- #898 and #519 change wake-word behaviour; real microphone / ear checks on a device are
  UNVERIFIED (unit tests with fakes only).
- #831 is drive-mode behaviour; an in-car check is UNVERIFIED.
- #603 popup rendering was checked by unit tests, not on a real desktop popup: UNVERIFIED.
- Installed-product acceptance for all six issues: UNVERIFIED.
