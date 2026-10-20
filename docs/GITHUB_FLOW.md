# GitHub flow (simple + automatic)

1. Work on a branch, push, open a PR to `main`.
2. When you want it merged as soon as CI is green: add the label **`automerge`** (`gh pr edit <n> --add-label automerge`).
3. The `jarves-automerge` workflow (a small merge queue) handles the rest, one PR at a time, oldest first: updates the branch if it is behind `main`, waits for ALL `jarves-checks` jobs to be green, merges, deletes the branch. A failing PR gets a comment and loses the label. Conflicting or draft PRs are skipped.
4. After the merge, `jarves-checks` runs on `main` and, when green, `jarves-package` publishes the signed installer.

Setup once (owner): create a fine-grained token for this repo (Contents: Read & write, Pull requests: Read & write) and save it as the repository secret `AUTOMERGE_TOKEN`. Without it merges still work but main CI and the installer publish are NOT triggered (GitHub does not start workflows for GITHUB_TOKEN pushes).
Why not GitHub's merge queue: it needs a Pro/Team/Enterprise account or an organization; this repo is private, personal and on the free plan.
Repo settings enabled 20 Oct 2026: delete branch on merge. (Auto-merge needs required checks = branch protection = Pro, so it stays off.)
