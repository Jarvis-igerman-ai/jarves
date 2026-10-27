# Software factory — flexible pipelines on top of the project manager

Owner request, 23 Oct 2026 (after PR #94). Status: design for review; built as its own PR after #94.

## What the owner asked

Every task goes through `plan -> work -> test -> review -> merge -> release`, but:
- the order can change and any step can be skipped;
- each step can be done by JARVES, by the owner, by one agent, by a list of agents, or by an agent with sub-agents;
- cloud agents too (Claude Code cloud, ChatGPT/Codex cloud and others);
- two agents can argue about an issue (debate) and a judge decides;
- a flow for debugging and bug fixes;
- in general: a full software factory. GitHub stays optional.

## Core idea: a task carries a pipeline

A **pipeline** is an ordered list of **stages**. A stage is `{kind, by, skip, on_fail, ...}`. The task keeps its pipeline
and the index of its current stage; the dispatcher runs the current stage of each ready task, then moves it forward,
back, or waits. Today's behaviour is the pipeline `work -> test -> merge`, so nothing changes for existing tasks.

### Stage kinds

| kind | what happens | output |
|---|---|---|
| `plan` | an agent writes a short implementation plan for THIS task (read-only) | a plan note (<= 800 chars) the work stage sees |
| `work` | an agent changes code in the task worktree (today's run) | commits + handoff |
| `test` | JARVES runs the project's check command (or `cmd`) on the merged result | pass/fail + output tail |
| `review` | one or more reviewer agents read the diff (read-only) and answer approve/changes with findings | verdict; changes send the task back to `work` with the findings as notes |
| `merge` | fast-forward into the integration branch (today's merge) | merged |
| `release` | the project's release command (owner-configured, PC only), or the owner does it | release note |
| `reproduce` | bug flow: an agent writes a failing test only; JARVES runs the check and EXPECTS a failure | the repro test name |
| `diagnose` | bug flow: an agent finds the root cause (read-only) | a diagnosis note for `work` |
| `verify` | bug flow: the check passes AND the repro now passes | pass/fail |
| `debate` | two (or more) agents argue a question in rounds; a judge decides | decision + reasons (recorded) |
| `gate` | waits for the owner's yes (on screen / voice / CLI) | approved/rejected |
| `cmd` | an owner-defined command (argv, PC-only config, `shell=False`) | pass/fail |

### Who does a stage (`by`)

One generic actor spec, the same everywhere:
- `jarves` — the built-in action of the kind (test/verify/merge/cmd run by JARVES; plan/review/diagnose pick the
  project's first available agent).
- `owner` — JARVES waits: the task shows `waiting` with what it needs; the owner answers by voice, CLI or page
  (`done`, `approve`, `reject "why"`). For `work` the owner gets the worktree path to edit by hand.
- an agent spec — `claude`, `codex:<model>`, `opencode:<provider/model>`, `openrouter`.
- `team:<agent>` — that agent with its own sub-agents.
- a list — several agents. Per kind: `review` = all review, `require: all|any|majority`; `work` = **best of N**
  (each in its own worktree; the first that passes test (+review) wins, the others are discarded); `plan` =
  consult + chair (as project planning); `debate` = the participants.
- `cloud:<provider>` — a cloud agent (below).

### Templates (named pipelines), per project default + per task override

| template | stages |
|---|---|
| `quick` (default, today) | work · test · merge |
| `feature` | plan · work · test · review · merge |
| `bugfix` | reproduce · diagnose · work · verify · review · merge |
| `release` | test · gate(owner) · release |
| `decide` | debate · gate(owner) |

Voice/CLI change any of it: "skip the review on task 3", "review by Claude and Codex", "I will do the tests myself",
"release after merge", `jarvis project flow 3 "work:codex test review:claude,codex merge"`.

### Failure routing (`on_fail`)

`test`/`verify`/`review` failing -> `back:work` (default; the failure or findings become notes; bounded by
`max_rounds`, default 2), `stop` (task waits for the owner), or `skip`. A `debate` with no decision -> `gate(owner)`.

## Debate

`debate(question, agents=[a, b], judge=c|owner, rounds=2)`: round 1 each states a position (<= 600 chars); each next
round each sees the others' last statement (quoted, untrusted) and answers; the judge gets the transcript (capped) and
returns `{decision, reasons, confidence}`. All read-only, recorded on the task/project timeline. Usable as a stage, as
`review` tie-breaker (reviewers disagree -> debate), and standalone ("let Claude and Codex argue whether to use
SQLite or Postgres").

## Bug-fix flow

`jarvis project bug "the login fails after logout"` (or by voice) creates a task with the `bugfix` template:
reproduce (a failing test only, scope = tests) -> diagnose (root cause, read-only) -> work (fix) -> verify (check
passes and the repro test now passes) -> review -> merge. Optional `debug` helpers: the owner's log/trace text as a
note (redacted), `--repro-cmd` to give the exact failing command.

## Cloud agents

Generic adapter: `start(card, repo, branch) -> job`, `poll(job) -> state`, `result(job) -> branch | diff | PR`. A cloud
stage is asynchronous: the task stays `running` with a job reference; each tick polls; when done JARVES brings the
result into the task worktree (fetch the branch or apply the diff) and continues with the next stage locally (test,
review, merge). Verified interfaces (23 Oct 2026, see research notes):

| provider | start | poll | result | needs |
|---|---|---|---|---|
| `cloud:codex` | `codex cloud exec --env ENV [--branch B] -` | `codex cloud list --json` / `status` | `codex cloud diff ID` (apply locally) | ChatGPT sign-in, a Codex environment for the repo (GitHub) |
| `cloud:copilot` | `gh agent-task create "prompt" --repo O/R --base B` or `POST /agents/repos/O/R/tasks` | `GET /agents/repos/O/R/tasks/{id}` | a branch + PR | GitHub, user token, Copilot plan |
| `cloud:jules` | `POST jules.googleapis.com/v1alpha/sessions` | `GET sessions/{id}` | PR or git patch | Jules app on GitHub, API key |
| `cloud:claude` | (filled from the Claude Code research) | | | GitHub |
| `cloud:cursor`, `devin`, `openhands` | documented REST APIs (search-level evidence only) | | | later |

Cloud work needs the code on the provider's side: the project must have **GitHub write** enabled (below) so JARVES can
push the task branch first. Each provider is off until the owner enables it; usage counts against daily caps.

## GitHub (optional, per project)

`github: off` (default, local git only, as today) · `read` (import issues as tasks, read PR/CI status into JARVES's
status) · `write` (owner-approved per project: push task/integration branches, open a PR, comment, react to CI; still
never force-push, never merge on GitHub without a `gate`). Through the `gh` CLI with the owner's own login. Required
for cloud agents. Buzz (Block's humans + agents workspace) is a later optional channel, not part of this work.

## Safety (unchanged rules, extended)

- Every stage's agent run keeps today's limits (worktree, scope + protected paths, allow-listed env, no push).
- Review/plan/diagnose/debate are read-only runs.
- `release` and `cmd` run only owner-configured argv (PC only, never by voice), each release asks first.
- Pushing to GitHub happens only in `write` mode, per project, and the first push of each task asks first.
- Agent text (findings, debate statements, cloud summaries) is untrusted data, fenced and capped in every prompt.

## Not in this work

Phone/car surfaces, Buzz, merging on GitHub without the owner, paid API agents beyond the existing gated OpenRouter.
