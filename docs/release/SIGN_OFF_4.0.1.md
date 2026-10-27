# Sign-off sheet: JARVES 4.0.1 (stable)

Prepared 26 Oct 2026 (#462). Short and plain: tick each row, then sign. The rule (owner 26 Oct): there is **no five-nights rule**.
A stable tag needs: main green, the release-gate dry run green, zero open P0-P2, and this signed sheet. There is no nightly requirement.

## 1. What the machine checks (you do nothing)

| Check | Who | State on 26 Oct (re-run before signing) |
|---|---|---|
| `jarves-checks` green on the tagged commit | gate | checked at tag time |
| Release checklist `docs/reviews/RELEASE_CHECKLIST_4.0.1.md`: rows 9 to 12 are still `open` | gate | NOT clear |
| Bug tables (`docs/reviews/*BUG_TABLE*.md`) and checklist rows: RETIRED from the gate (26 Oct), printed as "legacy, not gating" | information | 32 open rows (V4.5 table 25, V5.0 table 4, V6.0 table 3). The V4.5 table is a 24 Oct snapshot; rows fixed since need their status updated |
| Open GitHub issues labelled P0-P2 (planned work `enhancement`, `v5`-`v9` or `4.5.1` skipped unless also `bug`; the owner-only label `known-issue-4.5` marks an accepted known issue: not a blocker, listed by the gate) | gate | 5 open: #160 (P1 CI reliability), #179, #261, #441, #447 (P2). 35 carry a P0-P2 label in all |
| Installer install / upgrade / rollback / uninstall smoke | CI | green on every code PR |
| `jarvis selftest`, `doctor`, `speedtest` on the newest beta | agent | re-run on the beta you sign |

The legacy bug-table and checklist rows above are information only; the gate decides on the live P0-P2 issues, main green and your signed sheet. Today the gate says NOT READY. Dry run: command in section 5.

## 2. What only you can do (manual rows)

The machine sheet `docs/reviews/MANUAL_TEST_4.0.1.md` has 36 rows (all `pending`). Record each with
`jarvis release signoff --version 4.0.1 --row <ID> --result pass` (or the dashboard page Release sign-off). Install the newest beta first:
`jarvis update --channel beta`.

| Rows | What | Where | Time |
|---|---|---|---|
| OI-02 | finish the first-run wizard | PC | 5 min |
| OV-01, OV-02, OV-03, OV-07 | Hebrew time question, English question, interrupt, full exit by voice | PC | 8 min |
| OP-01, OP-02, OP-03 | approval by voice, "yes", Esc | PC | 6 min |
| OL-I1 to OL-I4 | in-app update, `jarvis doctor`, `jarvis update --rollback`, update again | PC | 15 min |
| OO-02, OR-01, OR-02 | Health page, `jarvis diag` ZIP, log view | PC | 7 min |
| C1 to C18 | one try per changed feature listed in the sheet | PC | 25 min |
| G1, P1 | skim the changed guides and dashboard pages, English then Hebrew | PC | 15 min |
| Phone row (#77 mic mute, wake model after migration; checklist row 12) | owner PC checks | PC / phone | 5 min |

About 90 minutes in all. Rows that cannot run (closed app, no device): `accepted-blocked` with a note.

## 3. Beta features (stage: beta, off by default, not part of the stable promise)

Listed so you test them only if you turn the global "beta features" opt-in on: software project manager and its factory flows
(`jarvis project ...`, feature `project_manager`), self-management (`jarvis manage`), Setup and tour wizard polish. Failing here does not
block the tag; record the result and the issue.

## 4. Sign

1. All rows above `pass` or `accepted-blocked` with a note: `jarvis release signoff --version 4.0.1 --finish --beta <4.0.1-beta.N> --commit <full SHA>`.
2. That is the only signature: `--finish` signs the sheet (owner, date, commit); there is no second file to create. Commit the signed sheet.

The commit must be the tag commit, or an ancestor with only documentation changes since. A change to the sheet after signing needs a new signature.

Owner: ____________________   Date: ____________   Tested beta: ____________

## 5. Dry run (does not tag or release)

```
python core/tools/release_gate.py --tag v4.0.1 --sha <commit> --bug-tables 'docs/reviews/*BUG_TABLE*.md'
```

It lists everything still missing and exits 1 until all of it is clear. Or: Actions > `jarves-release` > Run workflow with the tag and
`dry_run` ticked. Steps: [RELEASING](../RELEASING.md).
