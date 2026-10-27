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
