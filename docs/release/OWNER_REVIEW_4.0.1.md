# Owner review page for 4.0.1 (about 15 minutes)

Evidence: `docs/release/EVIDENCE_4.0.1.md`. Agents did the machine part; these are the only things left for you.

## Look at (3 minutes)
1. The row table in EVIDENCE_4.0.1.md: every row is pass or ci-proven except the owner-needed ones below.
2. Red items: open P0-P2 issues (#503, #515, #510, #509, #179) and the failed last update attempt (UPDATE_NOT_APPLIED).
3. The known issues list: that is what the release notes will say.

## Do (about 1 minute each, in this order)
1. S1: `jarvis update --channel beta`, then `jarvis doctor` (no red line).
2. S2: say "היי ג'רוויס, מה השעה?" (wakes, answers in Hebrew).
3. S3: interrupt an answer (stops within about a second, listens).
4. S4: ask for a medium-risk action by voice: popup on top, say "כן", approved once.
5. S5: `jarvis update --rollback`, check your data, then `jarvis update` forward again.
6. Phone: open the paired phone and see that it loads (known issues #509, #510).

## Sign-off commands (only you run these)

    jarvis release sheet --version 4.0.1
    jarvis release signoff --version 4.0.1 --row S1 --result pass
    jarvis release signoff --version 4.0.1 --row S2 --result pass
    jarvis release signoff --version 4.0.1 --row S3 --result pass
    jarvis release signoff --version 4.0.1 --row S4 --result pass
    jarvis release signoff --version 4.0.1 --row S5 --result pass
    jarvis release signoff --version 4.0.1 --finish

Use `--result fail --note "..."` for a failure. `release sheet` and `release signoff` come with the unmerged branch `simple-rel-code`; until it merges use the dashboard page Release sign-off. Nothing was signed or tagged by an agent.
