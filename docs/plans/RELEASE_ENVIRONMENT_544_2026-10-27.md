# Release workflow: protected `stable-release` environment (plan for #544, 27 Oct 2026)

Status: plan only. No workflow, secret or repository setting is changed by this document. It records the exact change to make
AFTER the code freeze for the 4.5 stable is lifted and the owner has created the environment (owner steps:
[RELEASING](../RELEASING.md), "Owner settings for the stable release").

## Problem (from the review of PR 541, #544)

`release.yml` reads `RELEASES_TOKEN` and `JARVES_UPDATE_SIGNING_KEY` as repository secrets with no environment. Anyone with write
access can start the workflow from any branch with an edited `release.yml`, or push a branch with a workflow that reads them. The
gate runs from the checked-out tree, so it cannot protect against a commit that edits `release.yml`, `packaging/release.py` or
`core/tools/release_gate.py`. #597 and #609 already make a dispatch a dry run unless it starts on a real tag ref and bind the tag to the ref.

## Target design

1. **Two jobs instead of one.**
   - `gate` (today's `stable` job without the publish step): checkout of `github.ref`, gate, installer download and verification,
     publish plan as a dry run. It never references a secret and needs no environment. It uploads the verified `dist/` as a workflow artifact.
   - `publish`: `needs: gate`, `if: github.ref_type == 'tag' && github.event_name == 'push' || (github.event_name == 'workflow_dispatch' && github.ref_type == 'tag' && github.event.inputs.dry_run == 'false')`,
     `environment: stable-release`, permissions `contents: read`. It downloads the artifact, re-checks that the tag still points at the commit
     the gate used (`git rev-parse "refs/tags/$TAG^{commit}"` equals the gate's `--sha`), then runs `public-publish`. Only this job has the two secrets, and
     only as step `env`.
2. **Environment rules (owner, already described in RELEASING step 3):** deployment tags `v*.*.*` only, required reviewer = the owner. A run from a
   branch cannot enter the environment, so it cannot read the secrets even with an edited workflow.
3. **Why two jobs:** the required-reviewer approval is requested when the `publish` job starts, after the gate is green, so the owner approves a
   concrete verified build and not a pending one; and the gate keeps running without his click.
4. **Order of work (do not reverse):** (a) merge the workflow change; (b) the owner adds the two secrets as ENVIRONMENT secrets; (c) the owner
   deletes the repository-level secrets of the same names (RELEASING step 4); (d) the owner runs one dry-run dispatch and one tag test with a
   throwaway tag in a fork-free way (step 5).

## Tests to add with the change (`core/tests/test_ci_workflows.py`)

- no job except `publish` mentions `secrets.`; `publish` has `environment: stable-release` and `needs: gate`;
- `publish.if` requires `github.ref_type == 'tag'`; the dispatch path requires `dry_run == 'false'`;
- the gate job has no `environment` and no secret; the artifact name is the same in upload and download;
- a re-check of the tag commit against the gate's sha exists in `publish` before `public-publish`;
- no `git tag` / `git push` in the file (existing test).

## Risks and open questions

- A required reviewer on `publish` means a tag push waits for his click: intended. If he is unavailable the release waits (safe default).
- The artifact is the verified installer set; the publish step must not rebuild or fetch anything else (the existing "no second build" rule).
- GitHub only enforces environment protection on repositories of a plan that supports it: confirm on the organization plan before step (b).
- The change is a risky workflow change: one PR, label `no-automerge`, independent Opus review, never together with a large merge.
