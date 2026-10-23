# Project Manager — JARVES manages a software project with many coding agents

Owner request, 23 Oct 2026. Status: built (core, CLI, voice tools, dashboard page). Reviewed by an Opus subagent before coding; the review changed the design as noted in "Review changes" at the end.

## Idea in one paragraph

The owner opens a software project (a git folder). JARVES becomes the project's manager: it splits the goal into
**small tasks**, gives each task to one short-lived coding agent (Claude Code, Codex, OpenCode, OpenRouter models), runs
independent tasks **in parallel** in their own git worktrees, and passes work forward by **handoff** (a short written
summary plus the merged code). No agent ever holds a long session: every task is a fresh, small context window. The
owner talks to the manager: status, new task, update task, deep dive, talk to an agent, pause, retry.

## What exists already (reused, not rewritten)

- `orchestrator/` manages *live* Claude Code / Codex sessions the owner started (list, ask, tell, approvals, allow-list
  in `policy.Projects`, one writer per folder, forbidden flags, worktree helper). Unchanged.
- `devmanager.py` = local drafts for JARVES's own development (not a project manager). Unchanged.
- `goal_plans.py` = goals executed as plans of Skills (not coding agents). Unchanged.
- Skill/tool pattern, `features.register`, CLI parity, guides, `hebrew.TEXT`, dashboard pages, approvals popup.

## Design

Package `core/src/jarves/projectmgr/`, feature `project_manager` (default OFF, risk medium), Skill `project`, CLI
`jarvis project ...`, dashboard page "Projects". Data in `<home>/projects.db` (SQLite, own file).

### Model
- **Project**: id, name, path (must be a git repo), goal, status (`active|paused|done|archived`), integration branch
  `jarves/p<ID>`, check command (argv list, optional, e.g. `["python","-m","pytest","-q"]`), brief (<= 800 chars: the
  project's conventions, taken from `AGENTS.md`/`CLAUDE.md` head when present, owner-editable), limits.
- **Task**: id, project, seq, title, spec (<= 1200 chars), acceptance (<= 300 chars), scope (<= 8 path globs), deps
  (task ids), status, runner, model, attempts, branch, worktree, session ref, handoff (<= 800 chars), result,
  cost, timestamps. Status machine:
  `proposed -> todo -> ready -> running -> review -> done`, side states `blocked`, `failed`, `cancelled`.
  `ready` = all deps `done`. `review` = agent finished and the check passed (or no check configured) and the task is
  waiting for the merge step; `done` = merged into the integration branch.
- **Event**: task timeline (who: owner / manager / agent name, kind, text), content-free of secrets (redacted).

### Small tasks and small contexts (the core rules, enforced in code)
1. A plan is proposed by a **planner** (a read-only agent run asked for strict JSON, or the owner's own list). The
   validator rejects: more than 12 tasks per plan, spec > 1200 chars, > 8 scope globs, dependency cycles, unknown deps,
   tasks without acceptance. A task marked size `L` is sent back for one split round, then rejected.
2. The plan is **proposed**; nothing runs until the owner approves it (on-screen approval, or `--yes` at the terminal).
3. Every run builds its prompt from a **task card** only: project brief + this task + the handoffs of its direct
   dependencies (each <= 800 chars) + the output contract. Total capped by `context_chars` (default 6000, about 1.5k
   tokens). Nothing else is passed; the agent reads the repo itself.
4. A run is one **headless one-shot process** (fresh session), time-limited (30 min default) and turn-limited where the
   tool supports it. The agent must end with a `HANDOFF:` block (status DONE|BLOCKED, summary, notes for next task).
   Missing/invalid block -> the manager builds a handoff from the diff stat + last output, marked "auto".
5. Long work = more tasks, never a longer session.

### Parallel work and handoff
- Dispatcher: a worker pool (default 3 in parallel, max 8). Picks `ready` tasks by seq; two tasks whose scope globs
  overlap never run at the same time. Each task gets its own worktree + branch from the **current integration branch**.
- When a task passes (agent said DONE and the check command, run by JARVES not by the agent, exits 0), the manager
  merges the task branch into the integration branch locally (`--no-ff`). A merge conflict -> task `blocked` with the
  conflicting files; dependents wait. Retry up to `max_attempts` (2) with the short failure excerpt added to the card.
- JARVES **never pushes, merges into a shared branch, opens a PR or deletes** (policy risk list). The finished
  integration branch is the deliverable; `jarvis project ship` only prints the exact commands / diff stat.

### Runners (pluggable, one class each, argv builders unit-tested without the tools)
| runner | headless command shape | auth |
|---|---|---|
| `claude` | `claude -p PROMPT --output-format json --permission-mode acceptEdits --allowedTools ... [--model M] [--resume SID]` | subscription; `ANTHROPIC_API_KEY` removed from the child env |
| `codex` | `codex exec --sandbox workspace-write --json PROMPT` (`exec resume SID` for talk) | subscription; `OPENAI_API_KEY` removed |
| `opencode` | `opencode run --format json [--model provider/model] PROMPT` (`--session SID` for talk) | its own login |
| `openrouter` | OpenCode with `--model openrouter/<id>`; key from `OPENROUTER_API_KEY` env only | **paid** -> off unless `projects.allow_openrouter`, counted against the daily cost cap |

Never passed: any flag in `policy.FORBIDDEN_FLAGS`. A runner that is not installed shows as unavailable with the exact
next step; the dispatcher falls back along the project's `runners` order. Per-task `runner` override is allowed
(e.g. review tasks to a different vendor than the author).

### Manager operations (`service.call(op, ...)`, same result shape everywhere: `{ok, code, text, ...data}`)
`open`, `list`, `show`, `pause`, `resume`, `archive`, `plan`, `approve_plan`, `add_task`, `update_task`,
`cancel_task`, `retry_task`, `status` (project or task; 2-sentence voice text + data), `deep_dive` (task card, attempts,
timeline, handoff, diff stat, check output tail; read-only), `talk` (follow-up turn with the task's agent on the same
worktree/session; queued if the task is running), `tick` (one dispatcher pass, also run by a timer when active),
`digest`, `ship_plan`. `open` and `archive` are CLI/dashboard-only (not voice), like the allow-list (OD-22): opening a
project adds it to the orchestrator allow-list.

### Safety, budget
- Allow-list only; one writer per worktree; worktrees under `<project>/.jarves/worktrees` (git-excluded).
- Daily caps (`projects.max_runs_per_day` 40, `max_cost_usd_per_day` 5, counted from runner-reported cost); at the cap
  the dispatcher stops and says why.
- Agent output is **untrusted data** (same rule as the orchestrator): never executed, shown fenced, redacted of
  secrets, prompt-injection note on every voice result.
- Restart recovery: `running` rows from a dead process become `ready` (attempt not charged), processes are not adopted.
- Owner approvals: plan approval, and any agent permission prompt cannot happen (headless + allowedTools); tools not in
  the allow list simply fail the task as `blocked` with the reason.

### Surfaces and shipping rules
CLI `jarvis project open|list|show|plan|approve|add|update|cancel|retry|status|deep|talk|run|pause|resume|ship|archive|runners`;
voice tools `project_status`, `project_task`, `project_plan`, `project_talk`, `project_deep`; dashboard page Projects
(list, board by status, task detail with timeline, buttons). Guide `project_manager.toml` EN+HE, CLI parity row, feature
row honest status (`implemented_unverified`: real Claude/Codex/OpenCode/OpenRouter runs are an owner gate), Hebrew via
`catalog_add.py`, CHANGELOG, HANDOFF.

## Honest limits
Offline tests use fake runner executables speaking each tool's recorded output shape; real agent runs, OpenCode and
OpenRouter behaviour, and paid calls are **unverified until the owner runs them**. No auto-push/PR/merge to shared
branches. Planner quality depends on the model; the owner approves every plan.

## Review changes (Opus review, 23 Oct 2026)
- Worktrees live in `<home>/projects/<id>/` (`integration`, `wt/t<seq>-a<attempt>`), not inside the repository.
- The integration branch has its own worktree; per-project merge lock; the task branch first merges the integration branch,
  the check runs on that merged result, then the integration branch fast-forwards. The owner's checkout is never touched.
- Scope is enforced after each run (`git diff --name-only`); protected paths (`.claude/`, `.codex/`, `.opencode/`,
  `opencode.json`, `.mcp.json`, `.git*`) always block the task; blocked work stays on its branch, nothing merges.
- Runs use `--permission-mode dontAsk --permission-prompts none --setting-sources user` with a narrow `--allowedTools` list;
  the prompt goes on stdin; forbidden flags are checked on the flags only; the child env is an allow-list (no API keys).
- DONE is decided by the check command run by JARVES (`shell=False`), not by the agent's claim; the project brief is a
  snapshot taken when the project is opened.
- Restart recovery kills a surviving child by pid and requeues once; a task interrupted `max_attempts` times fails.
- Not in the first release: runner fallback beyond the configured order, size-L split round, voice open/archive.

## Flexible orchestration (owner request, 23 Oct 2026, after the first merge)

The owner asked for a generic orchestrator without a fixed flow. Every operation can be used at any time, in any order:

- **Already-started projects:** `open` continues from the branch you are on (or `--base`), `--dirty` starts from your
  uncommitted tracked changes (`git stash create`, your folder untouched); `add --done` records work that exists, with
  its handoff, so later tasks know about it. A new task in a finished project continues it.
- **How to split, owner's choice:** your own list (starts at once, lenient: acceptance optional, size L only warns),
  your instruction (`--how`, wins over the defaults), JARVES's suggestion, or **consult** several agents
  (`--consult claude,codex:model`): they propose in parallel, read-only, and a **chair** agent merges the proposals
  (failures are noted, the rest still count). A new plan replaces an unapproved one. `projects.approve_plans = false`
  starts agent plans without a separate approval.
- **One lead agent with sub-agents:** `--delegate` gives the whole goal to one agent as a `team` task: its card says it
  may use its own sub-agents, Claude Code gets its sub-agent tool (same limits), the time limit is three times longer.
  Any task can be `team` or `solo`; `split N` breaks a task into smaller ones and rewires its dependants.
- **Agents are specs:** `claude`, `codex:<model>`, `opencode:<provider/model>`, `openrouter` (paid, gated) for a task,
  the project order, a planner, a consulted agent or the chair.
- **Taking the result:** `ship` shows it; `ship --apply` (PC only, approval, clean folder) merges it into your branch.
  Never a push.
