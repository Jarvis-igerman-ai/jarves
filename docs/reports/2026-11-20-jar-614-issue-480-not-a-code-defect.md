# JAR-614: issue #480 is the scanner's tracker, not a code defect (20 Nov 2026)

Ticket JAR-614 asks to "fix" #480 and close it with `Fixes #480`. Stopped before changing code. Why:

1. **#480 is the single tracker of the recurring security scan.** The workflow `jarves-security-scans` writes it
   (`core/src/jarves/security_scans.py`, `TRACKER_MARKER`, label `from-diagnostics`). Its body today says:
   no open findings; coverage INCOMPLETE because `dependabot` and `secret_scanning` return HTTP 403 to the scan token.
2. **No finding is open.** The owner's comments on #480 (16 and 17 Nov) show from the live API: code-scanning
   alerts 46 and 47 were fixed by CodeQL itself, Dependabot alert 2 was fixed by PR #1192, and no alert is open.
   Secret scanning is turned off on the repo.
3. **Closing #480 stops all future scan reports.** `update_tracker` returns `tracker_closed` for a closed
   tracker and never files a new one. The test `test_two_trackers_or_a_closed_one_never_get_a_new_issue` pins that
   behaviour on purpose. So a merged PR with `Fixes #480` would silence the scanner without anyone noticing.
4. **Any code change is Tier R, not Tier N.** `core/tools/risk_class.py` reports
   `high-risk path: core/src/jarves/security_scans.py`. The earlier attempts (PRs #1133, #1150, #1171, #1214, #1239)
   changed that file to add owner-action lines. Each was closed after review rounds or the Tier R council.
5. **What is left is configuration.** Full coverage needs the scan token (`JARVES_GITHUB_TOKEN`) to have read access
   to Dependabot alerts. Secret scanning coverage needs secret scanning turned on for the repo. Both are owner
   decisions, and no code can do them.

## Owner decision needed

- **A (recommended):** close JAR-614 as "not a code defect". Keep #480 open. Optionally remove the `bug` and
  `enhancement` labels from #480 so intake stops creating fix tickets from it.
- **B:** approve a Tier R change to `security_scans.py` (for example, owner-action lines for refused sources, or
  filing a new tracker after the old one is closed). It goes through the Opus review, never automerge.
- **C:** give the scan token Dependabot read access (and turn on secret scanning if you want it). The next scan then
  rewrites #480 with full coverage, with no code change.
