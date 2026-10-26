# Owner review page for 4.0.1 (about 15 minutes)

Evidence: `docs/release/EVIDENCE_4.0.1.md` (state of main 043583a6, 26 Oct 2026). Agents did the machine part; these are the only things left for you. Nothing was signed or tagged by an agent. Refs #505.

## Look at (3 minutes)
1. Gate dry run in the evidence file: what is missing (signature, checklist row 12, a short list of bug-table rows that need your decision).
2. Known issues for the release notes (section in the evidence file): #179, #441, #261, #509, #510, CodeQL #46 and #47, voice/wake items, #505.
3. Decisions only you can take (a safe default is given; say "default" and an agent applies it):
   - #160 (CI reliability tracker, P1) and #159 (perf tracker): close as trackers, or accept as known issues for 4.0.1. Default: accept (`known-issue-4.5`), work continues in 4.5.1.
   - Bug-table rows `#122, #71, #1, #58, #60, #77, HO-WAKE, HO-71, HO-OWNER` (V4.5 table), `#189, #193` (V5.0), `#219, #224` (V6.0): owner/device-gated or later-version work. Default: mark each `accepted (owner decision 4.0.1, work in 4.5.1/5.0/6.0)` in a docs PR after your OK. `#77` and `HO-WAKE` are checked on your PC (checklist row 12): mic mute works, your custom wake model is still used after the update (`jarvis doctor`).
   - `jarvis release status` still lists #510, #509, #179 as blockers although the gate accepts them (label `known-issue-4.5`). Cosmetic; an agent aligns the two.

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
