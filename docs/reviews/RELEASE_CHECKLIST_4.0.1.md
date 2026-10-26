# Release checklist: JARVES 4.0.1 (stable)

Prepared 23 Oct 2026 (#89), refreshed 24 Oct 2026 against `jarves/main` 04ff433. Read by `core/tools/release_gate.py` when the
tag `v4.0.1` is pushed: every row must be `done`, `n/a` or `accepted`. Rows marked "checked by the gate" are verified
automatically at tag time, not here. Rows that are not `done` are honest: they stay red until the work is finished or the owner
decides. Steps: [RELEASING](../RELEASING.md) "Stable release, step by step". Triage detail:
[BUG_TABLE_V45_2026-10-24](BUG_TABLE_V45_2026-10-24.md).

| # | Item | Status | Evidence / what is left |
|---|---|---|---|
| 1 | `jarves-checks` green on the release commit | n/a (checked by the gate at tag time) | The gate requires a green push run on `main` for the tagged commit (04ff433 is green on 24 Oct). |
| 2 | Latest nightly run green | n/a (checked by the gate at tag time) | Owner 26 Oct: no five-nights rule. The gate needs the newest completed nightly on `main` green and at most 2 days old. |
| 3 | Bug tables: no open P0-P2 | done (machine part) | Re-verified 26 Oct against `jarves/main` b0f99c68: every code, CI and security P0-P2 row in `BUG_TABLE_V45_2026-10-24.md` is fixed, accepted or moved to 4.5 with evidence (owner decisions of 26 Oct). Rows still open are owner-gated only: #1, #58, #77, HO-WAKE, HO-OWNER (V5 and V6 tables: #193, #219, #224, owner-gated). |
| 4 | Open GitHub issues triaged | done | All 24 open issues are classified in section D of the new bug table (owner, plan, which release each blocks, P3 reasons). Triage is done; the fixes are rows 3 and 9 to 12. |
| 5 | `jarvis selftest` and `speedtest` problems triaged | done | 24 Oct on the installed 4.0.1.349: `selftest` PASS 10/10, `doctor` all green, `speedtest --class owner` no failing rows; the one real finding (greeting stall crash) is row 11. `speedtest --gate --class ci` is green in every PR. Re-run on the newest beta before signing. |
| 6 | CHANGELOG lists the release | done | `CHANGELOG.md` section `4.0.1`; the installed app shows it on the Release notes page. |
| 7 | Installer: install, upgrade, rollback, uninstall | done | Every code PR builds the installer and runs the silent install / run / upgrade / uninstall smoke and the upgrade + rollback matrix (SHA-pinned old installers). The owner's real install is sign-off row OI-02. |
| 8 | Owner's manual test sign-off | n/a (checked by the gate at tag time) | The owner's sheet is [SIGN_OFF_4.0.1](../release/SIGN_OFF_4.0.1.md); the gate also needs `docs/release/SIGNED_4.0.1`. `docs/reviews/MANUAL_TEST_4.0.1.md` (35 rows, regenerated 24 Oct, all pending): install the newest beta, then `jarvis release signoff --version 4.0.1 --row <ID> --result pass` per row (or the dashboard page Release sign-off), then `--finish`. |
| 9 | CodeQL: every open alert has a reviewed verdict or a fix | open (5 new alerts) | The 7 alerts of 24 Oct are closed: #6, #7, #15 fixed by code (fixed 24-25 Oct); #13, #19, #20, #21 dismissed as false positive with test evidence (PR #195, owner pre-approved). 5 NEW open alerts need a verdict: #43 `health.py:86`, #45 `security_scans.py:93`, #46 and #47 `skills/generated.py:446,448`, #48 `github_write.py:402` (all clear-text rules). Not dismissed here. |
| 10 | Showstopper #163 root-fixed | done | PR #196 (median of 4 stale samples, quarantine lifted); `core/tests/quarantine.toml` has no entries; issue #163 closed 24 Oct. |
| 11 | Voice start: a missing greeting never crashes Core | done (issue #179 still open) | PR #207 (greeting retry inside the 15 s budget) and PR #454 (tests: stalled, late and dropped greeting leave an open silent room, no exception); live re-measure 10 of 10 starts got the greeting (comment on #179). Issue #179 is labelled P2 + bug, so the gate still lists it until it is closed with this evidence. |
| 12 | Owner PC checks: custom wake model after migration, #77 mic mute and appearance | open (owner) | Owner step; recorded as sheet rows, closes #77 with evidence. Nothing a machine can prove. |
