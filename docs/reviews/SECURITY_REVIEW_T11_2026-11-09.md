# Security review T11: goal_plans, continuity, devstatus, projectmgr, preferences

Date: 2026-11-09. Every file:line reference below was re-checked on 2026-11-20 against `origin/main` 3bed837.
Line numbers are valid only at commit 3bed837 (main moves on); check one with
`git show 3bed837:core/src/jarves/<file> | sed -n '<line>p'`.
Scope: `core/src/jarves/{goal_plans,continuity,devstatus,preferences}.py` and `core/src/jarves/projectmgr/`.
Method: static read of the code paths that touch subprocesses, files, network, SQL, tokens and prompt text. Nothing
was exercised at runtime: every finding is **UNVERIFIED by test** unless it says otherwise.
Not read in full: `projectmgr/{engine,pipeline,surfaces,cards,debate,plan}.py` and the route/auth layers that call
these modules. Earlier reviews: `V5_SECURITY_REVIEW_PILLARS_2026-10-25.md`, the V6 review. No secrets are included.
Gap: the council review of the first version (PR #979: opus 7, gpt 5 findings) arrived truncated (1 of 12). The other
11 findings are not on GitHub and are **not reflected here** (UNVERIFIED whether covered). Recovering them, and filing
the P3 items, is tracked in [#1323](https://github.com/Jarvis-igerman-ai/jarves/issues/1323).
Disclosure: this repo is public. #1-#3 are fixed on main; #4 is stated in public issue #1099. P3 #5-#10 are
published as hardening notes, as earlier reviews in `docs/reviews/` do; whether to keep them public is a council call.

Severity: P0 exploitable now / data loss; P1 bypass of a gate; P2 real weakness needing a fix; P3 hardening.

## Summary

| # | Module | Sev | Finding | Status / route |
|---|---|---|---|---|
| 1 | projectmgr/gitops | P1 | `.github/` was not in `PROTECTED` | fixed (JAR-317, JAR-574); #1096 closed |
| 2 | projectmgr/gitops | P2 | `PROTECTED` check was case-sensitive | fixed (JAR-317, JAR-574); #1097 closed |
| 3 | projectmgr/service | P2 | startup killed stored PIDs without identity check | fixed (JAR-575); #1098 closed |
| 4 | preferences (shared by goal_plans, goals, devmanager) | P2 | `looks_sensitive` misses key/token text | open, #1099 |
| 5 | projectmgr/gitops | P3 | `remove_worktree` guard is `"projects" in parts` | follow-up |
| 6 | projectmgr/gitops, service | P3 | refs passed to git without `--` / leading `-` check | follow-up |
| 7 | devstatus | P3 | read token may be `JARVES_UPDATE_TOKEN`; cache holds titles in plain JSON | follow-up |
| 8 | continuity | P3 | `store()` and ledger bodies ignore privacy mode | follow-up |
| 9 | goal_plans | P3 | step `args` not sensitivity-checked (title and `why` are) | note |
| 10 | projectmgr/runners | P3 | OpenCode prompt passed in argv | note |

## projectmgr

- **#1 P1: `.github/` unprotected.** At review time `PROTECTED` did not list `.github/`. Now fixed: `.github/` is in
  `PROTECTED` (`gitops.py:21-22`), commit 9e44446 (JAR-317); JAR-574 (4a972ce) also lists renamed and non-ASCII
  paths. #1096 is closed.
- **#2 P2: case-sensitive protection.** Now fixed: `is_protected` casefolds the path, normalises slashes and refuses
  8.3 short names (`gitops.py:186-191`), commits 9e44446 and 4a972ce/4104af7. #1097 is closed.
- **#3 P2: stale PID kill.** At review time the startup loop in `_recover` called `_kill_pid` for every
  `running/review` task's stored `pid` (`taskkill /F /T` or `killpg`), so a reused PID could take an unrelated process
  tree. Now fixed by JAR-575 (7e2692b): `_recover` calls `_kill_owned` (`service.py:103`), which kills only when the
  pid and its recorded start marker match the live process and otherwise logs `PID_NOT_KILLED`
  (`service.py:1299-1310`); `_kill_pid` itself is `service.py:1313-1323`. #1098 is closed.
- **#5 P3: `remove_worktree`** (`gitops.py:137`) refuses only paths without a `projects` component, so any folder
  with such a component passes the guard. Current callers build paths under `<home>/projects`.
  Harden with `resolve()` plus `is_relative_to(project_root)`.
- **#6 P3: git ref arguments.** `base`/`branch` strings reach `git diff base...HEAD` (`gitops.py:160,165`) and
  `git merge branch` (`gitops.py:208`). Worktree branches are checked with `rev-parse --verify` (`gitops.py:107`) and
  `forge.push_branch` is regex-restricted (`forge.py:73`). Add `--end-of-options` or reject a leading `-`.
- **#10 P3: OpenCode prompt in argv** (`runners.py:265`): visible to other local users in the process list and bounded
  by the OS command-line limit. `--` is inserted before the prompt on that same line (`argv += ["--", prompt]`,
  `runners.py:265`), so the prompt is not parsed as an option (from reading only). Claude/Codex receive the prompt on stdin.

Good: `child_env` allow-lists the environment (`gitops.py:53`); runners start the tool without a shell, with stdin
closed unless a prompt is piped (`runners.py:107`; no `shell=True` in `projectmgr/`). The only push in `projectmgr/` is
`forge.push_branch` (`forge.py:71-75`): a plain `git push origin <branch>:<branch>` with no `--force`, allowed only
for `jarves/p<N>/...` branch names, so the default branch is never pushed. The name guard is tested by
`test_projectmgr.py::test_forge_status_issues_pr_and_checks` (`main` and `../` are refused); the absence of
`--force` is from reading the code only.

## devstatus

- **#7 P3**: `_token()` tries `JARVES_UPDATE_TOKEN` first (`devstatus.py:58`), so an update-publish token (broader
  scope) can be used for read-only calls; prefer `GH_TOKEN`/`GITHUB_TOKEN` or a dedicated read token. The request uses
  `follow_redirects=True` with a Bearer header (`devstatus.py:70`); httpx drops `Authorization` on cross-origin
  redirects (UNVERIFIED against the pinned version). `dev_status_cache.json` (`devstatus.py:305`) stores issue/PR
  titles unencrypted in the home folder. Requests are GET-only to a fixed host.

## continuity

- **#8 P3**: SQL is parameterised. `add_turn` honours privacy mode (`continuity.py:147`) but `store()`
  (`continuity.py:122`) does not, so reply bodies persist for `LEDGER_TTL_S` (24 h, `continuity.py:40`).
  `device`/`cid` are caller-supplied: authentication must happen in the route layer (not reviewed here).

## goal_plans

- **#9 P3**: plan tools cannot nest (`_RECURSION`, `goal_plans.py:58,130`); title and `why` go through
  `looks_sensitive` (`goal_plans.py:115,135`), step `args` do not, so a secret pasted as an argument is stored in plain
  text. Execution still goes through the tool gate.

## preferences

- **#4 P2: `looks_sensitive`** (`preferences.py:42-49`) matches words (password, pin, salary...) and 6+ digit runs,
  but not key/token shapes (`sk-...`, `ghp_...`, `api key`, `token`, `secret`, long base64/hex strings). It also
  guards goals, devmanager drafts and plans, and confirmed preferences are injected into the model instructions, so a
  pasted API key could be stored and later sent to the model. Fix: add a keyword list plus a high-entropy rule, with
  tests. Still open on main.

## P0-P2 disposition

One issue per defect (AGENTS.md rule 8):
- P1 #1: [#1096](https://github.com/Jarvis-igerman-ai/jarves/issues/1096), closed. Fixed on main (9e44446, 4a972ce).
- P2 #2: [#1097](https://github.com/Jarvis-igerman-ai/jarves/issues/1097), closed. Fixed on main (9e44446, 4a972ce).
- P2 #3: [#1098](https://github.com/Jarvis-igerman-ai/jarves/issues/1098), closed. Fixed on main (7e2692b, JAR-575).
- P2 #4: [#1099](https://github.com/Jarvis-igerman-ai/jarves/issues/1099). Open; not fixed by this report-only PR.
- PR #1108 (JAR-376), which proposed fixes for all four, was closed unmerged; later tickets fixed #1-#3.
- P3 #5-#10: hardening notes, no issue each; recorded here and in [#1323](https://github.com/Jarvis-igerman-ai/jarves/issues/1323).
