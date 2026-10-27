# Owner review page for 4.0.1 (about 15 minutes)

Evidence: `docs/release/EVIDENCE_4.0.1.md` (refreshed 27 Oct 2026 on main fb926672; gate slice 3 #537, tag-commit fix #541, release status #514 merged). Agents did the machine part; these are the only things left for you. Nothing was signed or tagged by an agent. Refs #505.

## Look at (3 minutes)
1. Gate live dry run (27 Oct, main fb926672 is green): the only owner items are S1-S5 and the signature. Legacy bug tables and the checklist are printed for information and no longer gate (#537).
2. Open P0-P2 the gate still counts: flaky-test showstoppers #546-#551, #555, #556 (P1, label showstopper; agents fix them), release hardening #543 and #544 (P2, from the review of #541), and #592 (P1, release.yml dry run fails at checkout before the tag exists; the real tag run is not affected). Default: agents fix these before you sign; nothing for you to do.
3. Known issues for the release notes (label known-issue-4.5, accepted): #179, #441, #261, #509, #510. Also mention CodeQL #46 and #47 and the voice/wake items.
4. Merged today (27 Oct), main items: #531 jarvis wake record, #537 gate slice 3, #541 tag commit may be a docs-only descendant of the signed commit, #560 hotfix safety, #570 skills revoke race, #571 browser pilot IP pinning, #577 and #579 GitHub writes class 2, #587, #588 hotfix recover fixes.
5. Your decisions (a safe default is given; say "default"):
   - The old trackers #160 and #159: accept as known issues. Default: accept, work continues in 4.5.1.
   - Rows in the old bug tables are informational only. `#77` and `HO-WAKE` are still worth checking on your PC (checklist row 12): mic mute works, your custom wake model is still used after the update (`jarvis doctor`).

## Do: the 5 smoke rows (about 1 minute each, ears needed for S2-S4)
Install the newest beta first. Make the sheet (an agent already committed it; this command rebuilds it only if empty):

    jarvis release sheet --version 4.0.1

1. S1: `jarvis update --channel beta`, then `jarvis doctor`: no red line. (Earlier attempt showed UPDATE_NOT_APPLIED with nothing lost; try again on the newest beta.)
2. S2 (ears): say "היי ג'רוויס, מה השעה?": wakes and answers in Hebrew with the right time.
3. S3 (ears): interrupt an answer in the middle: it stops within about a second and listens.
4. S4 (ears): ask for a medium-risk action by voice: popup on top, say "כן", approved once, no echo self-approval.
5. S5: `jarvis update --rollback`, check your data, then `jarvis update` forward again: data kept both ways.

## Sign-off commands (only you run these)

    jarvis release signoff --version 4.0.1 --row S1 --result pass
    jarvis release signoff --version 4.0.1 --row S2 --result pass
    jarvis release signoff --version 4.0.1 --row S3 --result pass
    jarvis release signoff --version 4.0.1 --row S4 --result pass
    jarvis release signoff --version 4.0.1 --row S5 --result pass
    jarvis release signoff --version 4.0.1 --finish --beta <4.0.1-beta.N you tested> --commit <full SHA of that beta>

Use `--result fail --note "..."` for a failure. Then confirm:

    jarvis release status --version 4.0.1

It prints PASS or TODO for each gate and what remains. The tag is made only after you sign and the gate is clear.
