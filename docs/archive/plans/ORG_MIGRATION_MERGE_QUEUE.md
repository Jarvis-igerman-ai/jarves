# Plan: move Jarvis-igerman-ai/jarves into a free GitHub organization for the native merge queue

Status: **plan only, not executed.** The owner approves this plan and the organization name first (issue linked from
CI reliability E3, #160). Owner choice 24 Oct 2026: option (a), native merge queue after an org move.

## Why

Two green PRs merged minutes apart can break `main` together, because each was tested against an older `main`
(#80 + #107 on 23 Oct: prompt-size ceiling, `main` red 494 min). GitHub's merge queue tests every PR merged with the
latest `main` and the PRs ahead of it before it merges, and bisects a failing group. It is only available for
repositories owned by an **organization** (free for public repositories). A personal repository cannot enable it
(see the comment at the top of `.github/workflows/automerge.yml`).

Speed bonus (E3 speed table, #160): PRs can run the quick gate plus affected tests (~5 min instead of 15.6), the full
matrix runs once per merge group, and the run on `main` always reuses that proof (~1 min, tree identical).

## Decision needed first: organization name

A free org, owner `igalgerman` as the only owner. Candidates: `jarves-ai`, `jarves-app`, `jarveshq` (check
availability on github.com/organizations/plan). The repo keeps its name: `<org>/jarves`.

**Recommendation:** move only the source repo. Keep `igalgerman/jarvis-releases` (release files, `latest.json`) where it
is, so installed apps keep the exact update URL they have today and `RELEASES_TOKEN` keeps working.

## Inventory: what references the repo or its account

| Item | Today | After the move | Action |
|---|---|---|---|
| Repo URL in code (`updates.py` `REPO`, `issue_registry`, `devstatus`, tools `pr_triage`/`pr_overlap`/`hotfix`/`ci_minutes`/`release_status`/`triage_issues`, `jarves.iss` `AppPublisherURL`, bridge `go.mod` module path, tests, docs: ~60 files) | `Jarvis-igerman-ai/jarves` | `<org>/jarves` | one PR: search/replace + `go.mod` module path + tests (`test_repoint.py` asserts `REPO`). GitHub redirects the old URL, so nothing breaks between the move and this PR |
| Installed apps: update check | `api.github.com/repos/Jarvis-igerman-ai/jarves/releases` and release files from `igalgerman/jarvis-releases` | releases repo unchanged; API calls to the old source path get a 301 | `updates.py` uses `httpx.get(..., follow_redirects=True)`: redirects are followed. Release files are unaffected (repo not moved). Verify with `owner_upgrade_check.ps1` on the real PC after the move |
| `jarvis-releases` repo + `RELEASES_TOKEN` | personal repo, fine-grained PAT scoped to it | unchanged | none (if the owner moves it too: new PAT with resource owner = org, update `DEFAULT_RELEASES_REPO` in `updates.py` and `packaging/release.py`, and every installed build follows the GitHub redirect for `latest.json`, but test it on one PC first) |
| `JARVES_UPDATE_SIGNING_KEY` (secret) | repo secret | **transfers with the repo** (repository secrets and variables move with a transferred repository) | verify `gh secret list -R <org>/jarves` shows 3 secrets; if not, re-add from the owner's offline copy |
| `AUTOMERGE_TOKEN` (fine-grained PAT, resource owner `igalgerman`) | merge pushes trigger CI | a personal-owned fine-grained PAT cannot access an org repo | owner creates a new fine-grained PAT with resource owner = org (org setting "allow fine-grained tokens", approve it), Contents RW, Pull requests RW, Issues RW; replace the secret. With the merge queue the queue workflow itself is retired, so the token is only needed by `next-flow` (also retired) and can then be deleted |
| Variable `ENABLE_BETA_PUBLISH` | `true` | transfers | verify |
| Workflows (`uses:` only external actions, `github.repository` used everywhere) | no hard-coded owner except docs | unchanged | grep `.github/` for `igalgerman` (only comments today) |
| Branch protection on `main` (classic rule, 2 required checks) | classic | transfers, but merge queue needs a **ruleset** | create ruleset "main": require PR, required checks `CI result (single required check)` + `Auto-label policy`, **Require merge queue** (merge method: merge commit; build concurrency 3; group size 1-4; wait 2 min; status check timeout 60 min; "only merge non-failing PRs"); bypass: org admin only for emergencies. Then delete the classic rule |
| `jarves-checks.yml` | `push`, `pull_request`, `workflow_dispatch` | add `merge_group: types: [checks_requested]` | the required check must also report on `merge_group` events or the queue waits forever. `changes` classifies `merge_group` like a push to main (full matrix, installer). PR runs then may become light (affected tests); a separate owner decision |
| `autolabel.yml` (`Auto-label policy` required) | `pull_request_target` | must also report on `merge_group` | add a `merge_group` trigger that passes the check (labels were already checked on the PR) |
| `automerge.yml` | custom queue | retired: label `automerge` maps to `gh pr merge --auto` (enters the queue) | delete after one week of queue use |
| `next-flow.yml` | off (`ENABLE_NEXT_FLOW` unset) | redundant | delete |
| `main-health.yml`, `nightly.yml`, `package.yml`, `release.yml`, `bridge-publish.yml`, `codeql.yml` | use `github.repository` | unchanged | verify one run each |
| `codeql.yml` / code scanning, secret scanning, Dependabot | repo settings | transfer with the repo; Dependabot alerts and security settings stay | check Settings > Code security after the move; reopen Dependabot PRs if closed |
| Open PRs and issues | ~11 open PRs, issues #60, #160, ... | transfer with the repo, numbers unchanged; PR branches stay | none; agents' local remotes: `git remote set-url jarves https://github.com/<org>/jarves.git` (old URL keeps redirecting) |
| GitHub Actions minutes | public repo: free | public repo in a free org: free | none |
| Forks, stars, watchers, webhooks | | transfer | re-check webhooks (Telegram, if any) |
| Docs: CLAUDE.md, AGENTS.md, GITHUB_FLOW.md, RELEASING.md, guides TOML (`issue_reports.toml`) | personal URL | org URL | same search/replace PR; guides need English and Hebrew text unchanged except the URL |
| Agent worktrees in `C:/work/wt` | remote `jarves` = old URL | redirect works | set-url at leisure |

**Never re-create a repository named `Jarvis-igerman-ai/jarves` after the move**: that breaks the redirect for every
installed app and every link.

## Steps and order (owner + one agent, ~1 hour, no app downtime)

1. **Before (agent):** wait for an empty queue (no PR with a running check, no run on `main` in progress). Post a
   freeze note on #160. Record `gh secret list`, `gh variable list`, the branch rule (`gh api
   repos/Jarvis-igerman-ai/jarves/branches/main/protection`), webhooks, and the open PR list in the issue.
2. **Owner:** create the free org (`<org>`), enable "Allow fine-grained personal access tokens" (and approval).
3. **Owner:** Settings > General > Transfer ownership > `<org>`. Takes seconds. Old URLs redirect from now on.
4. **Owner:** create the new `AUTOMERGE_TOKEN` (resource owner = org) and replace the secret. Check the 3 secrets
   and the variable are present.
5. **Agent PR 1 (workflow, alone):** `merge_group` trigger in `jarves-checks.yml` and `autolabel.yml`, `changes`
   treats `merge_group` like `main`; tests in `test_ci_workflows.py`. Merge it the old way (protection still classic).
6. **Owner (or agent with admin token):** create the ruleset with "Require merge queue" and the two checks; delete
   the classic rule. Test with one docs PR, then one code PR, then two PRs at once.
7. **Agent PR 2:** URL search/replace (`Jarvis-igerman-ai/jarves` -> `<org>/jarves`, not `jarvis-releases`), `go.mod`
   module path, `jarves.iss`, tests, docs, guides; the bridge pin (`packaging/bridge_pin.json`) only if the
   module path changes the build hash (rebuild and re-pin in the same PR).
8. **Agent PR 3:** retire `automerge.yml` and `next-flow.yml`, update `GITHUB_FLOW.md` (merge = `gh pr merge --auto`
   puts the PR in the queue), `CLAUDE.md` merge rules.
9. **Verify:** one `package.yml` beta publish, `owner_upgrade_check.ps1` on the owner's PC (update found, signature
   ok), `jarvis selftest`. Close the migration issue with the evidence.

Downtime: none for installed apps (release repo not moved, API redirects). CI/merges paused for the ~1 hour of
steps 1-6.

## Rollback

- Before step 6: transfer the repo back to `igalgerman` (Settings > Transfer). Redirects reverse; the secrets move
  back with it; restore the old `AUTOMERGE_TOKEN`.
- After step 6: disable "Require merge queue" in the ruleset (PRs merge as before with `--auto`), keep the org.
- PR 2 (URLs) is a normal revert; the redirect covers both URLs in the meantime.

## Risks

| Risk | Mitigation |
|---|---|
| A required check never reports on `merge_group`, queue stalls | PR 1 before the ruleset; test with a docs PR |
| Full matrix per merge group (~15 min) is slower than today's direct merge | group size up to 4 and build concurrency 3: ~12-16 PRs per hour, more than the agents produce; light PR runs save ~10 min per PR |
| The PAT stops working at the moment of transfer | step 4 directly after step 3; nothing merges in between |
| Updater on old builds hits a 404 | release repo is not moved; API redirect followed (`follow_redirects=True`) |
| Go module path change breaks the reproducible bridge build pin | rebuild + re-pin in PR 2, or keep the module path (it is only a name) |
