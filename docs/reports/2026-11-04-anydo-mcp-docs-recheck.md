# Any.do MCP: official docs re-check and status (#140)

Checked 4 Nov 2026 (Israel time) against the live pages:
- https://www.any.do/developers
- https://support.any.do/en/articles/15505097-any-do-mcp

## Findings vs the merged skill (`core/src/jarves/anydo.py`, PR #264)
- Endpoint `https://mcp.any.do/mcp`, Streamable HTTP, OAuth 2.0 (scopes `access_anydo_data`, `update_anydo_data`): matches. Legacy `/sse` still exists, not used.
- Rate limit 60 tool calls/min/user, result starts with `RATE_LIMIT_EXCEEDED`: documented on the developers page.
- Developers page: edits to existing tasks/events go through `update_task_preview` and nothing is saved until the user clicks "Execute all". The support article instead lists `apply_bulk_task_updates` / `apply_bulk_event_updates` and does not mention previews. The two pages disagree.
- Skill allow-list uses `apply_bulk_task_updates` for updates. It already reports `ANYDO_UNSUPPORTED` when the server does not offer the tool and `ANYDO_PREVIEW_REQUIRED` when nothing was applied, never success. No code change needed now.
- Not used by the skill (no change): `update_task_preview`, calendar tools, workspaces, `get_workspace_members`.

## Status
- Implementation: merged (#264), beta, OFF by default, unit-tested with a stub MCP server (FAKE, no network).
- Live verification: UNVERIFIED. Needs the owner's Any.do OAuth sign-in, one read, one test task, and one update to see which tool the real server accepts.
- Owner item saved in the workflow inbox with safe default: stay OFF, update stays read-only.
