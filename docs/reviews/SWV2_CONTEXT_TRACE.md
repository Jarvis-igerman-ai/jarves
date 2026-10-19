# SW-V2: conversation context view ("what did he know?") - Issue #17

Owner request: "in the dashboard, in each conversation, I need a way to see his context".

## What it does

**Capture (Core, `core/src/jarves/context_trace.py`).** When a GPT-Live session starts, the session configuration
actually sent is split into blocks:

- voice model (front): persona + name (`FRONT_INSTRUCTIONS`), self-settings rules, workspace header and one block
  per injected file (SOUL / IDENTITY / USER, truncated exactly as sent), remembered facts, feature on/off note,
  guides briefing, setup suggestion, greeting request (with the first `[clock]` note);
- backend (delegation): rules, remembered facts, workspace files (AGENTS / SOUL / ... / TOOLS), guides briefing,
  tool definitions (token estimate includes the JSON schemas).

Each block keeps chars, an estimated token count (no tokenizer installed: ~4 Latin / ~2.2 Hebrew chars per token,
labelled "estimate"), a 12-char SHA-256 hash and (privacy off only) the redacted text. Also stored: models, voice,
effort, tier, max output tokens, verbosity; every registered tool with offered yes/no, why not (feature gate
reason, Skill disabled, registered later) and policy (not allow-listed, asks first); the remembered facts that are
really in each prompt (id, source, confidence, learned-in conversation, front/backend, "cut" when truncated).

During the conversation, everything sent on the connection is an event with its time: `[clock]` notes,
workspace-change notes (a new version of the workspace block), progress / approval / stall notes,
background-task announcements, tool results (tool, ok, code, args). At the end: facts learned / forgotten / edited
and whether the persona files changed (they apply to the next conversation). Typed chat: the backend instructions
of each request; a block that changed between requests becomes a `block_changed` event.

**Storage.** `store.db` tables `context_traces`, `context_events` (own connection + writer thread, never blocks the
voice loop); pruned with `history.keep_days`; `privacy.mask_history` strips the text, `delete_conversations` deletes
them. Secrets: every text passes `diagbundle.sanitize` (logs.redact: API keys, bearer, JWT, key=value credentials;
e-mails masked) plus the bundle leak scan; the export is sanitized again. Privacy mode at capture time: names, ids,
sizes, token estimates and hashes only. Every capture step is guarded: a failure only logs
`CONTEXT_CAPTURE_FAILED`, the conversation goes on.

**Dashboard.** `#/context/<session>` (`dashboard_static/context.js` + `context.css`): tiles (tokens at start / end,
model, tools offered, facts), tabs Blocks (size bars, expandable read-only text, copy, start vs end view), Timeline
(context events aligned with the transcript turns: "after turn 4: Workspace changed: USER.md"), Start vs end (block
diffs, facts learned / forgotten), Why did he say that? (for a chosen answer: the notes and tool results that reached
him for it, tools called, memory facts sharing words, standing blocks at that moment with their version, latest
clock note), Tools, Memory; search with highlighting; export as redacted JSON. A "Context" button and "why?" links
on every conversation page, "Where this file was used" (with version hash changes) on workspace file pages.
Routes: `GET /api/session/<id>/context`, `GET /api/session/<id>/context/export`, `GET /api/context/usage?file=`.

Feature switch `context_trace` (on by default, applies next conversation), guide `guides/data/context.toml`.
Windows: implemented_unverified (tests + one real run below). Pi: implemented_unverified, pure Python + SQLite,
roadmap issue #18.

## Integration hooks (outside the new files)

- `app.py`: `context_trace.attach_brain(...)` after `Brain(...)`; `context_trace.attach_live(...)` after the
  guides `attach_live` (must stay LAST so it sees the final instructions); `context_trace.close_home(self.home)` after
  `store.detach` in `stop()`.
- `dashboard.py`: `"jarves.context_trace"` added to `PLUGIN_MODULES`.
- `privacy.py`: `context_trace.mask_all(st._db)` in `mask_history`, `context_trace.delete_all(st._db)` in
  `delete_conversations`.
- `dashboard_static/index.html`: `context.css` + `context.js` (after guides.js).
- No edit to `voice/live.py` or `brain.py`: the trace wraps `live.session_config`, `live._run_tool`, `live.on_end`
  and taps `conn.session.thinking/commentary.append` + `conn.response.item.create` on the instance. This relies on
  `LiveVoice.start()` opening the connection BEFORE it calls `session_config()` (true today); if that order changes,
  notes are not captured (the start snapshot still is). `context_trace.attach_brain` wraps `agent.reply`.

## Tests and evidence

- `core/tests/test_context_trace.py` (10 tests, offline): capture contents (blocks, facts with targets, tools,
  clock / task / tool-result / workspace / end events, diff), secrets + e-mails never in store.db (file bytes
  scanned), privacy mode (no text anywhere, names kept), retention, privacy actions, typed chat, switch off /
  history off, HTTP routes over real HTTP, uncaptured sessions, lossless block split.
- Full suite: 1025 passed, 3 skipped (before the final merge; re-run after it, see the report).
- Real silent GPT-Live run (`tools/silent_live.py --turns 6,7`, virtual audio, ~0.6 min, about $0.03): the
  conversation's context was captured and linked to its transcript: 17 blocks, ~12.4k estimated tokens at the
  start, of which the backend tool definitions are ~6.6k; 59 tools offered, 7 left out (camera switched off).
- Headless screenshots (`core/tools/context_screens.py --real <home>`): `docs/reviews/swv2_context/`
  (blocks, search, timeline, start vs end, why, tools, memory, workspace "where used", phone width, and the real run).

## Findings

- **Real run: claimed an action without doing it.** For "תזכור שהצבע האהוב עליי הוא כחול" GPT-Live answered "רשמתי לי
  שהצבע האהוב עליך הוא כחול" with NO delegation and no tool call; memory.db stayed empty
  (`context_real_why_light.png`: "No tool result or note"). The front instructions say remembering must be delegated.
  This belongs to the conversation package (WP-A / live tuning), not fixed here.
- The backend tool definitions are about half of his whole context (~6.6k of ~12.4k estimated tokens) on every
  session start.
- At phone width the dashboard shell itself (nav, top bar) overflows horizontally; the context page inherits it.

## Open questions

- Should the Context page also be reachable from the Godot face / voice ("למה אמרת את זה?")? Not built.
- Token counts are estimates; a real tokenizer (tiktoken o200k) would need a new dependency.
