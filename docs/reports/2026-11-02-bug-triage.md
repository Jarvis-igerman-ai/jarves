# Bug triage, open P0-P2 issues (2 Nov 2026)

Task V5-BUG-TRIAGE, base `jarves/main` ca7a23fc. Method: `gh issue list` (open, P0-P2 or showstopper) matched against `git log` of main (last 400 commits) and `gh pr list` titles. Nothing was closed. "Fix PR open" is NOT fixed on main. Merged code is not installed-product verification; each candidate still needs the evidence listed before closing. Titles only were matched, so "no fix found" means none seen by title, not that none exists.

No open P0 found.

## A. Fix merged on main (candidates for closure, needs evidence check)
| Issue | Evidence | Needed before close |
|---|---|---|
| #788 flaky voice_faults budget test | commit 8c26ce82 "make live budget-exhausted session test event-driven" | 2 green main CI runs of the test |
| #741 console shows zeros when session missing | 4b455cf6, 64ad5cec (session-ended notice) | UI check on installed build |
| #732 workflow console UX redesign | 4b455cf6, 46ea4fb7 Today screen | owner look; part only (shell redesign is PR #773) |
| #722 silent-turn flake | 5a736145 guards stale cache, quarantined | un-quarantine after green runs |
| #660 helper pipe flake | PR #752, 6eaaa98d | green CI history |
| #422, #367 flakes | 6300cd89 hardened tests | green CI history (#422 also PR #804 open) |
| #544, #605 release secrets / rows hash | PR #646 merged (Tier R) | keep `known-issue-4.5` until sign-off review |
| #261 installer recovery | 41ce6528, 97bd902d | needs real schema-bump install test |

## B. Fix PR open (not on main yet)
| Issue | PR |
|---|---|
| #796 status line/verdict ignores native workers | #798, #807 (duplicates: merge one, close other) |
| #763 NO_CORE consumes start budget | #808 |
| #754 supervisor offline after restart race | #800 |
| #750 dashboard owner-answer route (Tier R) | #779 |
| #797 answer by typing in Claude window | #799 |
| #776 usage throttle, #760 question cap, #758/#761 verdict+report | #782, #781, #780 |
| #385 timed-out consequential step | #812 (Tier R-ish: plan state) |
| #709 mic menu flake, #481/#488 flakes (via #778), #708 | #792, #795, #801 |
| #711 say default-output, #710 slow-CLI probe | #804, #803 |
| #620 planned hotfix restart not a crash | #794 |
| #160 CI reliability E3 report | #817 |
| #733 task order (working first) | likely covered by #773 UI redesign, unconfirmed |

## C. Real, no fix seen, next candidates (priority order)
1. #786 P1 SubagentStart hook "database is locked" (workers cannot register). Directly blocks the factory. Related #767 (busy_timeout, BEGIN IMMEDIATE), P2, probably the same root cause: fix together.
2. #738 P1 agents must not answer owner questions (partly addressed by #779 unlock code; confirm scope).
3. #789 P1 showstopper dashboard unreliable (orphan web servers, slow endpoints).
4. #784 P2 autopilot allow list mismatch (popups, rate limit).
5. #813 P2 goal plans: skip read-then-write and runner overwriting a cancel (sibling of #385).
6. #717 P2 timing refresh reads merge-group artifacts.
7. #466 P2 verify bridge exe after install (needs installed-product check).
8. #343 P2 installer did not restart Core after silent install: no merged fix found by title in the recent window; the manager said #383 fixed it, so verify PR #383 and a silent install on the PC, then close with that evidence.
9. #357 P1 phone pairing too hard; #811 and #810 are open related PRs; needs phone retest.

## D. Showstopper / flake status
- Open showstopper flakes: #788 (fix merged, watch), #722 (quarantined, fix merged), #709 (fix PR #792), #660 (fix merged). Tracker #778 (consolidates #707 #709 #770 #367 #422 #481 #488) has fix PR #795.
- Showstopper non-flake: #789 (no fix seen).
- Rule: quarantined flakes must be fixed within 2 days; #722, #709 are over the limit if the fix PRs stay unmerged.

## E. Not bugs / out of triage scope
Open P1/P2 labelled `enhancement` (V7 tracks #390-#420, #365, #364, #336, #331, #762, #759, #757) are features, not defects. Phone #509, #510 are `known-issue-4.5`, `needs-phone-retest`: need a real phone check, not closable here. #179, #544, #543, #605 carry `known-issue-4.5` (owner-accepted). #543 (sign-off sheet editable after signing): no fix seen. #159, #160 are plans/reports.

## Review nits
None recorded.
