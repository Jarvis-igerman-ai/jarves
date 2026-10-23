# GitHub flow (simple + automatic)

1. Work on a branch, push, open a PR to `main`.
2. When you want it merged as soon as CI is green: add the label **`automerge`** (`gh pr edit <n> --add-label automerge`).
3. The `jarves-automerge` workflow (a small merge queue) handles the rest, one PR at a time, oldest first: updates the branch if it is behind `main`, waits for ALL `jarves-checks` jobs to be green, merges, deletes the branch. A failing PR gets a comment and loses the label. Conflicting or draft PRs are skipped.
4. After the merge, `jarves-checks` runs on `main` and, when green, `jarves-package` publishes the signed installer.

Setup once (owner): create a fine-grained token for this repo (Contents: Read & write, Pull requests: Read & write) and save it as the repository secret `AUTOMERGE_TOKEN`. Without it merges still work but main CI and the installer publish are NOT triggered (GitHub does not start workflows for GITHUB_TOKEN pushes).
Why not GitHub's merge queue: it needs a repository owned by an organization; this repository is owned by a personal account.
Repo settings enabled 20 Oct 2026: delete branch on merge.

## State checked 23 Oct 2026 (#89) and the simpler path that is now available

The repository is **public** since the 20 Oct consolidation (earlier text here said private). On a public repository
branch protection, required status checks and GitHub's own **auto-merge** are free. Checked through the API on 23 Oct:
`main` is **not protected** (no required check) and "Allow auto-merge" is **off**, so nothing enforces green CI before a
merge and a PR that is behind `main` can already be merged (PR #78, 5 commits behind, was `clean`).

Measured cost of the current habit of updating every green PR with `main` before merging (#89): with 5-8 agent PRs open
at once each merge makes the others stale and each re-runs the full 11-13 minute CI, so N PRs cost about N squared runs.

Owner action, not done by any agent (settings need admin rights; recommended):

1. Settings > Branches > add a rule for `main`: require the status check **`CI result (single required check)`**
   (job `ci-ok`; docs-only PRs also report it). Leave "Require branches to be up to date before merging" **off**.
2. Settings > General > Pull Requests: turn on **Allow auto-merge**.

Then a PR only needs auto-merge enabled once (`gh pr merge <n> --auto --merge`, or the GitHub MCP `enable_pr_auto_merge`):
GitHub merges it the moment `ci-ok` is green, with no polling and no update-and-re-run. The run on `main` stays the
integration check: when the merged tree was not proven on the PR it runs the full matrix and builds the installer itself
(`docs/CI_PERFORMANCE.md`), and queued `main` runs batch on their own (concurrency group). If `main` turns red, fix forward
before enabling more auto-merges. This changes the merge rule in `CLAUDE.md` ("never while a run on main is in progress",
one at a time), so it needs the owner's decision first.
