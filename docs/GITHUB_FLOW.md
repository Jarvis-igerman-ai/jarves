# GitHub flow (simple + automatic)

0. **Prevent avoidable conflicts before coding:** when 2+ agents may touch shared files, run `python core/tools/pr_overlap.py --files core/src/jarves/dashboard.py core/src/jarves/app.py` after `gh auth login`, or run it without `--files` after editing to compare your current branch and worktree against open `main` PRs. Exit 1 means overlap (coordinate one writer), exit 2 means the preflight was incomplete (do not claim no conflicts). Unlike updating/rebasing every stale PR, this costs **no CI run**. Inspect current `origin/main` before using the branch-diff mode. It warns only about exact same-file edits, not semantic conflicts across files; no claim it eliminates every conflict.
1. Work on a branch and open a **draft** PR to `main`: a draft runs only the fast checks (classify + validation), so
   unfinished pushes do not hold the 20 CI runners all PRs share (free plan). When it is done, run
   `python core/tools/prepush.py`, push, and mark it **ready for review**: the full CI starts. A draft's `CI result` check
   is cancelled on purpose (grey), never green, so nothing can merge from a draft run.
2. Put the PR in the **merge queue** once: `gh pr merge <n> --auto` (or the "Merge when ready" button). When the PR's two
   required checks are green, GitHub's merge queue tests it merged with the latest `main` and the PRs ahead of it (event
   `merge_group`, full matrix, up to 4 PRs per group, 3 groups at a time) and merges it only when that run is green too;
   a failing group is split and the culprit leaves the queue. The push run on `main` then reuses the group's proof
   (~1-2 min). Low-risk PRs (docs, tests, guides, Dependabot patch/minor) get the `automerge` label by themselves and
   `automerge.yml` turns on auto-merge for them. Never merge around the queue (no admin bypass, no direct push).
3. A PR that is only **behind** `main` is not updated and re-tested: merge `main` into it only to resolve a real conflict.
   A conflict in the dashboard translation files (`locales/en.js`, `he.js`): run
   `python core/tools/catalog_add.py --resolve` in the conflicted merge. It merges them text by text, keeps both
   sides and stages them; it names any text that both sides changed differently. These files stay sorted in one
   layout (`catalog_add.py --sort`, a test checks it), so PRs that add different texts rarely touch the same lines.
   The merge queue re-tests it with the current `main` before it lands, so being behind is never a risk. Fix a red
   `main` first: every merge group contains `main`'s failure until it is fixed. A red run on `main` opens
   one issue "main is red: jarves-checks failed" by itself (`main-health.yml`), closed again by the next green run.
   **Red main = fix-forward, never auto-revert (owner decision 24 Oct 2026, #160):** a red main run first re-runs its
   failed jobs once. Still red: the P1 issue names the failing tests and the suspect PRs (merges since the last green
   run, `core/tools/red_main.py`) and each suspect PR gets a hand-off comment; its agent opens a fix PR labelled
   `main-fix`, which goes to the front of the merge queue.
3a. **Merge agent's quick CI/perf check (every PR, no extra pipeline):** On the exact PR head, confirm the two required checks `CI result (single required check)` and `Auto-label policy` are green. Check the existing `Performance gate (Windows)` result **if relevant**; “skipped” means **not measured**, not a pass. Glance at the job summary/slowest shard and compare like-for-like with a recent green run only if readily available. Investigate clear regressions or broken installer/release logic, but do not chase small timing noise. Merge via the protected path, then check the resulting `main` CI and packaging status before reporting success. Do at most a small safe improvement within the approved scope; anything larger goes to the backlog, not another heavy rerun or speculative refactor. An agent should never use admin bypass to circumvent a red/pending required check.
3b. **Daily PR overview (read-only, issue #89):** `python core/tools/pr_triage.py list` groups open PRs into green / waiting / blocked / stale (a draft idle for 7 days; label `keep` exempts). `python core/tools/pr_triage.py label --pr N [--apply]` adds `size/XS..XL` and `area/<x>` labels from the PR file list. It never closes or comments on a PR.
3c. **Flaky tests are show-stoppers:** A flaky test is a show-stopper: when a test fails and then passes on rerun, pause it at once (quarantine it to the non-blocking job, never silently skip a real failure), open an issue labelled `showstopper` (P1) with the failing log and the suspect PR, and fix it as soon as possible, before new feature work on that area; the queue must not stay blocked by it.
4. After the merge, `jarves-checks` runs on `main` and, when green, `jarves-package` makes the signed candidate and the
   beta.

Repository settings (24 Oct 2026, after the move to the organization `Jarvis-igerman-ai`, #161): ruleset
"main: merge queue + required checks" on the default branch: **two** required checks,
`CI result (single required check)` (job `ci-ok`) and `Auto-label policy`, both also reported on `merge_group`;
**Require merge queue** (merge commit, build concurrency 3, up to 4 PRs per group, 2 min wait, 60 min timeout); no
deletion, no force push; organization admins may bypass only through a PR in an emergency. **Allow auto-merge** is
on; branches are deleted on merge. Public repository in a free organization: all of this is free.

## CI gate and auto-label reliability (24 Oct incident)

**Incident history:** Previously only `CI result (single required check)` was required, so a red `Auto-label policy` check did not block native auto-merge. **Both are now required** (verified via GitHub's public `main` branch metadata on 24 Oct); keep both. The repo's own `jarves-automerge` queue additionally checks all completed head check-runs, but native auto-merge and owner/admin merges are separate paths. A failed unrelated check is not proof that the required code checks failed.

**Branch-rule state:** `main` metadata reports both checks as required. The connector cannot edit the branch rule, and `enforcement_level=non_admins` means repository administrators may deliberately bypass it; the agent policy forbids using that escape route for routine merges. A passing workflow is not by itself evidence of local-device sign-off.

`autolabel.yml` now uses the scoped `GITHUB_TOKEN` (Actions permission `Issues: write`, `Pull requests: write`) to ensure the two labels exist. The separate `AUTOMERGE_TOKEN` continues to be used **only by the merge queue** for the push-triggered main CI/package; it need not acquire extra Issues access. Failed label creation is a visible error, never silently swallowed. Documentation, tests and label-only changes do not skip required validation unless the risk classifier explicitly allows it.

Setup once (owner): the repository secret `AUTOMERGE_TOKEN` (fine-grained token, resource owner the organization, this
repo only: Contents, Pull requests and Issues read & write, Actions read). `automerge.yml` uses it to turn on
auto-merge for labelled PRs; the merges themselves are done by the merge queue and start the `main` run as usual.

Why (measured 23 Oct 2026, #89): with 5-8 agent PRs open at once, updating every green PR with `main` before merging
re-ran the full 11-13 minute CI once per other merge (about N squared runs), and a PR took a median of 9 hours from open
to merge. GitHub never required it: before 23 Oct `main` was unprotected and a PR behind `main` was mergeable.
