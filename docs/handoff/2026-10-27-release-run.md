# 2026-10-27 jarvis release run

Asked: LEAN_PROCESS_V2 item 6. Split from PR 640 (install-latest is parked in issue 644, PR 640 kept as a draft).
Changed: release_run.py, release_cmd.py wiring, test_release_run.py, guide release_run (EN+HE), DOC_MAP row, changelog.d.
Verified: test_release_run (fakes, frozen clock, through cmd_release), prepush. Not verified: a live run against GitHub.
Never tags or pushes; refuses on red main, open P0-P2 (exact label known-issue-4.5 only), unsignable sheet.
