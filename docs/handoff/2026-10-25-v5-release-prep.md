# V5 release prep (5.0.0), 25 Oct 2026 (#192, with #191)

Docs only. Nothing was versioned, tagged, published or merged; no issue was commented or closed. Branch `docs/v5-release-prep-192` is pushed
without a PR (the coordinator opens it when a slot is free; the 4 slots are used by 4.5 work). It contains a merge of
`docs/v5-bug-table-191` (the 5.0 bug table and the V5 security review), so merge #191 first; the branch then shows only its own files.

## What was prepared

- `docs/reviews/RELEASE_CHECKLIST_5.0.0.md`: draft in the gate's format (`| # | item | status |`, 15 rows, honest statuses; 2 rows are
  gate-checked, row 5 done, the rest open). The version stays 4.0.1 (no bump).
- `docs/reviews/BUG_TABLE_V50_2026-10-25.md` (on branch #191): 15 open P2 rows, none P0 or P1.
- No `MANUAL_TEST_5.0.0.md` was committed. `core/tools/manual_test_sheet.py --version 5.0.0` runs, but its output today is wrong for 5.0:
  there is no `5.0.0` CHANGELOG section (it falls back to the 4.0.1 notes), the repo has no stable tag after `v3.0.0` (so `--since` defaults
  to v3.0.0 and it lists 61 guides), and the generator has no way to add the 18 SWV5_ACCEPTANCE phone rows (only the standing V4 rows
  `STANDING` and CHANGELOG/guide/page rows). Needed: (a) the `5.0.0` CHANGELOG section after the V5 merges, (b) `--since` set to the 4.5
  or 4.0.1 tag, (c) a small tool change (for example `--extra-rows docs/reviews/SWV5_ACCEPTANCE.md`, rows P1 to P18, or hand-added
  rows) so the phone steps are in the signed sheet. Generate it only after the last V5 merge: any later row change voids a signature.

## Dry run of the gate (read-only, no tag, no push)

```
C:/work/jarvis/core/.venv/Scripts/python.exe core/tools/release_gate.py --tag v5.0.0 --sha <sha> --verified-sha <sha> \
  --bug-tables 'docs/reviews/*BUG_TABLE*.md' --repo Jarvis-igerman-ai/jarves
```

Result: FAILED, 28 problems. Parsed correctly: tag format, checklist rows (10 not done), the 5.0 bug table (15 open P2), no sign-off sheet,
only 4 scheduled nightly runs, no green `jarves-checks` run for the branch commit (expected on a docs branch).
Gate gaps found: (1) the gate's `REPO` default is `igalgerman/jarves`; after the repo move (#182, #161) `release.yml` must pass the right
repo or the nightly and checks look-ups read the wrong place. (2) `--candidate-dir` was not run (no installer); it checks `5.0.0.<build>`.
(3) The sign-off needs the sheet generator fix above. (4) Rows 3, 7 and 8 have no automatic check; the coordinator keeps them true by hand.

## Remaining steps, in order

1. Finish 4.5 first (owner pipeline). Merge #191 (this table), then this prep PR, when slots allow.
2. Security fixes with tests: F1 (#200), F2 (#201), F3 (#202) exist as branches; write F4 (#203) and F6 (#205). F6 is only real if a live Claude Code accepts `git log --output=`: **owner-gated** (needs a paid session); safe default: treat it as real and apply the deny list.
3. Merge phone and test branches: 183, 184, 185 (with commit a05cd80c, the F5 fix), then 186 and 187, then 188 and 190. One PR at a time; the security PRs are `no-automerge`.
4. Real-GitHub read-only smoke for #190, recorded in HANDOFF (public data, GET only; no owner step).
5. Owner decisions on the P3 list in the bug table (F9, F12 to F14, H1 to H4, #181, #182, #188, #194).
6. Version bump to 5.0.0, CHANGELOG section, fix the sheet generator, generate `MANUAL_TEST_5.0.0.md` with the 18 phone rows.
7. Installer upgrade 4.5 to 5.0 and rollback smoke (CI matrix, then the owner PC with a backup, `jarvis update --rollback` and back). Run `selftest`, `doctor`, `speedtest --class owner` on the 5.0 beta and triage.
8. Publish 5.0.0 as a **beta** (every green main already does that). Check 5 green nights.
9. Owner-gated for the stable tag: (a) the real Android sheet #193 (about 20 minutes, adb or by hand on the phone, REAL-PASS or accepted-blocked with a note); (b) trusted certificate #194 (Tailscale account); (c) the signed sheet (`jarvis release signoff --version 5.0.0`, `--finish`) committed. Safe defaults if the owner is away: 5.0 stays beta, `mobile_access` stays `implemented_unverified`, self-signed certificate on home Wi-Fi, no stable tag.
10. Gate dry run on the final commit, then the owner says go and the tag `v5.0.0` is pushed.
