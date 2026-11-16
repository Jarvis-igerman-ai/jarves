# Security review T11: goal_plans, continuity, devstatus, projectmgr, preferences

Date: 2026-11-09 (line references re-checked against `origin/main` on 2026-11-14). Scope: `core/src/jarves/{goal_plans,continuity,devstatus,preferences}.py` and `core/src/jarves/projectmgr/`.
Method: static read of the code paths that touch subprocesses, files, network, SQL, tokens and prompt text, at base
`origin/main`. Nothing was exercised at runtime: every finding is **UNVERIFIED by test** unless it says otherwise.
Not read in full: `projectmgr/{engine,pipeline,surfaces,cards,debate,plan}.py`, route/auth layers that call these modules.
Earlier reviews: `V5_SECURITY_REVIEW_PILLARS_2026-10-25.md`, V6 review. No secrets are included here.
Gap: the council review of the first version (PR #979: opus 7, gpt 5 findings) was received truncated (1 of 12); the
other 11 findings were not received and are **not reflected here** (UNVERIFIED whether covered).
Disclosure: this repo is public. #1-#2 describe unfixed paths already stated in public issues #1096/#1097; fixes are
proposed in #1108 (not merged).

Severity: P0 exploitable now / data loss; P1 bypass of a gate; P2 real weakness needing a fix; P3 hardening.

## Summary

| # | Module | Sev | Finding | Fix route |
|---|---|---|---|---|
| 1 | projectmgr/gitops | P1 | `.github/` is not in `PROTECTED` | Tier R, proposed in #1108, #1096; see disposition |
| 2 | projectmgr/gitops | P2 | `PROTECTED` check is case-sensitive | Tier R, proposed in #1108, #1097; see disposition |
| 3 | projectmgr/service | P2 | startup kills stored PIDs without identity check | proposed in #1108 (Tier R), #1098; see disposition |
| 4 | preferences (shared by goal_plans, goals, devmanager) | P2 | `looks_sensitive` misses key/token-shaped text | proposed in #1108 (Tier R), #1099; see disposition |
| 5 | projectmgr/gitops | P3 | `remove_worktree` guard is `"projects" in parts` | follow-up |
| 6 | projectmgr/gitops, service | P3 | refs passed to git without `--` / leading `-` check | follow-up |
| 7 | devstatus | P3 | read token reuses `JARVES_UPDATE_TOKEN`; cache holds issue/PR titles in plain JSON | follow-up |
| 8 | continuity | P3 | `store()` and ledger bodies ignore privacy mode | follow-up |
| 9 | goal_plans | P3 | step `args` not sensitivity-checked (title and `why` are) | note |
| 10 | projectmgr/runners | P3 | OpenCode prompt passed in argv | note |

## projectmgr

- **#1 P1: `.github/` unprotected** (`gitops.py:19`). `PROTECTED` lists `.claude/ .codex/ .opencode/ .git/ .githooks/
   opencode.json .mcp.json .gitmodules` but not `.github/`. `forge.push_branch` pushes `jarves/p<N>/...` branches, so an
   agent-edited workflow file can run in CI with the repo's secrets once pushed (trigger-dependent: UNVERIFIED which
   workflows run on push of such branches). Proposal: add `.github/` (and `CODEOWNERS`) to `PROTECTED`. **Tier R
   (workflows/secrets): proposed only, not implemented here.**
- **#2 P2: case-sensitive protection** (`gitops.py:176`). `f.startswith(PROTECTED)` is case-sensitive; on Windows/macOS
   a change to `.CLAUDE/settings.json` addresses the same directory but is not flagged. Proposal: compare
   `f.lower()` and also match nested `**/.claude/`. **Tier R (gate): proposed only.**
- **#3 P2: stale PID kill** (startup loop in `_recover` at `service.py:100-103`, `_kill_pid` at `service.py:1298-1307`; both re-checked). On startup every `running/review` task's stored `pid` gets
   `taskkill /F /T` (or `killpg`). After a reboot or long gap the PID can belong to an unrelated process, and `/T`
   kills its tree. Fix: store process start time with the PID and verify it (or only kill PIDs started by this
   process). `if t["pid"]` correctly avoids pid 0.
- **#5 P3: `remove_worktree`** (`gitops.py:133`) refuses only paths without a `projects` component, so e.g.
   `C:\Users\x\projects\repo` would pass `shutil.rmtree`. Current callers build paths under `<home>/projects`.
   Harden with `resolve()` + `is_relative_to(project_root)`.
- **#6 P3: git ref arguments.** `base`/`branch` strings reach `git diff base...HEAD`, `git merge branch`,
   `worktree add ... start`. `start` is validated with `rev-parse --verify` first (good), `forge.push_branch` is
   regex-restricted (good). Merge/diff refs are internally built. Add `--end-of-options`/reject leading `-` for defence in depth.
- **#10 P3: OpenCode prompt in argv** (`runners.py:263`): visible to other local users via process list and bounded by
    the OS command-line limit. `--` prevents option injection. The API key is only passed when `allow_openrouter` is on. Claude/Codex use stdin.

Good: `child_env` allow-lists the environment (no keys unless named); checks run `shell=False` with stdin closed;
JARVES never force-pushes or pushes the default branch; merges are fast-forward only; the agent never runs the
check command.

## devstatus
- **#7 P3**: `_token()` accepts `JARVES_UPDATE_TOKEN` for status reads, so an update-publish token (broader scope) is
   used for read-only calls; prefer `GH_TOKEN`/`GITHUB_TOKEN` or a dedicated read token. `follow_redirects=True` with a
   Bearer header: httpx drops `Authorization` on cross-origin redirects (library behaviour, UNVERIFIED against the pinned version).
   The cache file `dev_status_cache.json` stores API responses (issue/PR titles) unencrypted in the home folder.
   Requests are GET-only to a fixed host. No SQL or shell use.

## continuity
- **#8 P3**: SQL is fully parameterised; `execute()` is idempotent per (device, cid). `add_turn` honours privacy mode
   but `store()` and the command-ledger `body` do not, so reply bodies persist for `LEDGER_TTL_S`. `device`/`cid`
   are caller-supplied: authentication must happen in the route layer (not reviewed here).

## goal_plans
- **#9 P3**: plan tools cannot nest (`_RECURSION`), steps capped, args size-capped, owner pause/cancel wins (#813),
   and the executor resets the untrusted-content taint per turn. Step `args` are not run through `looks_sensitive`
   (title and `why` are), so a secret pasted as an argument is stored in plain text in the DB. Execution still goes through the tool gate.

## preferences
- **#4 P2: `looks_sensitive`** (`preferences.py:41-48`) matches words (password, pin, salary...) and 6+ digit runs, but
   not key/token shapes (`sk-...`, `ghp_...`, `api key`, `token`, `secret`, long base64/hex strings). It also guards
   goals, devmanager drafts and plans, and confirmed preferences are injected into the model instructions. A pasted
   API key would be stored and later sent to the model. Fix: add a keyword list plus a high-entropy-string rule, with tests.
   Positives: proposals need owner confirmation and evidence, expire, are capped, and a model edit re-proposes.

## P0-P2 disposition
One issue per defect (AGENTS.md rule 8):
- P1 #1: [#1096](https://github.com/Jarvis-igerman-ai/jarves/issues/1096). Tier R (workflows/secrets): **proposed only**; a PR touching `PROTECTED` needs the owner's decision.
- P2 #2: [#1097](https://github.com/Jarvis-igerman-ai/jarves/issues/1097). Tier R (gate, `no-automerge`): **proposed only**, same reason.
- P2 #3: [#1098](https://github.com/Jarvis-igerman-ai/jarves/issues/1098). Fix **not done in this task** (report-only; this PR is Tier R by label); fix proposed in #1108 (below).
- P2 #4: [#1099](https://github.com/Jarvis-igerman-ai/jarves/issues/1099). Fix **not done in this task** (report-only; this PR is Tier R by label); fix proposed in #1108 (below).
- Fix status (2026-11-16): fixes for #1096-#1099 are **proposed** in open PR [#1108](https://github.com/Jarvis-igerman-ai/jarves/pull/1108) (JAR-376, Tier R, not merged; its diff touches `PROTECTED`/`is_protected`, `_kill_pid` with a process identity token, and `looks_sensitive`), pending the owner/council decision (required for #1096 and #1097). The issues stay open until a fix merges with evidence.
- P3 #5-#10: hardening notes, no issue each; track in `docs/reviews/FINDINGS_LEDGER.md` when picked up.
