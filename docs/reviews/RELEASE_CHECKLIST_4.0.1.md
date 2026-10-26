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
| 3 | Bug tables: no open P0-P2 | open | `docs/reviews/BUG_TABLE_V45_2026-10-24.md` lists the open P1 and P2 rows (7 CodeQL alerts, #163, #160, #161 and more). The 8 older tables list 0. Work and owner decisions: the last section of the new table. |
| 4 | Open GitHub issues triaged | done | All 24 open issues are classified in section D of the new bug table (owner, plan, which release each blocks, P3 reasons). Triage is done; the fixes are rows 3 and 9 to 12. |
| 5 | `jarvis selftest` and `speedtest` problems triaged | done | 24 Oct on the installed 4.0.1.349: `selftest` PASS 10/10, `doctor` all green, `speedtest --class owner` no failing rows; the one real finding (greeting stall crash) is row 11. `speedtest --gate --class ci` is green in every PR. Re-run on the newest beta before signing. |
| 6 | CHANGELOG lists the release | done | `CHANGELOG.md` section `4.0.1`; the installed app shows it on the Release notes page. |
| 7 | Installer: install, upgrade, rollback, uninstall | done | Every code PR builds the installer and runs the silent install / run / upgrade / uninstall smoke and the upgrade + rollback matrix (SHA-pinned old installers). The owner's real install is sign-off row OI-02. |
| 8 | Owner's manual test sign-off | n/a (checked by the gate at tag time) | The owner's sheet is [SIGN_OFF_4.0.1](../release/SIGN_OFF_4.0.1.md); that one signature is enough (no SIGNED file). `docs/reviews/MANUAL_TEST_4.0.1.md` (35 rows, regenerated 24 Oct, all pending): install the newest beta, then `jarvis release signoff --version 4.0.1 --row <ID> --result pass` per row (or the dashboard page Release sign-off), then `--finish`. |
| 9 | CodeQL: every open alert has a reviewed verdict or a fix | open | 7 open alerts (#6, #7, #13, #15, #19, #20, #21; all high). Preliminary reading: mostly name-taint false positives plus one path built before its allowlist check (`mobile.py`). Needs the independent security review and a CodeQL run on the release SHA, or an owner decision that they block V4.5 only. |
| 10 | Showstopper #163 root-fixed | open | The test is quarantined, not fixed (`core/tests/quarantine.toml`). Fix: median of 3 for the stale path, then lift the quarantine. |
| 11 | Voice start: a missing greeting never crashes Core | open | 1 of 5 live starts crashed at the 15 s limit (`V45_VOICE_LATENCY_2026-10-24.md`). Fix and test, or the owner accepts it for 4.0.1 with a reason. |
| 12 | Owner PC checks: custom wake model after migration, #77 mic mute and appearance | open | Owner step; recorded as sheet rows, closes #77 with evidence. |
