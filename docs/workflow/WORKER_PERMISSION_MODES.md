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
python/pytest/ruff/node/npm, read-only shell, local git, `gh pr|issue|run`, a few `jarvis` status commands;
replaceable by `worker_allowed_tools` in `autopilot.json`) and `--disallowedTools WORKER_DENY` (git push/tag,
gh release, gh pr merge, rm, jarvis hotfix/release/update, schtasks, taskkill, powershell). Autopilot is off by
default and needs the owner's grant. `autopilot_loop.py` only decides when to start a worker.

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
