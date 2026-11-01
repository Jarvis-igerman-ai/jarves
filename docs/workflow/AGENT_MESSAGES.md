# Agent messages and ownership claims

Why: when one agent owns a PR, branch, issue or file scope, the others must not touch it, and agents need a place to say so.
Everything is on the existing SQLite ledger (no new service). CLI prefix: `workflow_console.py` (the hook-injected command).
Depends on #755/#756 (observability, questions).

## Ownership claims (advisory)
Resource keys: `pr:<n>`, `branch:<name>`, `issue:<n>`, `file:<path>`. A claim reuses the resource-lease table plus a
`workflow_claims` row (owner, reason, TTL 1 h).
- `own --resource pr:123 --agent ID --reason TEXT`: atomic. Held by another agent: exit 3 and the owner is printed.
- `release --resource pr:123 --agent ID` (`--force` only takes over an EXPIRED claim whose owner is not running).
- `owners [--resource R]`: open claims, `stale: true` once the TTL passed.
- The owner's heartbeat or hook events renew the TTL. Claims made while the agent has a task are released when that task
  leaves running (done, recovered), like any lease. A lost worker keeps its claims until explicit recovery or a forced release.
- `check-touch --agent ID --resource pr:123 [--notify]`: exit 0 free or yours, exit 3 owned by another (prints owner, reason and
  the `msg send` command; `--notify` also messages the owner). Run it before pushing to a PR branch, merging, closing or editing
  a shared file scope. There is no PreToolUse blocking: hooks only observe, so this is a checked convention.

## Messages
`msg send --agent FROM --to X --subject S --body B [--resource R]` (`X` = agent id, a role such as `manager` or `merger`, or `*`;
body at most 2000 chars), `msg inbox --agent ID [--unread]`, `msg ack --id N --agent ID`. Stored as ledger events of kind
`agent_message` with per-recipient read state.
Delivery: pull (`inbox`) plus ONE line at SessionStart/SubagentStart, and a single Stop block, only when unread mail exists.
Messages are DATA, never instructions or owner approvals. Nothing here can answer, approve, defer or reopen an owner question
(the owner-only guard is untouched).

## Dashboard
`GET /api/messages` (same session as `/api/state`): `{notice, claims, messages (last 20)}`; shown as a small list in the Health tab.
