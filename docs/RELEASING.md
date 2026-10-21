# Releasing JARVES

Three layers. Only the first runs without the owner.

1. **Candidate (every green `main`)**: `.github/workflows/package.yml` takes the installer that `jarves-checks` built and
   smoke-tested (silent install, upgrade, uninstall) and builds a **signed candidate** (installer + signed manifest) as a
   workflow artifact, kept 30 days. Nothing is published.
2. **BETA channel (every green `main`, off by default)**: the same workflow publishes `JARVES X.Y.Z-beta.N` to
   `igalgerman/jarvis-releases` (pre-release `beta-v<build>`, plus the signed `latest-beta.json` on the rolling pre-release
   `beta-channel`) **only when the repository variable `ENABLE_BETA_PUBLISH` is `true`**. Unset or any other value = nothing
   is published. Needs the secrets `RELEASES_TOKEN` and `JARVES_UPDATE_SIGNING_KEY` (docs/RELEASE_HOSTING.md). The newest 5
   betas are kept. A re-run of the same commit publishes nothing new. Stable users are never offered a beta.
3. **STABLE (tag-driven, gated)**: push a tag `vX.Y.Z`; `.github/workflows/release.yml` runs `core/tools/release_gate.py`
   and publishes the tested installer (the one built from the signed-off commit) as the stable release only if the gate passes.

## Choosing the channel (users)

`jarvis update --channel beta` (or `stable`), the **Updates** dashboard page (button, English and Hebrew), or
`jarvis features on beta_channel`. A beta build shows **BETA 4.1.0-beta.N** in `jarvis status`, in the dashboard header
and in the footer. Going back: `jarvis update --channel stable`, and `jarvis update --rollback` restores the previous version.

## The stable gate (`core/tools/release_gate.py`)

All of these must hold, else the tag is refused:

- the tag is `vX.Y.Z` and the installer is `X.Y.Z.<build>`;
- the SHA being published is the verified SHA and its `jarves-checks` run on `main` is green;
- the release checklist has no open P0-P2 (and optional bug tables list none);
- the `nightly` workflow was green on the last 5 scheduled nights;
- the owner's **manual test sign-off** `docs/reviews/MANUAL_TEST_<X.Y.Z>.md` is complete and signed, and the tested beta
  commit equals the tag commit, or is an ancestor with **only documentation changes** since.

Run it yourself: `python core/tools/release_gate.py --tag v4.1.0 --sha <tag sha> --candidate-dir <dist>`.

## Manual test sign-off

1. Generate the sheet: `python core/tools/manual_test_sheet.py --version 4.1.0` (rows from the CHANGELOG entries, the guides
   and dashboard pages changed since the last stable tag, plus the standing owner rows: install, voice, approvals,
   update/rollback, health, privacy). Commit it.
2. Install the latest beta (`jarvis update --channel beta`) and go through the rows.
3. Record each result: `jarvis release signoff --version 4.1.0 --row C1 --result pass` (or `fail`, `blocked`,
   `accepted-blocked --note "why"`), or use the dashboard page **Release sign-off** (English and Hebrew).
4. When every row is `pass` or `accepted-blocked` with a note: `jarvis release signoff --version 4.1.0 --finish`. This writes
   the tested beta version, its commit SHA and the date into the sheet. Commit it (docs only), then tag.

Any later change to a row voids the signature.

## Rollback

`python core/tools/release_rollback.py --to win-v4.0.0.12` prints the plan (**dry run is the default**). Add `--execute`
(needs `RELEASES_TOKEN`) to make that release the latest stable and demote newer ones to pre-releases; `--channel beta --to
beta-v4.1.0.55` re-points `latest-beta.json`. The target's signed manifest is verified with the keys built into the app first.
Installed copies run `jarvis update --rollback`. Fix forward with a higher version.

Dry runs of the publishers: `python packaging/release.py beta-publish --dir dist --dry-run`, same for `public-publish`.

## CI shape

Windows Python tests run in 3 shards (`tests/conftest.py --shard I/N`, balanced by test count), Linux in one job; the
real-browser files (`test_browser.py`, `test_canary.py`, `test_dashboard_a11y.py`) are their own job with one retry. No test
may be skipped. Superseded runs of the same ref are cancelled; uv, Go modules, Playwright and tool downloads are cached;
docs-only changes skip the heavy jobs. CodeQL (Python) runs weekly and on core changes; Dependabot (pip, gomod,
github-actions) weekly; see `SECURITY.md`.

Required status check names changed with the sharding: `Python Core tests (windows-2025 shard 1/3)` ... `3/3`,
`Python Core tests (ubuntu-24.04 shard 1/1)`, `Real-browser tests (...)`.
