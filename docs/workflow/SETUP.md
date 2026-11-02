# Run the workflow console

Creating this PR does not install it on the owner's PC. HTTP/browser validation
and native Claude/Windows acceptance are separate evidence.

## First run from the updated Windows checkout

Use Python 3.12+; the direct entry point is standard-library-only and works with
the Core off. First inspect the local migration, then explicitly apply it:

```powershell
python core/tools/workflow_console.py bootstrap --repo-root .
python core/tools/workflow_console.py bootstrap --repo-root . --apply
python core/tools/workflow_console.py supervise
```

If `python` is the Microsoft Store alias (`...\WindowsApps\python.exe`), use a real interpreter
path instead, such as `py -3.12` or the venv python. The hooks and the worker context already
embed the exact interpreter and script path that bootstrap ran with, double-quoted with forward slashes (Claude Code runs hooks through Git Bash on Windows). Observer hooks that
return nothing to Claude (prompt, tool, notification, session end, subagent stop) are
installed with `"async": true`, so they never block a tool call; SessionStart, SubagentStart
and Stop stay synchronous. A tool call in flight (for example a long test run) keeps its
worker alive for up to 15 minutes without new hook events. The supervisor reads public
GitHub metadata every 30 minutes (the unauthenticated limit is 60 requests/hour).

Apply checks Claude 2.1.271+, backs up changed instructions, removes only recognised
standalone retired workflow rules, preserves unrelated lines/settings and sibling hooks, merges observer
hooks and locally ignores `CLAUDE.local.md` / `.claude/settings.local.json`.
It does not change global Claude settings, permissions or experimental teams.
Review warnings for ambiguous legacy wording in the dry-run; it is retained for
manual reconciliation instead of deleting surrounding instructions.
Restart Claude and check `/memory`; inspect additional global instructions there
for stale JARVES rules. In worktrees confirm the imports/hooks actually load.

In another terminal:

```powershell
python core/tools/workflow_console.py open
python core/tools/workflow_console.py unlock   # 4-digit code to answer in the dashboard (#750)
```

The private URL contains a local access key; do not share it. The service binds
only `127.0.0.1:8188`, not a phone endpoint. After installing this code with
JARVES, `jarvis workflow ...` is equivalent. For custom state use the same
`--home PATH` for every command.

## Observer outside Claude

```powershell
powershell -File core/tools/workflow_service.ps1 -Install -PythonExe (Get-Command python).Source
```

The installer refuses a `WindowsApps` interpreter and prefers `pythonw.exe` beside the
chosen `python.exe`, so logon opens no console window.

This explicitly installs/starts a per-user Task Scheduler task. Bootstrap does
not install it. Verify on the PC: close Claude and check the observer; stop the
owned web child and check restart; stop the supervisor and check OS recovery.
It never starts Claude sessions or types into arbitrary terminal windows.

```powershell
powershell -File core/tools/workflow_service.ps1 -Remove
```

Removal keeps private data and existing worker sessions.

## How to use it with Claude

For the owner-facing sequence, diagrams and simplicity decisions read
[USER_JOURNEY.he](USER_JOURNEY.he.md). Explicit external runs use the existing
`jarvis agents start PROVIDER PROJECT "task" --model MODEL`; no additional
launcher is installed. Those agent interfaces need the running Core and their
existing account/project/permission prerequisites.
The merge steward decides per cycle between single merges and one integration PR with `core/tools/integration_branch.py` (`plan`, `build`, `close-superseded`); see OPERATING_MODEL "Merge steward".

## Action history and a manual retrospective

Open **פעולות והיסטוריה** or use:

```powershell
python core/tools/workflow_console.py trace
python core/tools/workflow_console.py trace --capture details
python core/tools/workflow_console.py trace --agent AGENT_ID --task TASK_ID
python core/tools/workflow_console.py retro --agent AGENT_ID
```

Overview counters open the matching list. The roadmap supports version/state
filters, search and expandable version sections; each task opens its acceptance,
evidence and history links. All four tabs remain available on narrow screens.
Action details show readable facts with raw JSON collapsed. Custom date/time
ranges and older-page navigation freeze history for analysis; the selected end
second is inclusive. Live mode returns to a relative range. CLI time filters
use Unix seconds: `trace --since SECONDS --until SECONDS`. The prepared retro
prompt has a copy button, with manual selection if clipboard access is unavailable.

Capture defaults to metadata. Details apply only to future events and are
best-effort redacted. History is bounded to 7 days / 20,000 events; retro export
is limited to 5,000 and explicitly reports truncation. The export and invocation
prompt stay under the private workflow directory. No model or skill executes.
Install/verify the upstream `/retro` and its `writing-for-agents` dependency on
the owner PC through the owner's normal agent setup, after reviewing its source;
this PR does not install them or edit global settings. Then invoke `/retro`
explicitly with the prepared selected log. Rule/skill changes require selection.

Native hooks record their actual events. Existing Orchestrator providers mirror
their normalized timeline only when the default `<profile>/workflow/workflow.db`
exists; some providers have partial detail. A custom workflow `--home` does not
automatically receive that provider mirror. Optional telemetry uses short SQLite
timeouts and may drop an event during contention rather than stall an agent;
the Core logs `AGENTS_WORKFLOW_TRACE_UNAVAILABLE`. Do not claim complete capture.

## Native worker assignment and evidence

The view imports 94 real scope requirements and public GitHub metadata. No
workers appear until actual hooks/registration report; proof gaps stay visible.

Give Claude this first task:

> Read AGENTS.md and docs/NOW.md. Manage the workflow ledger with native
> subagents. Reconcile the next current-version task, delegate its exact ID,
> and keep implementation, evidence and delivery current. When I am away,
> retain questions and continue independent authorised work. Show the result
> in the live view; a worker return or PR merge is not verification.

Native hooks inject actual worker identity/home. Semantic updates use the CLI:

```text
python core/tools/workflow_console.py next --agent WORKER --task TASK
python core/tools/workflow_console.py report --agent WORKER --progress --note "Reproduced; focused checks running"
python core/tools/workflow_console.py ask --task TASK --title "Which behavior?" --checkpoint COMMIT
```

An unanswered task stays pending; the manager gives its safely released worker
other eligible work. Answer in the Questions tab. The answer/checkpoint is
included when the original task resumes.

Evidence protocol (use actual IDs and tested revision/artifact):

```text
python core/tools/workflow_console.py transition --task TASK --code-revision SHA --state review
python core/tools/workflow_console.py check --task TASK --criterion C1 --evidence "Run URL; target and any fakes"
python core/tools/workflow_console.py transition --task TASK --validation app --state verified
python core/tools/workflow_console.py transition --task TASK --delivery beta --artifact-ref BUILD_OR_HASH --state done
```

All criteria must pass at the same revision; new code clears old proof. Use
`check --failed` for a regression. Imported scope requires explicit operator
`authorize --task ID` after scope/dependency reconciliation; this does not grant
runtime permissions. Recover quarantine only after inspecting the original
process/worktree: `recover --task ID --checkpoint "writer stopped; state reconciled"`.

Codex can register with `agent --agent ID --lane ...` and use the same protocol;
automatic Codex hooks are not claimed. Optional existing-orchestrator delivery
requires an already running allow-listed session and explicit
`--session-id ID --auto-dispatch`; it starts no sessions and bypasses no gates.

## Troubleshooting

- No live view: run supervise and open a fresh private link.
- Unknown rows: scope exists but reconciliation/evidence is still missing.
- No work: inspect owner wait, dependencies, resources, scope and worker alerts.
- GitHub unavailable: cached metadata remains visible with its age/error.
- Present expired: select it again; polling does not count as owner activity.
- Native stop without handoff: inspect the result and explicitly recover or
  record evidence. Never duplicate a potentially live writer.

## Autopilot (beta, off by default, unverified on the owner PC)

Nothing starts until the owner opts in: `jarvis workflow autopilot status` shows the policy
(`enabled=false`), `start` enables it and `stop` is the kill switch (no new starts; running
workers finish or checkpoint). Launching workers also needs the owner start grant typed at the
approval popup (valid up to 365 days, default 365; `autopilot grant`, `grant-status`, `autopilot revoke` ends it at once).
The grant always needs the typed code: in the popup when the Core runs, in the terminal itself when no Core runs and a TTY exists,
and it is refused (never downgraded to a plain yes) when neither is available. The `kill` switch also ends the grant at once.
A start that gets no answer is looked up in the Core: a live session is adopted and tracked, otherwise the task is quarantined.
The watchdog re-queues a stalled task only after the stop is confirmed. Alerts (`lane_idle`, `worker_restarted`, `task_blocked`,
`permission_wait`) show in the console and `status`; a permission wait over 10 minutes sends one
away alert. While you are away, tool permission prompts still wait for you, so most lanes will
wait; a safe project allowlist in `.claude/settings.json` is proposed by agents and applied by the
owner. UNVERIFIED: real worker start with Claude models, hook env under Git Bash, away popup
behaviour, Task Scheduler restart, silent beta install chain.

## Autopilot self-update

When the autopilot is enabled the supervisor follows the synced main SHA: on a change it runs `git fetch jarves` and
`git merge --ff-only` on a clean flow checkout, then restarts itself once (a detached successor takes the supervisor lock;
ledger, policy and running worker sessions are kept). A dirty or non-fast-forwardable checkout raises one dashboard
alert and nothing is reset. Workers always start in a new worktree cut from `jarves/main`. The autopilot never merges
pull requests or tags.
