# 2026-10-27 CLI automation: install-latest and release run

Asked: the two owner-automation commands from LEAN_PROCESS_V2 items 5 and 6.

Changed: `core/src/jarves/install_latest.py` (`jarvis install-latest [--channel] [--dry-run]`, reuses UpdateChecker.check /
prepare_install / launch_installer / rollback), `core/src/jarves/release_run.py` + `release_cmd.py` (`jarvis release run`),
guides `install_latest` and `release_run` (EN+HE), tests `test_install_latest.py`, `test_release_run.py`, DOC_MAP rows, changelog.d.

Verified: both new test files, test_cli_lazy, release tests, `python core/tools/prepush.py` green after each commit.
NOT verified: a real silent install on the owner PC (needs the installed build; Tier R review first), a real `release run`
against live GitHub. Both commands are Tier R.

Notes: no new feature switch (a command, not a feature); parity is the guides plus the commands themselves. No new
dashboard texts, so no locale part was added; the guides carry their own Hebrew. `release run` never tags or pushes.

## Review fixes (Opus review of #640)
1. `release run` now has `release_run.cmd_run`; test goes through `release_cmd.cmd_release(action="run")`.
2. Setup force-stops everything under {app}\python, so `install-latest` is two phases: `begin` (verify, backup, stop, launch,
   keeps the setup process id) and a detached PowerShell waiter OUTSIDE {app} that waits for the setup process to exit and
   then starts `install-latest --finish STATE` from the NEW program (checks, rollback, report file). Manual proof on the owner
   PC: run `jarvis install-latest --channel beta` from PowerShell; the window may close; after 2-4 minutes
   `jarvis install-latest --report` shows PASS and `jarvis update status` shows the new version. First install of this
   feature: the installed version has no `--finish`, so the waiter writes an honest FAIL-style note telling to run doctor by hand.
3. A failed launch restarts the Core if it ran and prints a clean report.
4. Checks wait for the setup process to exit; no rollback while setup runs; Core-not-running no longer waits on the 90 s watch.

## Review round 2
Waiter script is UTF-8 with BOM; every PowerShell quote character (' and U+2018/2019/201A/201B) is doubled; old report, state
and pid files are deleted before launch; the waiter is started BEFORE setup (it waits for the setup pid published by the parent,
or finds setup by its path, `cancel` if setup never started); waiter runs with CREATE_NO_WINDOW and cwd <home>/updates.
