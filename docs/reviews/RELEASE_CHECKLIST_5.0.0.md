# Release checklist: JARVES 5.0.0 (stable), DRAFT

Draft prepared 25 Oct 2026 (#192), refreshed against `jarves/main` adfe3e21 after the V5 merges. Nothing is versioned, tagged or published:
`core/pyproject.toml` still says 4.0.1. Read by `core/tools/release_gate.py` when the tag `v5.0.0` is pushed: every row must be `done`,
`n/a` or `accepted`. Rows marked "checked by the gate" are verified automatically at tag time. Rows that are not `done` are honest and stay
red until the work is finished or the owner decides. Steps: [RELEASING](../RELEASING.md) "Stable release, step by step". Triage:
[BUG_TABLE_V50_2026-10-25](BUG_TABLE_V50_2026-10-25.md). Plan: [V5_INVENTORY_AND_PLAN_2026-10-24](../plans/V5_INVENTORY_AND_PLAN_2026-10-24.md).
Safe default (plan G12 and G13): 5.0.0 goes out as a beta only; the stable tag waits for the owner's signed sheet.

| # | Item | Status | Evidence / what is left |
|---|---|---|---|
| 1 | `jarves-checks` green on the release commit | n/a (checked by the gate at tag time) | The gate needs a green push run on `main` for the tagged commit. |
| 2 | Latest nightly run green | n/a (checked by the gate at tag time) | Owner 26 Oct: no five-nights rule; the newest nightly on `main` must be green and at most 2 days old. |
| 3 | The 4.5 stable gate has passed first | open | Owner pipeline is 4.5, then 5, then 6. The 4.5 bug table (`BUG_TABLE_V45_2026-10-24.md`) and its checklist must be closed before this tag. |
| 4 | Bug tables: no open P0-P2 | open | `docs/reviews/BUG_TABLE_V50_2026-10-25.md`: the open P2 rows are F6 (#205, no fix), #189 (until F6), #191 and #192 (this prep), #193 (owner-gated). F1 to F5 and #181 to #188, #190 are fixed with merged PRs. Re-triage on the release day. |
| 5 | Open GitHub issues triaged | done | All V5 issues (#181 to #194, #200 to #206) are classified in the 5.0 table with owner, plan and merged PR; P3 reasons are listed. Repeat for any new issue before the tag. |
| 6 | `jarvis selftest` and `speedtest` problems triaged | open | Last run 24 Oct on 4.0.1.349 (no V5 code). Run `jarvis selftest`, `jarvis doctor`, `jarvis speedtest --class owner` on the 5.0 beta and triage every finding. |
| 7 | Security fixes F1 to F6 merged with tests | open | F1 #245, F2 #256, F3 #248, F4 #259, F5 #272 merged. F6 (#205, coding agents writing outside the worktree via `git log --output=`) is not written and needs a live Claude Code check (owner-gated), or an owner acceptance with a reason. |
| 8 | V5 pillars merged: phone prefs, goal plans, indicator, resume proof, scenario test, capability evidence, real-data devstatus | done | #262 (prefs), #267 (goal plans), #272 (indicator, F5), #273 (resume proof), #275 (scenario test), #270 (capability evidence), #274 (devstatus on real data). |
| 9 | Every V5 feature has its guide (EN and HE), CLI parity entry and an honest capability row | n/a (checked by CI: `test_guides`, `test_cli_parity`, `test_capabilities`) | No V5 feature may be `verified_on_device` before row 13. |
| 10 | Version bump to 5.0.0 and CHANGELOG section `5.0.0` | open | Not done in this prep by design (no version bump). `changelog.d/*.md` fragments are folded in at release time; the sheet generator reads the `5.0.0` section. |
| 11 | Installer: upgrade 4.5 to 5.0 and rollback smoke | open | The installer chain (#164 merged; #165 and #166 in the queue) plus the CI upgrade matrix, and the owner-PC smoke from plan section 5 (back up first, install over 4.5, run one plan, confirm one preference, resume one chat, `jarvis update --rollback` and back). Record the result in `docs/handoff/`. |
| 12 | Release gate dry run on the final commit | open | Run `core/tools/release_gate.py --tag v5.0.0 --sha <sha> --verified-sha <sha>` after the version bump; problem count of the first dry run is in the handoff note. The repo default in the tool is already `Jarvis-igerman-ai/jarves`. |
| 13 | Owner's manual test sign-off (`MANUAL_TEST_5.0.0.md`) | n/a (checked by the gate at tag time) | Not generated yet: generate it after the CHANGELOG section exists and the last V5 merge, with `core/tools/manual_test_sheet.py --version 5.0.0 --since <4.5 or 4.0.1 tag> --extra-rows docs/reviews/SWV5_ACCEPTANCE.md` (needs the sheet tool PR #288). Then `jarvis release signoff --version 5.0.0` per row and `--finish`. |
| 14 | Real Android acceptance (#193): the 18 SWV5_ACCEPTANCE rows, suspend and resume, host down, Wi-Fi change, real microphone | open | Owner only, about 20 minutes. If the owner is away: `mobile_access` stays `implemented_unverified` and 5.0 ships as beta. |
| 15 | Trusted certificate for the phone (#194) | open | Owner decision. Safe default: self-signed certificate on home Wi-Fi, warning step in the guide; propose `accepted` with that reason. |
| 16 | Stable tag only after the owner says go | open | Plan G13: the stable tag is not pushed until the owner signs off. |
