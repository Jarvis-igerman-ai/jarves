# Security review T11: goal_plans, continuity, devstatus, projectmgr, preferences

Date: 2026-11-09 (findings authored); re-checked 2026-11-21. Every `file:line` reference and every issue state below
is as of that re-check against `origin/main` df23acb (the files cited by line are unchanged since 76cd30c); "at
review time" describes only the old 2026-11-09 behaviour, never a line number.
Scope: `core/src/jarves/{goal_plans,continuity,devstatus,preferences}.py` and `core/src/jarves/projectmgr/`.
Method: a static read of the code paths that touch subprocesses, files, network, SQL, tokens and prompt text. Nothing
was run: every finding is **UNVERIFIED by test** unless it says otherwise.
Not read in full: `projectmgr/{engine,pipeline,surfaces,cards,debate,plan}.py`, and the route and auth layers that call
these modules.
Earlier reviews: `V5_SECURITY_REVIEW_PILLARS_2026-10-25.md` and the V6 review. This report contains no secrets.
Gap: the council review of the first version (PR #979: opus 7, gpt 5 findings) arrived truncated (1 of 12). The other
11 findings never arrived and are **not reflected here** (UNVERIFIED whether they are covered). #979 has no GitHub
review or comment that contains the council text. Recovery from the bridge is tracked in
[#1254](https://github.com/Jarvis-igerman-ai/jarves/issues/1254). This report is incomplete until then; the
recovered text is added here as an addendum.
Disclosure: this repo is public. #1 and #2 were fixed on main by #1101 (JAR-317) and hardened by #1351 (JAR-574); #3
was fixed by #1366 (JAR-575). #4 is open public issue #1099 (its proposed fix in #1108 was closed unmerged).

Severity: P0 exploitable now / data loss; P1 bypass of a gate; P2 real weakness needing a fix; P3 hardening.

## Summary

| # | Module | Sev | Finding | Fix route |
|---|---|---|---|---|
| 1 | projectmgr/gitops | P1 | `.github/` was not in `PROTECTED` | fixed on main by #1101, #1351; #1096 closed |
| 2 | projectmgr/gitops | P2 | `PROTECTED` check was case-sensitive | fixed on main by #1101, #1351; #1097 closed |
| 3 | projectmgr/service | P2 | startup kills stored PIDs without identity check | fixed on main by #1366; #1098 closed |
| 4 | preferences (shared by goal_plans, goals, devmanager) | P2 | `looks_sensitive` misses key/token-shaped text | open, #1099 |
| 5 | projectmgr/gitops | P3 | `remove_worktree` guard is `"projects" in parts` | #1517 |
| 6 | projectmgr/gitops, service | P3 | refs passed to git without `--` / leading `-` check | #1517 |
| 7 | devstatus | P3 | read token reuses `JARVES_UPDATE_TOKEN`; cache holds issue/PR titles in plain JSON | #1517 |
| 8 | continuity | P3 | `store()` and ledger bodies ignore privacy mode | #1517 |
| 9 | goal_plans | P3 | step `args` not sensitivity-checked (title and `why` are) | #1517 |
| 10 | projectmgr/runners | P3 | OpenCode prompt passed in argv | #1517 |
| 11 | projectmgr/gitops | P3 | `CODEOWNERS` outside `.github/` (root, `docs/`) not in `PROTECTED` | #1517 (comment) |
| 12 | projectmgr/gitops | P3 | nested `**/.claude/` (e.g. `sub/.claude/settings.json`) not matched | #1517 (comment) |

## projectmgr

- **#1 P1: `.github/` unprotected** (`PROTECTED`, `gitops.py:21`). At review time, `PROTECTED` did not list
  `.github/`, and `forge.push_branch` pushes `jarves/p<N>/...` branches. The impact is stated in #1096. **Fixed on
  main by #1101 (JAR-317)**: `.github/` was added, and `test_projectmgr_git.py` covers it. #1381 (JAR-600) also counts
  a changed symlink or gitlink as protected (`link_files`, `gitops.py:186`). Remaining gap: #11.
- **#2 P2: case-sensitive protection** (`gitops.py:216-221`). The check was a case-sensitive `startswith`, so on
  Windows/macOS a change to `.CLAUDE/settings.json` reached the same directory without being flagged. **Fixed on main
  by #1101**: `is_protected()` casefolds the path and normalises `\` to `/`; #1351 (JAR-574) also refuses Windows 8.3
  short names (`CLAUDE~1/`). Remaining gap: #12 (only `PROTECTED_NAMES` match in any folder).
- **#3 P2: stale PID kill** (startup loop in `_recover`, `service.py:97-103`). At review time, the stored `pid` of
  every `running/review` task got `taskkill /F /T` (or `killpg`) at startup; after a reboot that PID can belong to an
  unrelated process, and `/T` kills its whole tree. **Fixed on main by #1366 (JAR-575)**: `_kill_owned`
  (`service.py:1299`) kills via `_kill_pid` (`service.py:1313`) only when the stored `pid_start` marker matches the
  live process, and fails closed otherwise.
- **#5 P3: `remove_worktree`** (`gitops.py:154-156`) refuses only paths without a `projects` component, so a path
  such as `C:\Users\x\projects\repo` would still reach `shutil.rmtree`. Current callers build paths under
  `<home>/projects`. Harden with `resolve()` + `is_relative_to(project_root)`.
- **#6 P3: git ref arguments.** The `base`/`branch` strings reach `git diff base...HEAD`, `git merge branch` and
  `worktree add ... start`. `start` is validated with `rev-parse --verify` first (good), and `forge.push_branch` is
  regex-restricted (good). The merge/diff refs are built internally. Add `--end-of-options` or reject a leading `-`
  for defence in depth.
- **#10 P3: OpenCode prompt in argv** (`runners.py:265`). Other local users can see it in the process list, and its
  size is bounded by the OS command-line limit. `--` prevents option injection. The API key is passed only when
  `allow_openrouter` is on. Claude/Codex use stdin.
- **#11 P3: `CODEOWNERS` outside `.github/`.** GitHub also reads `CODEOWNERS` from the repo root and `docs/`; neither
  is in `PROTECTED` or `PROTECTED_NAMES` (`gitops.py:21-23`), so an agent branch could change review ownership there.
- **#12 P3: nested `.claude/`.** `is_protected()` (`gitops.py:216-221`) matches `PROTECTED` prefixes from the repo
  root only, so `sub/.claude/settings.json` is not flagged. Claude Code reads `.claude/` from the folder it starts in.

Good: `child_env` allow-lists the environment (no keys unless named); checks run with `shell=False` and stdin closed;
JARVES never force-pushes and never pushes the default branch; merges are fast-forward only; the agent never runs the
check command.

## devstatus
- **#7 P3**: `_token()` (`devstatus.py:57`) accepts `JARVES_UPDATE_TOKEN` for status reads, so read-only calls use an
  update-publish token with a broader scope. Prefer `GH_TOKEN`/`GITHUB_TOKEN` or a dedicated read token.
  `follow_redirects=True` with a Bearer header (`devstatus.py:70`): httpx drops `Authorization` on cross-origin
  redirects (library behaviour, UNVERIFIED against the pinned version). The cache file `dev_status_cache.json` keeps
  API responses (issue/PR titles) unencrypted in the home folder. Requests are GET-only to a fixed host, with no SQL or
  shell use.

## continuity
- **#8 P3**: SQL is fully parameterised; `execute()` is idempotent per (device, cid). `add_turn`
  (`continuity.py:144`) honours privacy mode, but `store()` (`continuity.py:122`) and the command-ledger `body` do
  not, so reply bodies persist for `LEDGER_TTL_S`. `device`/`cid` are caller-supplied: the route layer must do the
  authentication (not reviewed here).

## goal_plans
- **#9 P3**: plan tools cannot nest (`_RECURSION`), steps and arg sizes are capped, an owner pause/cancel wins (#813),
  and the executor resets the untrusted-content taint each turn. Step `args` are not run through `looks_sensitive`
  (title and `why` are), so a secret pasted as an argument is stored in plain text in the DB. Execution still goes
  through the tool gate.

## preferences
- **#4 P2: `looks_sensitive`** (`preferences.py:42-49`) matches words (password, pin, salary...) and runs of 6+
  digits, but not key/token shapes (`sk-...`, `ghp_...`, `api key`, `token`, `secret`, long base64/hex strings). It
  also guards goals, devmanager drafts and plans, and confirmed preferences are injected into the model instructions,
  so a pasted API key would be stored and later sent to the model. Fix: add a keyword list and a high-entropy-string
  rule, with tests. Positives: proposals need owner confirmation and evidence, expire, are capped, and a model edit
  re-proposes.

## Disposition
One issue per defect (AGENTS.md rule 8):
- P1 #1: [#1096](https://github.com/Jarvis-igerman-ai/jarves/issues/1096). Fixed on main by #1101 (JAR-317) and #1351
  (JAR-574); issue closed.
- P2 #2: [#1097](https://github.com/Jarvis-igerman-ai/jarves/issues/1097). Same fixes; issue closed.
- P2 #3: [#1098](https://github.com/Jarvis-igerman-ai/jarves/issues/1098). Fixed on main by #1366 (JAR-575); issue
  closed.
- P2 #4: [#1099](https://github.com/Jarvis-igerman-ai/jarves/issues/1099). Not fixed on main; issue open. The fix
  proposed in [#1108](https://github.com/Jarvis-igerman-ai/jarves/pull/1108) was closed without merging.
- This report fixes no code. #1099 stays open until a fix merges with evidence.
- P3 #5-#12: hardening notes tracked together in one issue,
  [#1517](https://github.com/Jarvis-igerman-ai/jarves/issues/1517), split from #1254. Its body lists #5-#10;
  #11 and #12 (the remaining gaps of #1 and #2) are recorded in
  [a #1517 comment](https://github.com/Jarvis-igerman-ai/jarves/issues/1517#issuecomment-7068781593). They are not in
  `docs/reviews/FINDINGS_LEDGER.md` because its rules keep `security`-labelled items as issues (like #206). #1254 now
  tracks only the council recovery.
