# Release checklist: JARVES 5.0.0 (stable), DRAFT

Draft prepared 25 Oct 2026 (#192) against `jarves/main` 8b997585. Nothing is versioned, tagged or published: `core/pyproject.toml` still says
4.0.1. Read by `core/tools/release_gate.py` when the tag `v5.0.0` is pushed: every row must be `done`, `n/a` or `accepted`. Rows marked
"checked by the gate" are verified automatically at tag time. Rows that are not `done` are honest and stay red until the work is finished
or the owner decides. Steps: [RELEASING](../RELEASING.md) "Stable release, step by step". Triage:
[BUG_TABLE_V50_2026-10-25](BUG_TABLE_V50_2026-10-25.md). Plan: [V5_INVENTORY_AND_PLAN_2026-10-24](../plans/V5_INVENTORY_AND_PLAN_2026-10-24.md).
Safe default (plan G12 and G13): 5.0.0 goes out as a beta only; the stable tag waits for the owner's signed sheet.

| # | Item | Status | Evidence / what is left |
|---|---|---|---|
| 1 | `jarves-checks` green on the release commit | n/a (checked by the gate at tag time) | The gate needs a green push run on `main` for the tagged commit. |
| 2 | Nightly green 5 nights in a row | n/a (checked by the gate at tag time) | Count again after the last V5 merge; a red night restarts it. |
| 3 | The 4.5 stable gate has passed first | open | Owner pipeline is 4.5, then 5, then 6. The 4.5 bug table and checklist (branch `docs/release-45-prep`) must be closed before this tag. |
| 4 | Bug tables: no open P0-P2 | open | `docs/reviews/BUG_TABLE_V50_2026-10-25.md`: 15 P2 rows are unfinished (security F1 to F6, phone #183 to #185, #186, #187, #190, owner row #193, and the prep rows #191 and #192). All except F4 and F6 have a branch. Re-triage on the release day. |
| 5 | Open GitHub issues triaged | done | All V5 issues (#181 to #194, #200 to #206) are classified in the 5.0 table with owner, plan and branch; P3 reasons are listed. Repeat for any new issue before the tag. |
| 6 | `jarvis selftest` and `speedtest` problems triaged | open | Last run 24 Oct on 4.0.1.349 (no V5 code). Run `jarvis selftest`, `jarvis doctor`, `jarvis speedtest --class owner` on the 5.0 beta and triage every finding. |
| 7 | Security fixes F1 to F6 merged with tests | open | F1 `v5sec/plan-approvals-pc-only`, F2 `v5sec/pref-edit-proposed`, F3 `v5sec/goal-plan-show-taint` exist; F4 and F6 are not written; F6 needs a live Claude Code check. |
| 8 | V5 pillars merged: phone prefs, goal plans, indicator, resume proof, scenario test, capability evidence, real-data devstatus | open | Branches `v5/phone-prefs-183`, `v5/phone-goalplans-184`, `v5/phone-indicator-185`, `v5/phone-resume-186`, `v5/scenario-test-187`, `v5/capability-evidence-188`, `v5/devstatus-real-190`; none has a PR yet. |
| 9 | Every V5 feature has its guide (EN and HE), CLI parity entry and an honest capability row | n/a (checked by CI: `test_guides`, `test_cli_parity`, `test_capabilities`) | No V5 feature may be `verified_on_device` before row 13. |
| 10 | Version bump to 5.0.0 and CHANGELOG section `5.0.0` | open | Not done in this prep by design (no version bump). `changelog.d/*.md` fragments are folded in at release time; the sheet generator reads the `5.0.0` section. |
| 11 | Installer: upgrade 4.5 to 5.0 and rollback smoke | open | CI matrix `installer-upgrade` with the 4.5 installer, plus the owner-PC smoke from plan section 5 (back up first, install over 4.5, run one plan, confirm one preference, resume one chat, `jarvis update --rollback` and back). Record the result in `docs/handoff/`. |
| 12 | Owner's manual test sign-off (`MANUAL_TEST_5.0.0.md`) | n/a (checked by the gate at tag time) | Not generated yet: it must be generated after the V5 merges and the CHANGELOG section exist, and it needs the 18 phone rows (see the handoff note). Then `jarvis release signoff --version 5.0.0` per row and `--finish`. |
| 13 | Real Android acceptance (#193): the 18 SWV5_ACCEPTANCE rows, suspend and resume, host down, Wi-Fi change, real microphone | open | Owner only, about 20 minutes. If the owner is away: `mobile_access` stays `implemented_unverified` and 5.0 ships as beta. |
| 14 | Trusted certificate for the phone (#194) | open | Owner decision. Safe default: self-signed certificate on home Wi-Fi, warning step in the guide; propose `accepted` with that reason. |
| 15 | Stable tag only after the owner says go | open | Plan G13: the stable tag is not pushed until the owner signs off. |
