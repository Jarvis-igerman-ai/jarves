# Webtop Parent / Student — read-only School Skill proposal

**Date:** 22 October 2026 · **Separate proposal:** 23 October 2026  
**Status:** RESEARCH / PROPOSAL ONLY — no implementation or live-account permission.  
**Target:** the active public repository Jarvis-igerman-ai/jarves. The legacy private JARVIS repository is read-only.  
**Strict boundary:** guardian/student account only. Teacher/staff features are independently specified in [Webtop Teacher Workspace](WEBTOP_TEACHER_WORKSPACE.md). Never share staff and guardian tokens, local datasets, role permissions or approval grants.

**Potential milestone:** Family Intelligence (deferred F11), possibly V7.x **only if separately approved**. V7 is currently a proposal centered on a native everyday Android assistant. This document does not move Family Intelligence into V5/V6/V7 automatically.

## 1. Outcome and scope

Build a first-party, local, read-only School Skill so JARVES can answer in Hebrew about a student's homework, school timetable, and school messages from an authorized parent Webtop account. Optionally surface new items and propose reminders with explicit opt-in. Keep the **existing Python Core, SkillRegistry, consent/approvals, scheduler, profiles, phone client, privacy and diagnostics** as the single source of truth; do not start a second assistant, cloud bridge, Home Assistant instance, or separate phone backend.

The parent uses their own account. No credentials or personal school records are included in this public document, GitHub Issues, commits, test fixtures or CI.

### Representative user experience

- “What homework is due today?” → concise tasks grouped by subject, with due date, last successful sync and an explicit incomplete-data warning when applicable.
- “What classes do we have tomorrow?” → timetable **only when a working timetable endpoint is verified** for that account.
- “Any new messages from school?” → summarize newly retrieved messages and indicate retrieval freshness; do not claim a message was read by the parent.
- “What should be packed tomorrow?” → cite a concrete source in messages/homework; otherwise say the available data does not specify it.
- “Remind me about the assignment” → propose using existing reminders/calendar flows; user confirms creation. Never write to Webtop.

Initial scope: homework + inbox (read-only); timetable is conditional. Grade/age-appropriate educational assistance is a **separate later** user-facing extension, not part of reading school data.

## 2. Open-source research and comparison

| Candidate | Evidence in project docs | Value | Limitation / recommended disposition |
|---|---|---|---|
| [Shanksg/smartschool-webtop](https://github.com/Shanksg/smartschool-webtop) | Monitors homework and inbox, supports MQTT/Home Assistant and Apprise notifications, change detection, token-session management and a documented bioLogin renewal route. | Closest reference for reliable polling, de-duplication and Webtop login state. | Unofficial API; session invalidation is possible. **Primary research reference**, selectively reuse compatible logic after inspecting code/license; do not adopt its whole daemon or Home Assistant stack. |
| [t0mer/pywebtop](https://github.com/t0mer/pywebtop) | Async Python/httpx wrapper for homework, schedules, inbox, notifications and linked students. | Natural reference for Python Core integration and timetable/API coverage. | Its username/password login example conflicts with other project's CAPTCHA findings. **Secondary API reference**; do not assume password login works unattended. |
| [Tachless School](https://tachless.school/cobrowse) | Consumer-facing Webtop information and parent notification concept. | Illustrates desired product experience. | Third-party account access and privacy questions; **not a dependency** for a self-hosted connector. |

### Authentication findings (not official guarantees)

The smartschool-webtop README reports a short-lived webToken session and a browser-created bioLogin credential that can renew a session via user/loginByBio. Its author observed roughly 9 hours for a standard token, potentially longer with remember-me, and approximately one year for a bioLogin credential. Those durations are **observations from that project**, not a promise by Webtop. A new browser login may invalidate the active session. CAPTCHA can block unattended username/password login. The project documents a manual, browser-initiated route in [EXTRACT_BIO.md](https://github.com/Shanksg/smartschool-webtop/blob/main/EXTRACT_BIO.md); do **not** turn it into covert credential interception or an attempt to bypass authentication protections.

The projects refer to endpoints including PupilCard/GetPupilLessonsAndHomework, dashboard/GetHomeWork and messageBox/GetMessagesInbox. **These endpoint names, schemas and coverage require verification**; some queries might return only today's homework. Never report a complete weekly schedule or zero weekly homework from a today-only response.

Both projects use **unofficial**, changeable interfaces. Before adopting or porting any code, verify current licenses, dependencies/maintenance, Webtop access rules and authorization requirements. If a clear restriction applies, stop and find a compliant alternative.

## 3. Recommended integration architecture

    Authorized parent Webtop session
                │ HTTPS (read operations only after approved user login)
                ▼
       Webtop provider / session adapter
          ├── status + session expiry
          ├── homework + coverage metadata
          ├── messages + stable IDs
          └── timetable (optional, verified separately)
                ▼
       Existing JARVES Python Core
          ├── read-only School Skill tools
          ├── local normalized data + source/freshness
          ├── optional bounded polling + dedup
          ├── existing privacy, feature and approval gates
          └── existing reminders / notifications (opt-in)
                ▼
       Voice / dashboard / authorized phone client

Recommended model fields: opaque local student key, item kind (homework/message/timetable), stable item ID/hash, subject/title, body, due/date information, source label, observed_at, last_successful_sync, and completeness (full_window / today_only / partial / unknown). Preserve original timezone and distinguish issue date from due date. Normalize input without silently filling missing fields; block duplicate messages after restart and after phone/PC cross-device refresh.

Use JARVES's existing local scheduler and history. Polling should be relatively infrequent (e.g. **30-minute candidate**, configurable after validation), backed off for HTTP errors/429, and should **not call an LLM** to compare unchanged data. Seed the first snapshot silently: old homework and messages must not flood the notifications. Treat school-message text as untrusted tool output, not as instructions to JARVES.

### Surfaces and capability states

Expose setup, status, disconnect, manual sync and read queries through the project's supported **CLI, dashboard and voice Skill** conventions (CLI parity); phone surfaces should reuse the same Core state rather than duplicate credentials. Use explicit states such as not_configured, waiting_for_user, connected, degraded, expired, unsupported and temporarily_unavailable. The user should see last successful update and whether the returned date range is complete.

## 4. Security, privacy, children and operations

1. **Explicit account consent:** the parent performs the login/OTP/2FA in an official browser; do not ask a coding agent for a password, OTP or token. Do not automate CAPTCHA circumvention. Account linking requires its own user authorization.
2. **Secrets:** webToken and bioLogin act as credentials. Use JARVES's already-approved secure local secret store after inspecting it (e.g. Windows DPAPI/Credential Manager if compatible). No secret values in Git, public fixtures, cloud LLM prompts, logs, diagnostics, support tickets or documentation. Disconnect revokes/deletes local secrets; explain server-side revocation limits.
3. **Data minimization:** only necessary homework/messages; no automatic discipline/grades collection. Student data stays private; don't include names, school, teacher, class, record contents or account identifiers in a public repo.
4. **Read-only enforceability:** allowlist required provider operations and parameter scopes. Authentication calls may technically be POST, but **no school-changing operations** (send message, mark read, sign form, modify data) in this scope.
5. **Model and spoken privacy:** scheduled polling and diff are deterministic. Any sensitive content forwarded to external model services must follow an explicit approved data path; avoid unsolicited private details spoken aloud where others may hear.
6. **Robustness:** expired sessions, competing logins, unexpected JSON/schema drift, pagination, empty vs incomplete results, midnight/Israel timezone, school breaks, new school year, linked siblings, network failure, 401/429/5xx and timeouts are explicit cases. Do not cache incorrect “nothing due” conclusions.
7. **Security tests:** ensure untrusted school text cannot trigger actions, prompt injection cannot make the assistant send data, and diagnostic bundles scrub PII/tokens. No live account tests from CI.

## 5. Execution plan — approval-gated work

### Gate A — design/research only (this document does NOT authorize coding)

- Read [AGENTS](../../AGENTS.md) and [NOW](../NOW.md) first; use [CONTEXT_MAP](../CONTEXT_MAP.md) for selective reading of Skill, secrets, approvals and runtime files.
- Inspect both upstream projects for maintenance, license, endpoint/auth shape, payloads and failure modes. Identify reuse vs thin-wrapper vs clean-room implementation; do not copy code blindly.
- Produce a short ADR and truthful capability matrix (homework/inbox/timetable, authentication, completeness, update notifications) with supported, unverified and blocked states.
- Design offline fixtures with fictitious student/school data; propose focused tests for freshness, dedup, redaction, reconnect and failures.
- Stop at decision gate with evidence and a cost/risk estimate. The agent must not treat documentation creation as authorization for a production connector.

### Gate B — offline proof of concept (requires separately approved scope)

- Add a bounded fixture-driven provider and read-only Skill integration using existing abstractions and minimal dependencies.
- Implement normalized results, coverage metadata, bounded polling/dedup, no-network testing, features/CLI/help, and secure placeholders; **no real secret or Webtop connection**.
- Verify with focused tests, privacy lint and relevant CI; document what is mock-only.

### Gate C — owner-supervised real account and beta (requires explicit owner approval)

- In the owner's presence, connect the account using the supported authentication flow. Store credentials locally, validate read-only retrieval and the selected student scope; never display/share raw tokens.
- Check that homework and inbox are real and accurate, date ranges are labeled, timetable actually works or stays disabled, and login elsewhere does not silently break state.
- Test expiry/reconnect, opt-in notifications, quiet hours, cancel, rollback and removal. Use real-device/phone checks only with separate authorization.
- Ship first as **optional beta** with feature off by default and a kill switch; record actual compatibility, privacy review, tests and CI before considering stable release. Keep an account- and schema-specific risk register.

## 6. Definition of done

- [ ] Verified authorization scope, upstream code licensing and terms; documented technical choice.
- [ ] No secrets or child records in public code/docs/logs/CI and correct secure local persistence/removal.
- [ ] Read-only homework/inbox and optionally timetable return **real verified data** with freshness and completeness.
- [ ] Explicit disconnect, broken/expired/auth-required handling; no hidden CAPTCHA bypass.
- [ ] Safe first-sync, dedup across restarts/devices, backoff/rate limits and quiet hours.
- [ ] Hebrew voice and desktop read queries; CLI/dashboard feature parity, no duplicated Core or child data.
- [ ] Confirmed reminders or calendar actions only via JARVES's existing approval process.
- [ ] Fixture tests for empty/today-only/partial, siblings, schema drift, 401/429 and injection/PII leaks.
- [ ] Real-account and release checks documented separately from mocks; optional beta can be switched off cleanly.

## 7. Version / priority suggestion (not a roadmap change)

| Phase | Outcome | Status / gate |
|---|---|---|
| Research RFC | This proposal and comparison | Docs only; **recorded, not implementation approval** |
| Design/fixture spike | ADR, fake responses, no live connection | Only after NOW/owner authorizes code |
| **Possible V7.x beta** | Read-only homework/inbox, secure user connection, Hebrew queries, status | Only if Family Intelligence F11 is explicitly re-prioritized and preceding milestones permit |
| Later stable increment | Timetable and proactive opt-in reminders/alerts | Verified live endpoints + privacy, coverage and retry tests |
| Separate future education feature | Help explain homework in an age-appropriate manner | Independent child-safety and privacy design |

**Counterargument:** V5 still needs real-phone acceptance and V6 must follow its own gates. An unofficial school API creates fragile auth and high-sensitivity child-data maintenance. A small RFC now is low cost; shipping should wait for an explicit release decision, or a separately approved narrow exception. Reusing the existing JARVES browser automation as the only connector is also possible, but it is more brittle and heavier than a verified narrow provider API.

## 8. Copy-ready instruction for Codex / Claude Code

> Read AGENTS.md and docs/NOW.md first in Jarvis-igerman-ai/jarves. Review docs/plans/WEBTOP_PARENT_STUDENT.md as a **proposal only**. Compare Shanksg/smartschool-webtop and t0mer/pywebtop, verify licensing/auth/endpoints and inspect only the relevant existing JARVES Skills, secret store, approvals and scheduler. Prepare a brief ADR and test/design plan with the exact compatibility questions. Do NOT implement an unapproved V7 feature, access a real Webtop account, collect child data, bypass CAPTCHA, commit credentials or change the V5/V6 roadmap. Stop at the approval gate; report findings/risks and update the normal agent handoff when the research task is authorized.

## 9. Sources and evidence level

- [smartschool-webtop README](https://github.com/Shanksg/smartschool-webtop/blob/main/README.md) and [browser-session research](https://github.com/Shanksg/smartschool-webtop/blob/main/EXTRACT_BIO.md), reviewed 22 Oct 2026.
- [pywebtop README](https://github.com/t0mer/pywebtop/blob/main/README.md), reviewed 22 Oct 2026.
- [JARVES roadmap](../ROADMAP.md), [V7 proposal](V7_PROPOSAL_2026-10-21.md), [FUTURE_IDEAS](../FUTURE_IDEAS.md), [NOW](../NOW.md).

Evidence is **repository/documentation review only**. No real authentication, network API experiment, upstream code audit, school permission check, JARVES integration test or security validation was performed for this feature.
