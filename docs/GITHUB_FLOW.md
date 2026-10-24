# GitHub flow (simple + automatic)

0. **Prevent avoidable conflicts before coding:** when 2+ agents may touch shared files, run `python core/tools/pr_overlap.py --files core/src/jarves/dashboard.py core/src/jarves/app.py` after `gh auth login`, or run it without `--files` after editing to compare your current branch and worktree against open `main` PRs. Exit 1 means overlap (coordinate one writer), exit 2 means the preflight was incomplete (do not claim no conflicts). Unlike updating/rebasing every stale PR, this costs **no CI run**. Inspect current `origin/main` before using the branch-diff mode. It warns only about exact same-file edits, not semantic conflicts across files; no claim it eliminates every conflict.
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
3a. **Merge agent's quick CI/perf check (every PR, no extra pipeline):** On the exact PR head, confirm the two required checks `CI result (single required check)` and `Auto-label policy` are green. Check the existing `Performance gate (Windows)` result **if relevant**; “skipped” means **not measured**, not a pass. Glance at the job summary/slowest shard and compare like-for-like with a recent green run only if readily available. Investigate clear regressions or broken installer/release logic, but do not chase small timing noise. Merge via the protected path, then check the resulting `main` CI and packaging status before reporting success. Do at most a small safe improvement within the approved scope; anything larger goes to the backlog, not another heavy rerun or speculative refactor. An agent should never use admin bypass to circumvent a red/pending required check.
4. After the merge, `jarves-checks` runs on `main` and, when green, `jarves-package` makes the signed candidate and the
   beta.

Repository settings (verified 24 Oct 2026): `main` is protected with **two** required checks,
`CI result (single required check)` (job `ci-ok`; docs-only PRs report it too) and `Auto-label policy`, "Require branches to be up to date" is
**off**, administrators may bypass in an emergency; **Allow auto-merge** is on; branches are deleted on merge.
The repository is public, so these are free; GitHub's merge queue would need an organization-owned repository.

## CI gate and auto-label reliability (24 Oct incident)

**Incident history:** Previously only `CI result (single required check)` was required, so a red `Auto-label policy` check did not block native auto-merge. **Both are now required** (verified via GitHub's public `main` branch metadata on 24 Oct); keep both. The repo's own `jarves-automerge` queue additionally checks all completed head check-runs, but native auto-merge and owner/admin merges are separate paths. A failed unrelated check is not proof that the required code checks failed.

**Branch-rule state:** `main` metadata reports both checks as required. The connector cannot edit the branch rule, and `enforcement_level=non_admins` means repository administrators may deliberately bypass it; the agent policy forbids using that escape route for routine merges. A passing workflow is not by itself evidence of local-device sign-off.

`autolabel.yml` now uses the scoped `GITHUB_TOKEN` (Actions permission `Issues: write`, `Pull requests: write`) to ensure the two labels exist. The separate `AUTOMERGE_TOKEN` continues to be used **only by the merge queue** for the push-triggered main CI/package; it need not acquire extra Issues access. Failed label creation is a visible error, never silently swallowed. Documentation, tests and label-only changes do not skip required validation unless the risk classifier explicitly allows it.

Setup once (owner): the repository secret `AUTOMERGE_TOKEN` (fine-grained token for this repo: Contents and Pull requests
read & write). Merges made with `GITHUB_TOKEN` do not start the `main` run or the installer publish.

Why (measured 23 Oct 2026, #89): with 5-8 agent PRs open at once, updating every green PR with `main` before merging
re-ran the full 11-13 minute CI once per other merge (about N squared runs), and a PR took a median of 9 hours from open
to merge. GitHub never required it: before 23 Oct `main` was unprotected and a PR behind `main` was mergeable.
