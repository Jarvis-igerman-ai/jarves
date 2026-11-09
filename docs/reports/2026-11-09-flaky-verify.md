# Flaky-test verification (JAR-56): #946 #943 #788 #770

Date: 2026-11-09. Code under test: main at `09eec58b` (the later `main` head `9571c1c7` changes no file these tests touch: UNVERIFIED by a rerun). Command per run: `PYTHONPATH=core/src python -m pytest <file> -k <test> -q -p no:cacheprovider`, local Windows, Python 3.12, no network.

| Issue | Test | Runs | Pass | Fail |
|---|---|---|---|---|
| #946 | `test_codex_approval_request_goes_to_the_owner` (test_agents_managed.py) | 34 | 34 | 0 |
| #943 | `test_exhausted_budget_degrades_without_a_third_call` | 29 | 29 | 0 |
| #788 | `test_delegated_tool_call_budget_exhausted_ends_session` (test_voice_faults.py) | 30 | 30 | 0 |
| #770 | `test_hidden_page_and_a_refused_wake_lock` (test_phone_hands_free_e2e.py) | 35 | 35 | 0 |

Two overlapping runner loops executed concurrently (each targeting 20 runs), so counts exceed 20. Zero failures, so no deflake and no quarantine change: `core/tests/quarantine.toml` has no entry for these four issues. Issues are not closed by this report; the owner/PM decides. CI evidence is the PR check run.
