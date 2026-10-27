# Release gate preparation for the first signed stable (4.0.1), 24 Oct 2026

Docs only. Nothing was tagged, published, versioned, merged or changed on GitHub (read-only; no issue was commented or closed,
no CodeQL alert dismissed). Base: `jarves/main` 04ff433 (`jarves-checks` green). Branch `docs/release-45-prep` is pushed without a PR
(the coordinator opens it on "queue clear").

## What was prepared

- `docs/reviews/BUG_TABLE_V45_2026-10-24.md`: triage of all 24 open issues, the 7 open CodeQL alerts, #163, the voice latency
  findings, `selftest`/`speedtest`, and the open handoff items; severity, owner, status, plan, which release each blocks, and the
  list of what must still be fixed. Gate-readable (`Sev`, `Status`).
- `docs/reviews/RELEASE_CHECKLIST_4.0.1.md`: refreshed (version stays 4.0.1; no version file touched). Rows 3 and 9 to 12 are
  honestly `open`.
- `docs/reviews/MANUAL_TEST_4.0.1.md`: regenerated with `manual_test_sheet.py --version 4.0.1 --force` (35 rows, all `pending`,
  nothing signed; the old sheet had 29 rows from 23 Oct and no recorded results, so nothing was lost). For the wizard-based
  sign-off: install the newest beta, then use the dashboard page Release sign-off or `jarvis release signoff`. Any later change
  to a row voids the signature, so regenerate only before the owner starts.

## Dry run (no tag, no push)

```
C:/work/jarvis/core/.venv/Scripts/python.exe core/tools/release_gate.py --tag v4.0.1 --sha <main sha> --verified-sha <main sha> \
  --bug-tables 'docs/reviews/*BUG_TABLE*.md'
```

(`--nightly-json` and `--checks-json` take saved API output when no token is available. The Actions dry run is
`gh workflow run release.yml -f tag=v4.0.1 -f dry_run=true`, inputs `tag`, `dry_run`; not triggered here.)
Result: FAILED, 70 problems. Passing: tag format, SHA check (jarves-checks green on 04ff433). Missing:

1. Checklist rows 3, 9, 10, 11, 12 and 25 open P1/P2 bug-table rows (see the table's last section).
2. Nightly: only 4 scheduled runs exist (21 Oct green, 22 Oct red, 23 and 24 Oct green). The gate needs the last 5 scheduled nights
   green on consecutive days: earliest 27 Oct (23 to 27 Oct), if none fails; a red night restarts the count.
3. Manual sign-off: 35 rows pending, sheet unsigned (Tested beta, Commit, Date missing). The tested beta commit must equal the tag
   commit or be an ancestor with docs-only changes since.
4. Candidate dir: with `--candidate-dir dist` the gate also checks the installer is `4.0.1.<build>`; not run here (no dist).

## Automated gates for "I test and release" and the commands (run on the beta)

| Gate | Command | Notes |
|---|---|---|
| State of the release | `python core/tools/release_status.py` (needs `gh auth login`) | main CI, installer proof, candidate, beta, blocking list |
| Selftest | `jarvis selftest` (installed: `%LOCALAPPDATA%\Programs\JARVES\jarvis.cmd selftest`) | 24 Oct: PASS 10/10 on 4.0.1.349 |
| Doctor | `jarvis doctor` | all green on 24 Oct; check the wake model threshold (HO-WAKE) |
| Speedtest, owner PC | `jarvis speedtest --class owner` (x3 for a median) | no failing rows 24 Oct |
| Speedtest, CI gate | `python -m jarves.cli speedtest --class ci --gate --no-store --no-net --idle-window 5` | runs on every PR; also `core/tools/perf_gate.py --class ci --runs 3` |
| Live voice latency | `core/tools/silent_live.py` (see `docs/reviews/V45_VOICE_LATENCY_2026-10-24.md`, about 0.30 USD) | owner PC with OpenAI credit; #122 |
| Installer install/upgrade/uninstall smoke | CI job "Windows installer (build + silent install smoke)"; local: `packaging/` scripts invoked from `jarves-checks.yml` | green on every code PR |
| Upgrade and rollback matrix | CI job `installer-upgrade`: `./packaging/upgrade_matrix.ps1 -Candidate dist/JARVES-Setup-<ver>.exe -Version <ver> -MetricsDir dist` | SHA-pinned old installers; user rollback `jarvis update --rollback --yes` |
| Rollback of a published release | `python core/tools/release_rollback.py --to <tag>` (dry run default) | needs `RELEASES_TOKEN` for `--execute` |
| Nightly streak | `gh run list -R igalgerman/jarves -w nightly.yml --limit 10` | current streak 2 of 5 (see above) |
| Pre-push | `python core/tools/prepush.py` | local gate before each push |
| Sign-off sheet | `python core/tools/manual_test_sheet.py --version 4.0.1`, then `jarvis release signoff --version 4.0.1 --row <ID> --result pass` and `--finish` | owner only |
| Stable gate dry run | command above | final check before the tag |

## Questions for the owner

1. Do the 7 CodeQL alerts block the 4.0.1 stable, or only V4.5? (The plan's gate is for V4.5; the checklist now holds 4.0.1 back
   until they are reviewed, which is the safer reading.)
2. May #164 and #165 (labelled P2 upgrade features) and #153, #159, #60, #71, #122 be moved to 4.5 so the 4.0.1 stable does not wait
   for them? The P3 "proposed accept" list in the bug table needs a yes or no each.
3. #161: move the repo to an organization for the native merge queue, or accept the serial flow?
4. Open an issue for VOICE-GREET (no issue exists)?

## Recommended order to a signable build

1. Fix #163 (median of 3, lift quarantine), then the `mobile.py` path fix and the review verdicts for the other 5 CodeQL alerts
   (Opus security review), then a CodeQL run on main; fix VOICE-GREET with a stalled-greeting test.
2. Let the owner answer the questions above; update the checklist rows 9 to 12 and bug table statuses with evidence.
3. Wait for main to be green and 5 green nights in a row (earliest 27 Oct), without merging risky changes in between (a code
   change resets nothing, but a red night does).
4. Install the newest beta on the owner PC; run `selftest`, `doctor`, `speedtest`; the owner completes the sheet in the wizard
   and signs (`--finish`); commit the signed sheet (docs only).
5. Dry-run the gate on that commit, then the owner or coordinator tags `v4.0.1`.
