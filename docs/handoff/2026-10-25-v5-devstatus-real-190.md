# V5 #190: dev status run against real GitHub data (25 Oct 2026)

Smoke (read-only GET via the existing `gh` login, no write calls): `devstatus.build()` against the public repo
`Jarvis-igerman-ai/jarves` returned 4 open PRs (195-198) and 26 issues (30 listed, 4 of them PRs). Payload shapes are
recorded, trimmed (no bodies, no users), in `core/tests/fixtures/devstatus_real/`; tests: `core/tests/test_v5_devstatus_real.py`.

Real-data bugs found and fixed:
- Cancelled check runs counted as green (PRs 196/197 had 16 cancelled runs incl. "CI result" and showed "green"): new state
  `ci_cancelled`, suggestion "rerun" with an approval-first `gh run rerun` proposal, never a merge proposal.
- Check runs were read with `per_page=50`: real PRs carry 47-50+ checks, so a failure on page 2 was missed. Now `per_page=100`
  and up to 3 pages by `total_count`.
- The old address `igalgerman/jarves` answers 301; the token path (httpx) did not follow it. It follows now, and a 301 that is
  not followed is reported as note `moved`. `DEFAULT_REPO` is now `Jarvis-igerman-ai/jarves`; override with the setting
  `dev.repo` or env `JARVES_DEV_REPO` (nothing else hard-codes the repo in this module).
- Unsupported routes are labelled `unsupported` in the payload and the dashboard (live push, attach to a session; see #161, V6).

Not verified: the dashboard page in a browser (a one-line JS change in `pillars.js`: state label + the unsupported notes).
