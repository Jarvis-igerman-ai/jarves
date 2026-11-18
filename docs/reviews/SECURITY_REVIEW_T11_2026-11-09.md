# Security review T11: goal_plans, continuity, devstatus, projectmgr, preferences

Date: 2026-11-09. Every file:line reference below was re-checked on 2026-11-18 against `origin/main` d6e1c58.
Scope: `core/src/jarves/{goal_plans,continuity,devstatus,preferences}.py` and `core/src/jarves/projectmgr/`.
Method: static read of the code paths that touch subprocesses, files, network, SQL, tokens and prompt text. Nothing
was exercised at runtime: every finding is **UNVERIFIED by test** unless it says otherwise.
Not read in full: `projectmgr/{engine,pipeline,surfaces,cards,debate,plan}.py` and the route/auth layers that call
these modules. Earlier reviews: `V5_SECURITY_REVIEW_PILLARS_2026-10-25.md`, the V6 review. No secrets are included.
Gap: the council review of the first version (PR #979: opus 7, gpt 5 findings) arrived truncated (1 of 12). The other
11 findings are not on GitHub and are **not reflected here** (UNVERIFIED whether covered). Recovering them, and filing
the P3 items, is tracked in [#1323](https://github.com/Jarvis-igerman-ai/jarves/issues/1323).
Disclosure: this repo is public. #1-#2 are fixed on main; #3-#4 are stated in public issues #1098/#1099.

Severity: P0 exploitable now / data loss; P1 bypass of a gate; P2 real weakness needing a fix; P3 hardening.

## Summary

| # | Module | Sev | Finding | Status / route |
|---|---|---|---|---|
| 1 | projectmgr/gitops | P1 | `.github/` was not in `PROTECTED` | fixed on main by JAR-317 (#1101); #1096 |
| 2 | projectmgr/gitops | P2 | `PROTECTED` check was case-sensitive | fixed on main by JAR-317 (#1101); #1097 |
| 3 | projectmgr/service | P2 | startup kills stored PIDs without identity check | open, #1098 |
| 4 | preferences (shared by goal_plans, goals, devmanager) | P2 | `looks_sensitive` misses key/token text | open, #1099 |
| 5 | projectmgr/gitops | P3 | `remove_worktree` guard is `"projects" in parts` | follow-up |
| 6 | projectmgr/gitops, service | P3 | refs passed to git without `--` / leading `-` check | follow-up |
| 7 | devstatus | P3 | read token may be `JARVES_UPDATE_TOKEN`; cache holds titles in plain JSON | follow-up |
| 8 | continuity | P3 | `store()` and ledger bodies ignore privacy mode | follow-up |
| 9 | goal_plans | P3 | step `args` not sensitivity-checked (title and `why` are) | note |
| 10 | projectmgr/runners | P3 | OpenCode prompt passed in argv | note |

## projectmgr

- **#1 P1: `.github/` unprotected.** At review time `PROTECTED` did not list `.github/`. Now fixed: `.github/` is in
  `PROTECTED` (`gitops.py:21-22`), commit 9e44446 (JAR-317). #1096 is still open; closing it is the issue owner's call.
- **#2 P2: case-sensitive protection.** Now fixed: `is_protected` casefolds the path and normalises slashes
  (`gitops.py:178-181`), commit 9e44446. #1097 is still open, same as above.
- **#3 P2: stale PID kill.** Startup loop in `_recover` (`service.py:100-103`) calls `_kill_pid` (`service.py:1299-1309`)
  for every `running/review` task's stored `pid`: `taskkill /F /T` on Windows, `killpg` elsewhere. After a reboot or a
  long gap the PID can belong to an unrelated process, and `/T` kills its tree. Fix: store the process start time with
  the PID and verify it before killing. `if t["pid"]` correctly avoids pid 0. Still open on main.
- **#5 P3: `remove_worktree`** (`gitops.py:137`) refuses only paths without a `projects` component, so for example
  `C:\Users\x\projects\repo` would reach `shutil.rmtree`. Current callers build paths under `<home>/projects`.
  Harden with `resolve()` plus `is_relative_to(project_root)`.
- **#6 P3: git ref arguments.** `base`/`branch` strings reach `git diff base...HEAD` (`gitops.py:157,162`) and
  `git merge branch` (`gitops.py:198`). Worktree branches are checked with `rev-parse --verify` (`gitops.py:107`) and
  `forge.push_branch` is regex-restricted (`forge.py:73`). Add `--end-of-options` or reject a leading `-`.
- **#10 P3: OpenCode prompt in argv** (`runners.py:265`): visible to other local users in the process list and bounded
  by the OS command-line limit. `--` prevents option injection. Claude/Codex receive the prompt on stdin.

Good: `child_env` allow-lists the environment (`gitops.py:53`); runners use `shell=False` with stdin closed unless a
prompt is piped; JARVES never force-pushes or pushes the default branch.

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
- P1 #1: [#1096](https://github.com/Jarvis-igerman-ai/jarves/issues/1096). Code fixed on main (9e44446, JAR-317).
- P2 #2: [#1097](https://github.com/Jarvis-igerman-ai/jarves/issues/1097). Code fixed on main (9e44446, JAR-317).
- P2 #3: [#1098](https://github.com/Jarvis-igerman-ai/jarves/issues/1098). Open; not fixed by this report-only PR.
- P2 #4: [#1099](https://github.com/Jarvis-igerman-ai/jarves/issues/1099). Open; not fixed by this report-only PR.
- PR #1108 (JAR-376), which proposed fixes for all four, was closed without merging.
- P3 #5-#10: hardening notes, no issue each; recorded here and in [#1323](https://github.com/Jarvis-igerman-ai/jarves/issues/1323).
