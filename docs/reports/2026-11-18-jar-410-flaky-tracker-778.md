# JAR-410: flaky tests tracker #778, state on 18 Nov 2026

Read-only re-audit on main `fe6d307`. No code change. Follows the 14 Nov audit
(`2026-11-14-jar-410-flaky-tracker-778.md`), which left only #660 open.

| Tracker item | State on GitHub / main |
|---|---|
| #707 #709 #770 #367 #422 #481 #488 #722 #890 | closed (unchanged since 14 Nov) |
| #660 helper pipe exit 2 | closed 18 Nov by PR #1258 (JAR-517: NPROC headroom now counts the real uid; its own test) |
| #866 diag monitor clock, #874 PR-card browser goto | closed |
| `core/tests/quarantine.toml` | header only, no entries |

Unquarantined sightings in the tracker comments, checked against main:

| Sighting | Already handled on main by |
|---|---|
| `DiscussHTTP.test_rate_limit_one_launch_per_ten_seconds` (200 != 429) | uses an injected clock (`self.now[0] += 10.5`), not wall time |
| `test_mobile_access.py` double-tap `IndexError first[0][0]` | first request socket timeout raised to 60 s (#911 comment in the test) |
| `test_selfcheck.py` ERROR record after the check thread | asyncio task-destroyed records from earlier tests filtered (commit 0639a6f) |

Blocker: the ticket asks for a test that fails before and passes after per issue. Every listed
issue is already fixed on main with its own test, so no new failing test exists to write;
inventing one would mean inventing a defect. Proposed owner action: close #778 with this
report and PR #1258 as evidence.
