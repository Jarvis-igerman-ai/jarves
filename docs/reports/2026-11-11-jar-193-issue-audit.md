# Already-fixed open issues audit (JAR-193)

Scope: 19 open issues flagged by a grep-only audit. Each was checked against main a9e478c by reading the code, finding the fixing PR and running the proving tests locally (948 passed, Linux). Green main evidence: merge-queue run https://github.com/Jarvis-igerman-ai/jarves/actions/runs/43011543730 (Python Core tests ubuntu 3/3 + windows 4/4) and main push run https://github.com/Jarvis-igerman-ai/jarves/actions/runs/43013679725 (reuses that proof). Every issue has a JAR-193 comment with the details.

## Closed with evidence
| Issue | Fixing PR / commit | Test |
|---|---|---|
| #898 | #929 / 2096c9d | `test_wake_prod.py::test_898_min_usable_counts_usable_clips_not_skipped_ones` |
| #904 | #905 / 28adace | `test_workflow_typed_answer.py::TypedAnswerTests::test_held_answer_stays_open_in_questions_view` |
| #767 | #772 / c5f4829 | `test_workflow_store.py::WorkflowStoreTests::test_concurrent_writers_never_hit_database_is_locked` |
| #853 | #859 / df7641f | `test_workflow_native.py::WorkflowNativeTests::test_actual_subagent_id_and_home_are_injected_without_parsing_transcript` |
| #852 | #859, #916 / 219654f, 1ab9f2a | `test_workflow_859_followups.py::GitNoteBehindTests` |
| #854 | #859 / 697e627 | `test_workflow_native.py::WorkflowNativeTests::test_recover_can_go_straight_to_review` |
| #603 | #601 | `test_v5_goal_plans.py::test_565_cut_never_splits_an_escape_and_is_marked` |
| #943 | #952 / 5cd01df | `test_workflow_859_followups.py::GitNoteTests::test_exhausted_budget_degrades_without_a_third_call` |
| #946 | #952, #969 | `test_agents_managed.py::test_codex_approval_request_goes_to_the_owner` |

## Kept open (missing items in each issue comment)
- #897: missing-file alert (owner decision), absolute paths in the schedule command, no area label.
- #786: `msg inbox` and `status` still take the write lock.
- #741, #796: remaining parts live in owner-local scripts; legacy console/status line retired 8 Nov.
- #824: no test parses a real ACL; folder ACL needs an owner decision.
- #879: Windows reproduce-or-rule-out not recorded.
- #469: no locked `auto_update` test; stable list undecided.
- #565: part 3 (200-char args cut, confirm steps hide full args) not fixed.
- #77: installed recheck and device items (UNVERIFIED).
- #510: needs-phone-retest (UNVERIFIED).
