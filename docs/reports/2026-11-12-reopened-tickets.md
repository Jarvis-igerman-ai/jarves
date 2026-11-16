Root cause of the five reopened tickets JAR-51/188/191/217/220 (JAR-268). Factory 2 quality flow. Status: current.

## Per ticket

| Ticket | Closing PR (merge) | Fix commit | Proof at close | Check that should have caught it | State on main 12 Nov |
|---|---|---|---|---|---|
| JAR-51 nightly ffmpeg | #1020 (e32d238, 11 Nov 10:09 UTC) | 234beab, 3e9dd77, b9229e7 | PR body: "UNVERIFIED until a nightly runs" | `jarves-nightly` run on main. The last nightly (run 42992721733, 11 Nov 09:09) is **before** the merge and red; no nightly has run on the fix yet | fix present, nightly result UNVERIFIED |
| JAR-188 Android reminders | #1055 (5280732) | 7a34cae (test), 85e2dfd, 0f9fd5d | CI red→green on the JVM test; Gradle not run locally; real phone UNVERIFIED | owner phone sheet (device check) | fix present, device UNVERIFIED |
| JAR-191 phone fit test | #1058 (6d7a90a) | 7505d67 | 20/20 local Linux; Windows shard UNVERIFIED | 20 clean Windows merge-queue runs, then lift quarantine | test still in `quarantine.toml`, showstopper #1049 open |
| JAR-217 projectmgr flake | #1070 (350fb28) | d6b6c60, b534b5d | 20/20 + 240/240 Linux; Windows shard UNVERIFIED | Windows merge-queue shard | quarantine lifted, showstopper #1051 still open |
| JAR-220 web search tests | #1075 (298274d) | 1892989, 6d57dad, f2e07da | 6 passed locally; CI UNVERIFIED in the PR | PR CI on the head SHA | tests pass |

The reopen reasons are recorded in the ticket system, which this session cannot read: **UNVERIFIED**. The table uses
only GitHub and git evidence.

## Common cause

Not a shared code regression: each fix is still on main and its tests pass (proof below). The common cause is
**ticket closed before verification**: every ticket was closed at PR merge while its own proof still listed the
named check as UNVERIFIED (a nightly that had not run, a Windows shard, a phone, a quarantine that stays until 20
clean runs). Merge and green PR CI were treated as done, against the operating model rule that merge ≠ verified.
Two tickets also left their GitHub showstopper issue inconsistent with the ticket (#1049, #1051 open).

## Regression guard

`core/tests/test_reopened_fixes.py` pins each fix at the owning file. Each test fails on the revision before the
fix (checked by restoring the pre-fix files: 5 failed, each with its own message) and passes on main.

Process follow-up (not done here, outside this ticket's scope): close a ticket only when the check its proof marks
UNVERIFIED has a result on main (nightly run, Windows shard, device sheet), and close/link the showstopper issue
in the same step.

## Proof (main b7296fa, Linux)

- `uv run --frozen pytest tests/test_web_search.py tests/test_web_search_sources.py` and the two projectmgr tests: 13 passed.
- `test_mobile_browser_e2e.py -k fits` (Playwright 1.63, Inter font rejected, `.lan` hosts outside the proxy): 2 passed.
- JAR-188 Gradle tests: not runnable here (no Android SDK); CI job `Android companion` covers them. UNVERIFIED locally.
- `escaped_24h`: a factory metric not visible from this repo. UNVERIFIED.
