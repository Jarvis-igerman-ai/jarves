# V5 release prep (5.0.0), 25 Oct 2026 (#192, with #191)

Docs only. Nothing was versioned, tagged or published; no issue was commented or closed except as the PR flow does. Refreshed after the V5
phone PRs (#262, #267, #272), the V5 security fixes (#245, #248, #256, #259), the tests (#273, #275), #270, #274, the DST fix (#232) and the
flaky fixes (#250, #287) merged.

## What was prepared

- `docs/reviews/BUG_TABLE_V50_2026-10-25.md` (PR for #191): current truth for every V5 issue, F1 to F14, H1 to H4, with merged PR numbers.
- `docs/reviews/RELEASE_CHECKLIST_5.0.0.md`: draft in the gate's format (16 rows, honest statuses). Version stays 4.0.1.
- Sheet tool `--since` and `--extra-rows` (PR #288) so `MANUAL_TEST_5.0.0.md` can carry the 18 SWV5_ACCEPTANCE rows. Not generated yet:
  generate it only after the last V5 merge and the `5.0.0` CHANGELOG section (a later row change voids signatures).

## Gate dry run (read-only, no tag, no push)

`core/tools/release_gate.py --tag v5.0.0 --sha adfe3e21 --verified-sha adfe3e21 --bug-tables 'docs/reviews/*BUG_TABLE*.md'`:
FAILED with 43 problems. 18 of them come from the 5.0 checklist and the 5.0 bug table (`--bug-tables 'docs/reviews/BUG_TABLE_V50*.md'`);
the rest are the 4.5 table rows (about 22 open rows, stale in places, for example #163, #161, #164 are fixed in code but still `open` there),
a failed nightly of 22 Oct (the 5-night streak is not reached) and the missing sign-off sheet. Also "no green jarves-checks run for
adfe3e21" (that SHA is a merge commit, not the tagged one). The tool's default repo is already `Jarvis-igerman-ai/jarves`.

## Remaining steps, in order

1. Finish 4.5 first (owner pipeline): refresh `BUG_TABLE_V45_2026-10-24.md` to the merged state (many rows are fixed now), close its open P1 and P2 rows or get owner acceptances.
2. F6 (#205): write the `git log --output=` deny and test, or owner acceptance; live Claude Code check is owner-gated (paid session). Then close #189. Close #187 (merged in #275) with evidence.
3. Owner decisions on the open P3 list: F9, F12, F13, F14, H1, H2, H3, H4, #194. #206 stays open until then.
4. Run `jarvis selftest`, `doctor`, `speedtest --class owner` on the newest beta, triage.
5. Version bump to 5.0.0, CHANGELOG section `5.0.0`, generate `MANUAL_TEST_5.0.0.md` with `--since` and `--extra-rows docs/reviews/SWV5_ACCEPTANCE.md`.
6. Installer upgrade 4.5 to 5.0 and rollback smoke (CI matrix, then the owner PC with a backup). Needs #165 and #166 (PRs #268, #269) merged.
7. Publish 5.0.0 as a beta; check 5 green nights.
8. Owner-gated for the stable tag: real Android sheet #193, trusted certificate #194 (or accept self-signed), signed sheet. If the owner is away: 5.0 stays beta, `mobile_access` stays `implemented_unverified`, no stable tag.
9. Gate dry run on the final commit, the owner says go, tag `v5.0.0`.

## Questions for the owner

- F6: can you run one live Claude Code check of `git log --output=<path>` in a read-only agent session?
- Accept the P3 list (F9, F12 to F14, H1, H2 deferred; H3, H4, #194 accepted with their reasons)?
- Do the 18 Android rows (#193) now, or ship 5.0 as beta only?
