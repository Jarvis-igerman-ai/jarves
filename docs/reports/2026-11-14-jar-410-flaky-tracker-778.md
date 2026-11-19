# JAR-410: flaky tests tracker #778, state on 14 Nov 2026

Read-only audit. No code change.

| Tracker item | State on GitHub / main |
|---|---|
| #707 #709 #770 #367 #422 #481 #488 #722 #890 | closed (most recently #367 and #422 via PR #1114, JAR-398) |
| #660 `test_helper_approvals.py::test_dangerous_command_needs_the_code_and_timeout_kills` | open, but **no quarantine entry**: `core/tests/quarantine.toml` on main is empty |

#660 already has its suspected cause handled on main: the fixture gives `RLIMIT_NPROC`
1024 headroom (a failed fork in `sh` exits with code 2, the symptom seen), and the assertion
prints code, message, output tail and isolation, so the next failure carries its cause.
It has had one sighting (run 40516866677, 27 Oct) and none since.

Blocker: the ticket needs "a test that fails before and passes after" for each issue. There
is no failing test to reproduce on main. Writing one would mean inventing a cause. Owner choice:
close #660 and #778 with this audit as evidence, or keep #660 open until it happens again.
