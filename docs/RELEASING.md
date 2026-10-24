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

## Stable release, step by step

Every green `main` is already a beta (layer 2). A stable release promotes a beta that the owner tested:

1. Pick the version: `version` in `core/pyproject.toml` (for example `4.0.1`); the tag will be `v4.0.1`.
2. Add `docs/reviews/RELEASE_CHECKLIST_<version>.md` (rows `| # | item | status |`, every row `done`, `n/a` or
   `accepted`); it records the bug-table state. Only 4.0.0 used the old `SWV4_RELEASE_CHECKLIST.md`.
   Fold the one-file-per-change entries into their base files in the release PR: `python core/tools/catalog_add.py --fold`
   (dashboard texts, `locales/parts`) and `python core/tools/changelog_build.py --write` (`changelog.d` into CHANGELOG.md).
3. Make the sign-off sheet (`manual_test_sheet.py`, below), install the newest beta, test it and sign it off
   (`jarvis release signoff ... --finish`); commit the signed sheet.
4. Wait for 5 green nightly runs in a row on `main`.
5. Dry run: Actions > `jarves-release` > Run workflow with the tag and `dry_run` ticked (or
   `python core/tools/release_gate.py --tag v<version> --sha <commit>`); it lists everything still missing.
6. Tag the signed-off commit and push the tag (`git tag v<version> <sha> && git push origin v<version>`):
   `release.yml` re-checks the gate and publishes the tested installer as the stable release.

## The stable gate (`core/tools/release_gate.py`)

All of these must hold, else the tag is refused:

- the tag is `vX.Y.Z` and the installer is `X.Y.Z.<build>`;
- the SHA being published is the verified SHA and its `jarves-checks` run on `main` is green;
- the release checklist has no open P0-P2 (and optional bug tables list none). Bug tables (`release.yml` passes
  `docs/reviews/*BUG_TABLE*.md`) are read by each table's header row: a `Sev`/`Severity`/`Priority` column and a
  `Status`/`State` column (or `Retest`/`Result`, where a non-empty retest without "fail"/"open"/"pending" counts as closed);
  a status starting with fixed, closed, accepted, wontfix, duplicate, done, resolved, verified or pass is closed. Tables
  without a severity column are ignored, and every problem names its file. A new bug table must keep these columns
  (`test_every_bug_table_in_the_repo_has_a_status_the_gate_can_read`);
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

## What is the state of the release? `release_status.py`

```
python core/tools/release_status.py            # needs `gh auth login`; --json for scripts; exit 1 = something blocks
```

Prints, for the head of `main`: the latest `jarves-checks` run, the installer artifact of the head tree (and which run
proved it), the `jarves-package` run and the signed candidate artifact, the newest beta and stable releases, whether beta
publishing is on, the stable gate, and a **BLOCKING** list (main CI red or running, no proven installer, no candidate, ...).
The installer is looked up by the docs-free content tree (`ci_reuse.api_tree`, from the git-trees API, the same address
CI uses), not by the commit's git tree sha. What the token cannot read is shown as **UNKNOWN**, never as "none"/"off": the
releases repository, and `ENABLE_BETA_PUBLISH` (reading variables needs admin rights; the tool then takes the answer from
whether the last `jarves-package` run's `beta` job ran or was skipped).

## Release candidate on demand

`gh workflow run package.yml -f ref=main` (or any commit or branch) makes a signed candidate, and a beta when
`ENABLE_BETA_PUBLISH` is `true`, from the installer that CI already built and smoke-tested for that commit's tree. It never
builds; if no green run built that tree it fails with the reason. Stable still needs the tag and the manual sign-off.

## How the installer gets from the PR to the release (no second build)

The installer is built and smoke-tested **on the PR**, in parallel with the tests, and uploaded as
`jarves-installer-<tree>` (tree = git tree without documentation). When the merge commit has the same tree, `main` runs no
tests and builds nothing; `jarves-package` (beta/candidate) and `release.yml` (stable, tag) download that artifact, check
the SHA256 and the tree (`release.py verify --tree`), and stamp the commit and channel into `build-info.json` and the signed
manifest (`release.py stamp`). The `.exe` bytes and its version (`X.Y.Z.<run number of the tested build>`) are unchanged.
A different tree falls back to the full matrix on `main`. Details, numbers and limits: `docs/CI_PERFORMANCE.md`.

## CI shape

Windows Python tests run in 3 shards planned from measured per-file durations (`core/tools/ci_plan.py`, rebalanced after
each full run on main), Linux in one job; inside a shard pytest-xdist runs files in parallel (`--dist loadfile`). The
real-browser files (`BROWSER_FILES` in `core/tests/conftest.py`: browser, canary, dashboard a11y, chat markdown, phone e2e) are their own job with one retry. Known
socket/load flakes re-run only the failed files once and are listed in `flake-report.json`. No test may be skipped.
Superseded runs of the same PR or ref are cancelled; branch pushes have no second run; uv, Go modules, Playwright and tool
downloads are cached; docs-only changes skip all jobs and guides-only changes run the Linux shard only. CodeQL (Python) runs
weekly and on core changes; Dependabot (pip, gomod, github-actions) weekly, patch and minor updates auto-merge when green
(`dependabot-automerge.yml`, kill switch `DEPENDABOT_AUTOMERGE=false`, majors never); see `SECURITY.md`.

Branch protection: require **`CI result (single required check)`**. The individual names remain valid: `Python Core tests
(windows-2025 shard 1/3)` ... `3/3`, `Python Core tests (ubuntu-24.04 shard 1/1)`, `Real-browser tests (ubuntu-24.04)`,
`Real-browser tests (windows-2025)`, `Windows installer (build + silent install smoke)`. Full list: `docs/CI_PERFORMANCE.md`.

## Automatic labelling of low-risk PRs (`autolabel.yml`, `core/tools/risk_class.py`)

Low-risk PRs get the `automerge` label by themselves; the queue (`automerge.yml`) then merges them when
`CI result (single required check)` is green. Nothing is bypassed: no label ever skips a check, a conflicting PR waits, and
nothing merges while the latest finished `jarves-checks` run on `main` is red. A PR that is only behind `main` is not
updated and re-tested (owner decision 23 Oct 2026, #89): the run on `main` after the merge is the integration check (runs
on `main` queue, batch and are never cancelled while running).

| Class | Rule |
| --- | --- |
| docs | only `docs/`, `*.md`, `CHANGELOG*`, `DOC_MAP.md` |
| tests | only `core/tests/test_*.py` (plus docs) |
| guides | only `core/src/jarves/guides/data/*.toml` and dashboard locale / translation catalogs (plus docs) |
| dependabot | patch and minor of pip, gomod, npm, github-actions get `automerge` (`dependabot-automerge.yml`); major gets `needs-review` |
| bugfix | label `bugfix`, fewer than 150 changed lines, no high-risk path (only when `ENABLE_AUTOLABEL=all`) |

**High-risk paths never get the label** (even with the `bugfix` label): `.github/**`, `packaging/**`, `CLAUDE.md`,
`AGENTS.md`, `core/pyproject.toml`, `uv.lock`, approvals / helper / isolation / winsandbox / mobile / secrets /
dashboard_auth / privacy / permissions / updates / signing / security / redact modules under `core/src/jarves/`, release,
promote and CI tooling (`core/tools/release*`, `promote*`, `ci_*`, `risk_class.py`), `core/tests/conftest.py`. The labels
`needs-review`, `no-automerge`, `do-not-merge` and `wip` block the merge. The label is withdrawn when a later push makes the
PR stop qualifying (only if `autolabel.yml` added it: marker label `automerge-auto`).

Switch: `gh variable set ENABLE_AUTOLABEL --body all` (also bugfix PRs), `--body false` (off), unset or `true` (default:
docs, tests, guides). Dependabot has its own switch `DEPENDABOT_AUTOMERGE=false`. The workflow never checks out PR code.
