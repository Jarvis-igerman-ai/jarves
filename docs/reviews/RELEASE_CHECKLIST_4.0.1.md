# Release checklist: JARVES 4.0.1 (stable)

Prepared 23 Oct 2026 (#89). Read by `core/tools/release_gate.py` when the tag `v4.0.1` is pushed: every row must be
`done`, `n/a` or `accepted`. Rows marked "checked by the gate" are verified automatically at tag time, not here.
Steps: [RELEASING](../RELEASING.md) "Stable release, step by step".

| # | Item | Status | Evidence / what is left |
|---|---|---|---|
| 1 | `jarves-checks` green on the release commit | n/a (checked by the gate at tag time) | The gate requires a green push run on `main` for the tagged commit. |
| 2 | Nightly green 5 nights in a row | n/a (checked by the gate at tag time) | 3 nightly runs so far (21 and 23 Oct green, 22 Oct red: a browser bug fixed in #90). Wait for 5 green in a row. |
| 3 | Bug tables: no open P0-P2 | done | `release_gate.bug_table_open` over `docs/reviews/*BUG_TABLE*.md` lists 0 rows (#90 read the tables correctly and synced V4-SC-1, V4-RP-1, V4-RP-2; owner accepted B13, B14, Q05 on 23 Oct). |
| 4 | Open GitHub issues triaged | done | No bug report is open. #58: its 4.0.1 scope (installer setup, keep settings) shipped in #68 and #70 and is tested by sign-off rows C3 and OI-02. #60, #61: shipped in part (sign-off page, setup and tour), the rest is later work. #71 is 4.0.2. #77 is in PR #80. #89 is this CI and release work. #1-#5 and #50 are roadmap and legacy gates. |
| 5 | `jarvis selftest` and `speedtest` problems triaged | done | `jarvis selftest` (offline, empty temp home, 23 Oct): passed, 1 warning = 6 features without keys or connections in an empty home (expected, not a bug). `speedtest --gate --class ci` runs green in every PR's performance gate. |
| 6 | CHANGELOG lists the release | done | `CHANGELOG.md` section `4.0.1`; the installed app shows it on the Release notes page. |
| 7 | Installer: install, upgrade, rollback, uninstall | done | Every code PR builds the installer and runs the silent install / run / upgrade / uninstall smoke and the upgrade + rollback matrix (SHA-pinned old installers). The owner's real install is sign-off row OI-02. |
| 8 | Owner's manual test sign-off | n/a (checked by the gate at tag time) | `docs/reviews/MANUAL_TEST_4.0.1.md` (28 rows): install the newest beta, then `jarvis release signoff --version 4.0.1 --row <ID> --result pass` per row (or the dashboard page Release sign-off), then `--finish`. |
