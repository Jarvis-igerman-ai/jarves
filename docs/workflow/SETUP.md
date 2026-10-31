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
```

The private URL contains a local access key; do not share it. The service binds
only `127.0.0.1:8188`, not a phone endpoint. After installing this code with
JARVES, `jarvis workflow ...` is equivalent. For custom state use the same
`--home PATH` for every command.

## Observer outside Claude

```powershell
powershell -File core/tools/workflow_service.ps1 -Install -PythonExe (Get-Command python).Source
```

This explicitly installs/starts a per-user Task Scheduler task. Bootstrap does
not install it. Verify on the PC: close Claude and check the observer; stop the
owned web child and check restart; stop the supervisor and check OS recovery.
It never starts Claude sessions or types into arbitrary terminal windows.

```powershell
powershell -File core/tools/workflow_service.ps1 -Remove
```

Removal keeps private data and existing worker sessions.

## How to use it with Claude

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
