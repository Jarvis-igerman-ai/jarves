# V4.5 proposal — Any.do official MCP integration for JARVES

> **Status: OWNER-REQUESTED PLAN ONLY (24 October 2026). Not implementation authorization.** Tracker: [#140](https://github.com/igalgerman/jarves/issues/140). The active development permission remains exclusively [NOW](../NOW.md), whose V4.5 implementation rows do **not** include an Any.do integration. This is an optional, isolated addition to the V4.5 planning backlog, **not** a release blocker or permission to link an account, modify tasks, run recurring jobs or expand V5/V7 scope. Existing V4.5 security, CI and release-acceptance priorities win.

## Goal, user value and smallest viable scope

Let the existing Windows JARVES Core answer and carry out explicit Hebrew or English requests about the owner's Any.do personal tasks, shopping list and reminders using one existing Skills/approval/voice/dashboard pipeline. Preserve a single real source of truth: **Any.do holds the tasks**. No local shadow task database, automated sync loop, cross-provider data import, separate orchestrator or phone-specific implementation.

Example journeys:
- **Read**: "Jarves, what is on my list today?" → list, due times in user's timezone, unambiguous source and connected-account state.
- **Create**: "Add eggs and milk to my shopping list" → show target list and exact items → consent → create → check response and say which succeeded.
- **Reminder**: "Remind me tomorrow at 9 to call Max" → resolve time in `Asia/Jerusalem`, explicitly distinguish task due time vs notification/reminder and verify both results.
- **Complete / clear**: "Mark plastic chair as done" or "clear shopping list" → show exact selected tasks and their counts, require the **provider's human approval** for updates → only claim success after a verified persisted state. No task delete.
- **Calendar (optional extension)**: read connected calendar events, then separately gated create; never mix Google Calendar semantics or modify a calendar without a clear user choice.

**Priority:** P2/discrete optional feature candidate in V4.5 *after* security/fast CI foundational gates; never delay 4.5 stabilization or merge of already approved work. Owner's request authorizes this proposal and Issue, not code or account setup.

## Verified external capability (as of 24 Oct 2026)

**Official source**: [Any.do Developers — MCP](https://www.any.do/developers), [Any.do support guide](https://support.any.do/en/articles/15505097-any-do-mcp).

- Remote MCP endpoint **`https://mcp.any.do/mcp`**; **Streamable HTTP**; legacy `/sse` is not preferred.
- **OAuth 2.0**, no API key; protected-resource and authorization-server metadata and dynamic client registration advertised. Scopes currently documented as `access_anydo_data` and `update_anydo_data`. A separate **JARVES** OAuth sign-in is necessary: the owner's ChatGPT Any.do plugin authorization must **not** be assumed reusable.
- Any.do advertises the MCP connector as **beta** and free with all Any.do plans. **No public REST API**. Do not scrape the Any.do interface or use unsupported private endpoints.
- Documented MCP tools include `get_user_info`, `get_lists`, `get_tasks`, `get_task`, `create_personal_tasks`, `set_reminder`, `set_recurring_reminder`, `get_subtasks`, `create_subtasks`, workspaces/boards and calendar reads/creates.
- **Critical contract**: changes to existing tasks and calendar entries use `update_task_preview` / event previews and require the person to press **Execute all** in a compatible host UI. The preview response alone **does not apply changes**. Do not assume the current JARVES Dashboard can render/execute this provider UI. Official documented tools do **not** include deleting tasks. Treat completion, bulk clear, edits and event deletion as **unsupported until end-to-end preview/apply is demonstrated** by the provider-supported protocol/interface. Support-guide marketing copy and current developer tool inventory differ; validate against actual negotiated MCP tools.
- Task paging includes `next_cursor`, default 20 and max 100 per page; documented limit **60 tool calls/min/user**. Date strings without offset are interpreted in account timezone.

## Technical integration design

```text
Voice / Text / Dashboard / CLI
  -> existing JARVES intent + SkillRegistry + owner approvals
    -> AnyDoSkill (thin allowlisted tool adapter; no general purpose untrusted MCP execution)
      -> supported MCP Streamable-HTTP client + OAuth2 account connection
        -> https://mcp.any.do/mcp  (Any.do-owned task store)
```

- Reuse existing Core, SkillRegistry, capability matrix, permission gates, audit/telemetry, dashboard connection settings, bilingual user guide and targeted test harness. Audit current code before naming file paths or creating new modules.
- **OAuth:** launch the system browser for explicit user authentication, use advertised OAuth discovery / PKCE where the chosen client supports it, verify redirect/state and scopes, account identity confirmation, logout/revoke flow and token refresh/expiry; protect stored tokens using the project's existing credential store/Windows facilities. Never send credentials in chat, logs, PRs, issue bodies or sample fixtures. If secure OAuth is unavailable, **do not ship a workaround**.
- **Tool policy:** implement a bounded allowlist. Read operations may run on direct owner request; create actions require preview of list/item/time and confirmation; provider-previews require additional explicit user execute action. Default no background sync, no delegated skills performing surprising writes, no autonomous shared-list changes, no universal MCP tool exposure to arbitrary prompts.
- **Model/data boundary:** MCP tool outputs are untrusted user data, not instructions. Normalize names and IDs carefully, avoid name-only destructive selection, show ambiguities and choose by stable IDs. Never insert returned notes into system prompts as authoritative commands. Avoid arbitrary URL fetching or propagating provider notes as executable content.
- **Failure states:** `not_connected`, `auth_expired`, `permission_denied`, `rate_limited`, `offline`, `preview_required`, `unsupported`, `provider_error` and `partial_success`. Show them in Hebrew/English with an actionable fix. On retries, avoid duplicate creations; after uncertain network result re-read provider state / require user choice rather than blindly resending.
- **Latency/cost:** no paid LLM call or poll just to refresh tasks; read only on explicit request, optionally bounded local cache for UI responsiveness with timestamp and stale indicator. Suppress credentials/task text from external performance issue telemetry.

## Bounded implementation packages (future approval only)

| Package | Content | Gate / evidence |
| --- | --- | --- |
| **A. Feasibility spike** | Inspect existing JARVES Skill/connection/approval paths; test external MCP client compatibility and the real OAuth login/read-only `get_user_info/get_lists/get_tasks` | Owner authorizes account access; real provider handshake and disconnected/expired behavior recorded without secrets |
| **B. Read-only Skill** | List today's/all tasks, filter by list/date, pagination, Hebrew speech and dashboard status, CLI parity | Mock and real read parity, timezone and repeated-page tests |
| **C. Controlled create** | Create tasks and grocery items, one-off and recurring reminders; visible target list/time; explicit confirmation | User-visible external write, success verified from Any.do read; duplicate retries and partial failures tested |
| **D. Human-approved edits** | Investigate Any.do's actual MCP App/host approval UX. Use existing approval surface only **if** it can invoke the documented provider Execute-all action end-to-end | Confirm on **real** Any.do account; otherwise keep update/complete/clear/read-write calendar edits disabled and honest |
| **E. QA and product polish** | Connection guidance EN/HE, capability table, Dashboard test steps / #60 QA integration, audit and disconnect | CI + real Windows owner test, review, rollback; no unapproved release |

**Suggested product cut:** A + B + C deliver practical V4.5 value without gambling on the preview UI. D is a deliberate feasibility gate, not a promise. Do not widen A–C into general-purpose MCP marketplace integration or multi-user family task management; these can be later separately authorized.

## Test matrix and safeguards

1. **Real account only with permission**: connect, confirm email/account identity privately, read lists, create one harmless named test task and reminder, confirm exactly once, then clean up through supported human-approved flow.
2. **Language and time**: Hebrew date phrases, local date vs UTC offsets, `Asia/Jerusalem` DST changes, missing time and ambiguous list name, Hebrew RTL and English.
3. **Task semantics**: grocery list bulk create, list IDs rather than guessed names, pagination beyond 100, private vs shared list selection, stale task IDs, duplicate voice command, retries and partial success.
4. **Error handling**: OAuth denial/revoke/expiry, 401, 429/rate-limit, offline/timeout, reconnect, provider beta contract changes; no hidden retries that repeat writes.
5. **Permissions and prompt injection**: malicious task notes/tool output cannot cause side effects or privilege expansion; no tokens/task contents in logs, GitHub Issues or generic performance reports.
6. **Update gate**: deliberately test provider preview returns *without mutation*, and prove clear completion occurs **only after** supported explicit human Execute-all action. If unavailable, UI describes read/create capability only and refuses updates correctly.
7. **CI/product truth**: focused adapter tests and existing regression gates; Dashboard/help/capability/CLI parity, optional manual QA row in #60, verify on actual installed Windows build before declaring supported.

## Definition of done / non-goals

- [ ] OAuth account link and disconnect are secure and documented; scopes and active account visible to owner.
- [ ] Owner can ask in Hebrew what tasks they have, including list and reminder times, with a verified real response.
- [ ] Owner can explicitly confirm grocery additions and a reminder; Any.do shows one correct result.
- [ ] Complete/clear/edit workflows either pass a true provider-approved live end-to-end test or are **not advertised or enabled**.
- [ ] No private data in public repository, no new paid service, no implicit calendar/deletion/phone permissions.
- [ ] Focused CI green on exact SHA, install-level smoke and owner sign-off captured with links, issues closed only with evidence.
- [ ] No implementation before a separate addition to `docs/NOW.md`; no forced PR merge, no new V4.5 release or gate.

## Contra / alternative

The beta MCP server may change and the provider's **interactive edit-preview** is not necessarily embeddable in JARVES. The fastest low-risk option is **read + create + reminder** first. If the end-to-end provider approval surface cannot be implemented without unsupported APIs, retain that cut and postpone completion/clear to a later milestone. Better to report an honest limitation than silently mutate user tasks.

**Related source docs:** [V4.5 stabilization package](V4_5_STABILIZATION_SECURITY_QA.md), [NOW](../NOW.md), [Skill creation and approvals](../SKILL_CREATION.md), [GitHub flow](../GITHUB_FLOW.md), [#60 release QA](https://github.com/igalgerman/jarves/issues/60).
