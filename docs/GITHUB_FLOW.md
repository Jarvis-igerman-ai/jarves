# GitHub flow (simple + automatic)

1. Work on a branch and open a **draft** PR to `main`: a draft runs only the fast checks (classify + validation), so
   unfinished pushes do not hold the 20 CI runners all PRs share (free plan). When it is done, run
   `python core/tools/prepush.py`, push, and mark it **ready for review**: the full CI starts. A draft's `CI result` check
   is cancelled on purpose (grey), never green, so nothing can merge from a draft run.
2. Turn on the PR's **auto-merge** once: `gh pr merge <n> --auto --merge` (or the "Enable auto-merge" button, or the
   GitHub MCP `enable_pr_auto_merge`). GitHub merges it the moment the required check
   **`CI result (single required check)`** is green, and never before. Low-risk PRs (docs, tests, guides, Dependabot
   patch/minor) get the `automerge` label by themselves and the `jarves-automerge` queue merges them the same way.
3. A PR that is only **behind** `main` is not updated and re-tested: merge `main` into it only to resolve a real conflict.
   Its run tested the merge with `main` as it was; the run on `main` after the merge is the integration check (the full
   matrix when the merged tree was not proven on the PR; queued `main` runs batch on their own). While the latest run on
   `main` is red nothing else is merged (the queue stops by itself); fix `main` first. A red run on `main` opens
   one issue "main is red: jarves-checks failed" by itself (`main-health.yml`), closed again by the next green run.
4. After the merge, `jarves-checks` runs on `main` and, when green, `jarves-package` makes the signed candidate and the
   beta.

Repository settings (owner, 23 Oct 2026, #89): `main` is protected with the required check
`CI result (single required check)` (job `ci-ok`; docs-only PRs report it too), "Require branches to be up to date" is
**off**, administrators may bypass in an emergency; **Allow auto-merge** is on; branches are deleted on merge.
The repository is public, so these are free; GitHub's merge queue would need an organization-owned repository.

Setup once (owner): the repository secret `AUTOMERGE_TOKEN` (fine-grained token for this repo: Contents and Pull requests
read & write). Merges made with `GITHUB_TOKEN` do not start the `main` run or the installer publish.

Why (measured 23 Oct 2026, #89): with 5-8 agent PRs open at once, updating every green PR with `main` before merging
re-ran the full 11-13 minute CI once per other merge (about N squared runs), and a PR took a median of 9 hours from open
to merge. GitHub never required it: before 23 Oct `main` was unprotected and a PR behind `main` was mergeable.
