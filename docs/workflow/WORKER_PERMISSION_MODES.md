# Worker permission modes: evaluation (issue #768)

Checked 2 Nov 2026 against the official docs; installed `claude --version` = 2.1.316.
Sources: [permission modes](https://code.claude.com/docs/en/permission-modes),
[agent view / `claude agents`](https://code.claude.com/docs/en/agent-view).
Evaluation only: this document changes no permission or setting.

## Modes (official, 2 Nov 2026)

| Mode | Runs without asking | Fit for a headless worker |
| --- | --- | --- |
| `default` | reads only | everything else denied under `-p` (v2.1.302+) |
| `acceptEdits` | reads, edits and mkdir/touch/mv/cp/rm/rmdir/sed in the working directory | today's choice; other Bash needs an allow rule, otherwise denied |
| `plan` | reads (edits blocked) | review-only tasks |
| `auto` | everything, a classifier model reviews each action | broad allow rules are dropped on entry; a model, not a rule, decides |
| `dontAsk` | reads plus pre-approved tools; anything that would prompt is denied (also ask rules and AskUserQuestion); flag only | the explicit allow-list mode |
| `bypassPermissions` | everything (allow rules have no effect; deny rules still apply) | never for JARVES workers |

Facts that matter: deny rules apply in every mode; allow rules and PreToolUse hooks returning allow still work
in `dontAsk`; protected paths (`.git`, `.claude`, `.mcp.json`) are never auto-approved except in bypass; since
v2.1.302 `-p` with no mode starts in `default` and denies instead of hanging (older versions could silently
start in auto). Cloud sessions ignore `defaultMode` dontAsk/bypass from settings files.

## What the autopilot launches today

`core/src/jarves/orchestrator/claude.py` (`ManagedClaude`): `claude -p --input-format stream-json ...
--permission-mode acceptEdits` (`WORKER_PERMISSION_MODE`; the code accepts only `default` or `acceptEdits`,
anything else falls back to `default`), `--allowedTools WORKER_ALLOWED_TOOLS` (the narrow list from PR #748:
pytest/ruff, read-only shell, local git, `gh pr|issue|run`, a few `jarvis` status commands;
replaceable by `worker_allowed_tools` in `autopilot.json`) and `--disallowedTools WORKER_DENY` (git push/tag,
gh release, gh pr merge, rm, jarvis hotfix/release/update, schtasks, taskkill, powershell, autopilot.json, and
the run-time write/exec options in `WORKER_WRITE_FLAG_DENY`, #1311). Autopilot is off by
default and needs the owner's grant. `autopilot_loop.py` only decides when to start a worker.

## Run-time matcher check (#1316, #1311)

Checked on 19 Nov 2026 with the real matcher: `claude` 2.1.327, model `claude-haiku-5-5`, `-p --setting-sources
project` in an empty temp git repo. Allow rules `Bash(ls:*)`, `Bash(git diff:*)`, `Bash(git log:*)`, `Bash(git rebase:*)`,
`Bash(pytest:*)`, `Bash(gh run:*)`, plus the new deny rules. The model was told to run each command once, exactly as
written. After the run the files were listed on disk, and the stand-in `autopilot.json` in a second temp dir stayed `{}`.

| Command (allow rule `Bash(ls:*)` unless noted) | `default` | `acceptEdits` |
| --- | --- | --- |
| `ls` | allowed | - |
| `ls > f`, `ls >> f` (f in the working dir) | refused | allowed (same as an edit in the worktree) |
| `ls > <outside>/f`, `ls >> <outside>/autopilot.json` | refused | refused ("outside the working directories") |
| `ls \| tee f` | refused (tee needs approval) | refused |
| `ls; echo hi > f`, `ls && echo hi > f` | refused | refused (outside path) |
| `` ls $(...) ``, ``ls `...` `` | refused ("nested command can't be checked") | refused |
| `git diff --output=f`, `git log --output=f` | denied by `Bash(*--output*)` | denied |
| `git rebase --exec "..." HEAD` | denied by `Bash(git rebase*--exec*)` | - |
| `pytest --basetemp=`, `-o cache_dir=`, `--junitxml=` | - | denied |
| `gh run download 1 -D <outside>` | - | denied |
| `git diff HEAD` | allowed | - |

Result: a prefix rule does not admit redirection, `tee`, `;`/`&&` chains or `$(...)`/backticks writing outside the
worktree, and the deny rules catch the write options. `core/tests/test_worker_owner_commands_932.py` holds a hermetic
model of OUR deny rule set (fnmatch on the whole command), not of Claude Code. Re-run this check when Claude Code
changes its Bash matcher.

Added after the review of PR #1349, **not yet run against the real matcher (UNVERIFIED)**; only the hermetic model
covers them. Add them to the next real run: `ruff check . -o <outside>`, `pytest -o log_file=<outside>`,
`pytest -qo log_file=<outside>`, `pytest --override-ini=log_file=<outside>`, `pytest --debug=<outside>`,
`pytest --pastebin=all`, `ruff format <outside>/x.py`, `ruff check --fix ../x`, `git rebase -x'sh x' main`.
`Bash(git worktree:*)` is narrowed to `Bash(git worktree list:*)`, so `git worktree add <outside>` is not allowed at all.
Whether Claude Code path-checks plain path arguments of `ruff format`/`--fix` by itself is also UNVERIFIED, so the deny
list refuses absolute, `..`, `~` and drive-letter paths after `ruff`.

Residual risk of the deny-glob approach: a glob list cannot list every spelling. Known gaps: a ruff short-option
cluster with an attached path (`ruff check -eo/abs/x`), pytest ini writers other than `log_file`/`cache_dir` given in a
`-qo` cluster, and plugin options of pytest plugins installed later. These sit next to the pytest/conftest item below,
which already allows arbitrary code; OS isolation is the real fix.

Residual risk, needs an owner decision (#1311): `pytest` runs test code and `conftest.py` that the worker writes in its
worktree. That is arbitrary code running as the owner's user, so it could rewrite `autopilot.json` no matter what
the allow list says. The default list cannot both run the worker's tests and stop this. The only real fixes are OS
isolation (a separate user or a container) or no tests in workers. `git commit/merge/rebase` run only the hooks the owner
installed (they live outside the worktree, which a worker cannot edit, and `git config` is not allowed).

## Comparison

- `acceptEdits` + allow + deny (today): works. The mode itself also approves mv/cp/sed edits in the worktree;
  `rm *` stays blocked because deny wins.
- `dontAsk` + the same allow + deny: strictly narrower. File edits are not approved by the mode, so Edit/Write
  must be listed explicitly, or the worker cannot change files. It can never prompt or hang, and ask rules
  become denies. Gain: the allow-list is the whole authority and easy to audit. Cost: list upkeep and a risk
  of denied-edit stalls.
- `auto`: rejected for workers (a model decides; repeated blocks pause it; it drops the broad rules we use).
- `bypassPermissions` / `--dangerously-skip-permissions`: rejected. Docs say containers and VMs only; it would
  skip approvals.

## `claude agents --json` for the Health tab

Documented as the supported read interface (not `~/.claude/jobs/`). Fields: `state`
(working/blocked/done/failed/stopped), `status` (busy/waiting/idle), `waitingFor` (permission prompt, input
needed, sandbox request, worker request, dialog open), `id`, `sessionId`, `cwd`. It lists sessions started with
`claude --bg`; our `claude -p` stream-json children are probably not listed (UNVERIFIED). It is a research
preview, so use it read-only, pin to the documented fields and treat absence as unknown. It maps to the
watchdog classes (stalled, permission wait, offline) as a second signal. Not needed while JARVES already reads
its own workers' events.

## Merge queue note (second part of #768)

Verified in the repo: `merge_group` triggers exist in `.github/workflows/autolabel.yml` and `jarves-checks.yml`.
UNVERIFIED: the required-check list in branch protection and the build-concurrency setting (needs a GitHub
settings read).

## Installed-product QA from a sandboxed worker (#856)

A worktree-isolated worker cannot run `powershell.exe`, `cmd.exe` or `jarvis.cmd` (the sandbox cannot show they
do not run git). The silent beta install is therefore a manager-run step: the QA task states "install: manager",
and the worker continues with read-only checks once the manager reports the installed version. Betas are not in
the source repo: they are releases `beta-v4.0.1.NNNN` of `igalgerman/jarvis-releases`. Without the manager step
the worker records an UNVERIFIED blocker instead of guessing.

## Recommendation

1. Keep `acceptEdits` + allow + deny now. It is safe: no bypass, deny wins, unanswered prompts are denied.
2. Pilot `dontAsk` first for read-mostly autopilot tasks (steward, reviews, QA, verify): their allow-list needs
   no edit rights, so risk drops with no stall cost.
3. Extend to code workers only after a week of the pilot without denied-edit stalls, with explicit
   `Edit`/`Write` allow rules and the unchanged WORKER_DENY.
4. Never `auto` or bypass for workers. Widening the allow-list or the accepted-mode set in `claude.py` is a
   Tier R change (autopilot code): Opus review and the owner's grant.

Rollout: accept `dontAsk` behind an `autopilot.json` key, default unset (= today's behaviour); enable per task
type; watch watchdog stalls. Rollback: remove the key; workers return to `acceptEdits` on the next start.

Open before any pilot: run a harmless `claude -p --permission-mode dontAsk --allowedTools Read` on the installed
2.1.316 (not done here).
