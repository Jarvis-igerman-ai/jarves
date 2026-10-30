# Industry research: the smallest reliable Claude Code workflow

Verified against official documentation on **30 October 2026**. Research only: no user settings, accounts, permissions, subscriptions or installed tools were changed. Documentation capabilities are not proof that the owner's installed Claude Code version exposes them.

## Recommendation

Keep **Claude Code as the manager with native subagents**, and use Codex when deliberately assigned. Reconcile local Markdown first; add a thin local status collector/view only after its state is useful. Neither an agent-team migration nor LangGraph/Temporal is justified by the current evidence.

This is an engineering recommendation from the sources below, not a measured comparison of competing implementations. The valuable industry pattern is durable task identity, explicit waiting states, evidence and safe resumption—not a requirement to adopt a framework.

## Source verification

| Official source | Retrieved result | Date information |
|---|---|---|
| [Claude Code hooks](https://code.claude.com/docs/en/hooks) | HTML HTTP 200 | Page metadata modified 2026-10-29 19:44:04 UTC |
| [Claude Code subagents](https://code.claude.com/docs/en/sub-agents) | HTML HTTP 200 | Page metadata modified 2026-10-28 19:31:55 UTC |
| [Claude Code agent teams](https://code.claude.com/docs/en/agent-teams) | HTML HTTP 200 | Page metadata modified 2026-10-26 21:20:47 UTC |
| [Claude Code memory](https://code.claude.com/docs/en/memory) | HTML HTTP 200 | Page metadata modified 2026-10-27 19:25:38 UTC |
| [Claude Code settings](https://code.claude.com/docs/en/settings) | HTML HTTP 200 | Page metadata modified 2026-10-27 17:34:35 UTC |
| [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution) | HTTP 200 after redirect to [persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Retrieved 2026-10-30; publication date not asserted |
| [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | HTTP 200 | Retrieved 2026-10-30; publication date not asserted |
| [Temporal Workflow Execution](https://docs.temporal.io/workflow-execution) | HTTP 200 | Retrieved 2026-10-30; publication date not asserted |

The Claude documentation's advertised `.md` alternatives returned HTTP 403 in this environment; the HTML pages above were accessible and used. LangGraph's Markdown alternatives were accessible. No inaccessible page is presented as having been read.

## Hooks: exact identities and completion semantics

Source: [hooks reference](https://code.claude.com/docs/en/hooks).

| Event | Relevant input fields beyond the common fields |
|---|---|
| Common | `hook_event_name`, `session_id`, `cwd`, `transcript_path`; `prompt_id`, `permission_mode`, `agent_id`, `agent_type` are conditional |
| SubagentStart | `agent_id`, `agent_type` |
| SubagentStop | `stop_hook_active`, `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message` |
| TaskCompleted | `task_id`, `task_subject`; optional `task_description`, `teammate_name`, deprecated `team_name` |
| TeammateIdle | `teammate_name`, deprecated `team_name` |

On TaskCompleted/TeammateIdle, `agent_id` can be absent; its documented support requires v2.1.290+. Internal agents can emit SubagentStop. Since v2.1.271, a SubagentHandback report may be in `tool_input.message`, rather than the closing message.

These events indicate lifecycle transitions, not product acceptance. TaskCompleted can be blocked with exit 2. Async hooks cannot enforce decisions; ordinary async results wait for another turn. Current `asyncRewake` can wake an idle session on exit 2, subject to installed-version validation. Stop-loop control includes `stop_hook_active` and a consecutive-continuation cap. Async work may be canceled when a noninteractive session ends.

**Integration implication:** filter registered worker identities, retain only minimal telemetry, and keep acceptance separate. Do not parse or publish complete transcripts or treat a broad Stop event as a verified task.

## Native subagents fit the requested manager model

Source: [subagents](https://code.claude.com/docs/en/sub-agents).

Subagents have separate context, configurable tools/permissions and shared subscription usage. Definitions can specify `isolation: worktree`; background subagents have a narrower built-in tool set, so browser/PC tools must be checked in the actual worker context. Permissions needed by a background worker can surface in the main session.

General-purpose/custom workers can be resumed with their identity and retained context; Explore/Plan are documented as one-shot and non-resumable. Partial output at a turn limit is not a finished result. Native `/agents` and `/tasks` help inspect activity; persistent version acceptance still needs its own evidence.

**Integration implication:** define only the recurring roles that save setup—bugs, current-version implementation, and installed-product verification. Prefer resuming a relevant worker over repeatedly spawning replacements. Do not assume every worker inherits every tool or that worktree isolation follows from merely assigning a role name.

## Agent teams are an optional escalation

Source: [agent teams](https://code.claude.com/docs/en/agent-teams).

Agent teams remain experimental and disabled by default. They add peer messaging and, where Task tools are available, a shared task list with dependency blocking and file-locked claiming. The documentation warns about coordination overhead, increased token consumption and resumption/shutdown limitations. Teammates require an interactive session; named ordinary delegation can also change behavior after teams are enabled.

**Recommendation:** leave teams disabled initially. The user already wants one Claude manager and scoped subagents. Consider teams only if direct worker-to-worker coordination becomes a measured bottleneck, not as a prerequisite for a live view or question queue.

## Local files: instructions are not a live task database

Sources: [settings](https://code.claude.com/docs/en/settings) and [memory](https://code.claude.com/docs/en/memory).

| File | Documented role | Consequence |
|---|---|---|
| `.claude/settings.json` | Shared project configuration | Commit generic hooks/settings only when they should apply to every checkout. |
| `.claude/settings.local.json` | Personal project overrides | Manually created files need an explicit ignore rule. Merge required keys; never overwrite existing approvals or unrelated configuration. |
| `CLAUDE.local.md` | Local project instructions, loaded alongside CLAUDE.md | It is read by Claude; “local” does not mean inert. Gitignore it explicitly. |
| Auto memory | Machine-local learned notes | It is not a shared, complete workflow ledger. Main-session auto memory is not normally loaded into subagents. |

Memory files concatenate. A root CLAUDE.local.md can change AGENTS.md fallback behavior; a literal sentence asking Claude to read AGENTS.md is not an import. An explicit `@AGENTS.md` import is the documented compatibility path. Current direct AGENTS.md support requires v2.1.277+ and depends on project-instruction mode. In worktrees, local instructions may exist only in one checkout, whereas local settings may resolve through the main checkout.

**Recommendation:** maintain one concise shared policy plus an explicitly ignored local work ledger/question queue. CLAUDE.local.md can point to them. Avoid putting all changing task history into instruction files that are eagerly loaded each session.

## Durable execution and human input: useful patterns, no migration requirement

Sources: [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).

LangGraph separates thread checkpoints from cross-thread stores. Its in-memory saver does not survive process restarts. An interrupt can preserve state and wait for external input; resumption uses the same thread ID. Crucially, the interrupted node restarts from its beginning, so earlier side effects may repeat unless designed safely. Current typed interrupt responses are version-dependent.

**Transferable pattern:** retain the question, task identity and continuation context; resume only the dependent work after an answer. Keep unrelated ready work moving. A saved checkbox or an unanswered question is not a completed node. Put external effects behind existing approvals and idempotency protections. These principles can be implemented in the existing workflow without importing LangGraph.

Source: [Temporal Workflow Execution](https://docs.temporal.io/workflow-execution).

Temporal persists execution history and recovers workflow progress through replay. Its worker/service model and deterministic commands distinguish durable orchestration from a script merely remembering the last status. A workflow can be running while awaiting something; that does not mean it is actively using a worker.

**Assessment:** Temporal becomes relevant for many long-lived, distributed jobs where recovery guarantees justify its service and workflow constraints. That need has not been established for a local Claude Code manager. An OS-scheduled observer is substantially smaller, though it does not provide Temporal's guarantees.

## Smallest implementation sequence for JARVES

1. **Reconcile Markdown before adding machinery.** One current plan, one list of open decisions, and criterion-level implementation/verification/release evidence. Keep unknowns and blocked items visible.
2. **Keep Claude as the decision-making manager.** Register task/worker IDs, dependencies and ownership; use native subagents and existing permission gates. Occasional Codex work receives the same task/evidence contract.
3. **Add minimal telemetry.** A short local hook records lifecycle IDs and timestamps. Explicit progress/evidence records supplement hooks; no transcript scraping or claims that tool activity proves forward progress.
4. **Handle presence explicitly.** While the owner is available, present concise questions with quick choices and free text. Away, persist them and switch to independent authorized work; absence never means approval or abandonment.
5. **Use an independent observer.** A Windows Task Scheduler job or separate small process checks last observation, active workers and ready work. It deduplicates alerts. It must report “waiting on owner”, “no authorized ready work”, “unknown” or “manager unavailable” distinctly.
6. **Connect wake-up only through a verified interface.** A local inbox can wait for a safe-stop consumer; it cannot wake a dead session by itself. Validate installed support before using asyncRewake or another documented message/channel path. Do not type commands into arbitrary terminal windows.
7. **Add a live view after the ledger is trustworthy.** Render the same records with source freshness, checkmarks backed by evidence and interactive questions. The interface must not become a second independent tracker.

## Checks required on the owner's PC

- Record `claude --version`; inspect loaded sources with `/status` and `/context`, and available worker/tool behavior with `/agents` and `/tasks`.
- Test one worker start/stop, one partial result, one unanswered question and resumption, one manager stop and one observer detection. Use a harmless scratch task.
- Prove a disconnected observer cannot alter runtime approvals, publish, spend or declare verification.
- Verify existing local settings and ignore rules before installing hooks. Do not enable experimental teams, overwrite personal instructions or widen permissions as a side effect.

No live Claude Code, Codex, Windows Task Scheduler, browser/PC-use or device test was performed in this research task.
