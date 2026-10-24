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
   A conflict in the dashboard translation files (`locales/en.js`, `he.js`): run
   `python core/tools/catalog_add.py --resolve` in the conflicted merge. It merges them text by text, keeps both
   sides and stages them; it names any text that both sides changed differently. These files stay sorted in one
   layout (`catalog_add.py --sort`, a test checks it), so PRs that add different texts rarely touch the same lines.
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

## CI gate and auto-label reliability (24 Oct incident)

The **required** branch check is `CI result (single required check)`. A separate `jarves-autolabel / Auto-label policy` check may fail even when required CI is green: **GitHub native auto-merge can still merge if that separate check is not configured as required**. The repo's own `jarves-automerge` queue additionally checks all completed head check-runs, but native auto-merge and owner/admin merges are separate paths. A failed unrelated check is not proof that the required code checks failed.

**Owner repository-settings action for strict fail-closed behaviour:** In Settings → Branches → `main` branch protection (or Rulesets), add the exact unique check **`Auto-label policy`** to required status checks alongside **`CI result (single required check)`**, then verify a deliberately failing non-production PR cannot merge (and one green docs-only PR can). The GitHub integration used to edit these source files lacks repository-administration access, so only the owner can confirm or update the branch-rule setting. Do not mark that policy as installed until GitHub's settings are verified. A repo admin bypass remains a deliberate exception until separately disabled by the owner.

`autolabel.yml` now uses the scoped `GITHUB_TOKEN` (Actions permission `Issues: write`, `Pull requests: write`) to ensure the two labels exist. The separate `AUTOMERGE_TOKEN` continues to be used **only by the merge queue** for the push-triggered main CI/package; it need not acquire extra Issues access. Failed label creation is a visible error, never silently swallowed. Documentation, tests and label-only changes do not skip required validation unless the risk classifier explicitly allows it.

Setup once (owner): the repository secret `AUTOMERGE_TOKEN` (fine-grained token for this repo: Contents and Pull requests
read & write). Merges made with `GITHUB_TOKEN` do not start the `main` run or the installer publish.

Why (measured 23 Oct 2026, #89): with 5-8 agent PRs open at once, updating every green PR with `main` before merging
re-ran the full 11-13 minute CI once per other merge (about N squared runs), and a PR took a median of 9 hours from open
to merge. GitHub never required it: before 23 Oct `main` was unprotected and a PR behind `main` was mergeable.
