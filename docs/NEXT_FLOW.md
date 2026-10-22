# The `next` integration-branch flow

Status: **built, OFF**. The `next` branch and the workflows exist; the default PR base is still `main`. Source: plan
`docs/plans/RELEASE_AND_TEST_SYSTEM.md` section 5.1 (owner decisions of 22 Oct 2026).

## How it works

```
feature PR -> next   light CI (jarves-next-flow): fast validation + only the affected test files
   |  merge (automerge label, once ENABLE_NEXT_FLOW is on)
   v
 next  -- push --> batch run (jarves-checks, full matrix, queued, never cancelled)
   |  green
   v  bot fast-forwards main to that exact commit (same tree)
 main  -- push --> jarves-checks finds the proof (no re-run) --> jarves-package (candidate / beta)
```

* **Light PR CI** (`.github/workflows/next-flow.yml`, job `CI result (single required check)`, same name as the batch
  check): `tests/test_ci_*`, docs links and `test_next_flow`, then `core/tools/ci_affected.py` selects the test files whose
  import closure contains a changed `jarves` module (plus data-file rules: guides, dashboard, Hebrew, docs, packaging).
  Lock file, workflow, CI tool or `conftest.py` changes, or the PR label `full-ci`, select everything. Real-browser files,
  Windows-only shards and the installer run in the batch. Selection is by deselection of files, never skips.
* **Batch run**: every push to `next` runs `jarves-checks` (full matrix + installer). Runs of `main` and `next` queue and
  are never cancelled; GitHub keeps only one pending run per group, so pushes arriving during a run batch up.
* **Fast-forward** (`fast-forward-main`): after a green batch on `next`, `main` is pushed to the batch commit with a plain
  `git push` using `AUTOMERGE_TOKEN` (so the follow-up workflows start). It refuses non-fast-forward (for example a
  hotfix that landed on `main`: merge `main` into `next` first).
* **Automerge into next** (`automerge-into-next`): PRs labelled `automerge` with a green light run on their exact head are
  merged into `next`, oldest first. No "branch must be up to date" requirement, so a moving `next` does not re-run PRs.
* PRs into `next` do not run the full `jarves-checks` (it has `branches-ignore: [next]`).

## Switch on / off

```
gh variable set ENABLE_NEXT_FLOW --body true      # on: fast-forward and automerge into next are active
gh variable delete ENABLE_NEXT_FLOW               # off (default)
```

Then retarget: set the repository default branch to `next` (Settings > Branches) or open PRs with `--base next`. Without
the variable, PRs into `next` still get the light CI, but nothing merges or fast-forwards automatically.

## Branch protection (NOT enabled yet; decisions for the owner)

* `main`: require the check `CI result (single required check)`; allow the bot token (`AUTOMERGE_TOKEN` owner) to push
  without a PR (only fast-forwards happen); no "require branches to be up to date".
* `next`: require `CI result (single required check)`; do not require up-to-date branches; allow merge commits.
* `AUTOMERGE_TOKEN` is a fine-grained PAT with Contents RW and Pull requests RW (see `GITHUB_FLOW.md`).

## Rollback

Turn the variable off. `main` is always a proven commit, so nothing needs undoing. To stop using `next`, delete it and
PR to `main` again (the old `automerge.yml` queue still works for PRs into `main`).
