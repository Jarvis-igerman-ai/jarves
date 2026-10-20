# SW-V4 E — Agent Orchestrator: integration research and capability matrix (#49, #17)

Checked **20 Oct 2026** on the owner's PC (Windows 11 Home 10.0.26300), by reading the official docs and by
probing the installed tools. No token, key or e-mail was read or printed (`claude auth status` and Codex
`account/read` were filtered to the auth *mode* only; `~/.claude/sessions/*.key` and `~/.codex/auth.json`
were never opened).

| Tool | Installed | Login (mode only) | Probe |
|---|---|---|---|
| Claude Code CLI | **2.1.304** (`npm` global, `claude.ps1`) | `authMethod: claude.ai`, `subscriptionType: max`, `apiProvider: firstParty` | `claude agents --json`, two live `claude -p` stream-json turns (haiku) |
| OpenAI Codex CLI | **codex-cli 0.163.0** (`%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe`, the Codex app's CLI) | Codex `account/read` → `type: chatgpt`, `planType: plus` (subscription, no API key) | `codex app-server` stdio: `initialize`, `account/read`, `thread/list`, `thread/loaded/list`, `thread/read`; `codex app-server generate-json-schema` (the exact protocol of this version) |

Live cost of the probes: two haiku turns, `total_cost_usd` 0.0015 + 0.0026 (a client-side estimate; the turns ran
on the Max subscription, `apiKeySource: none`, so no money was spent). Codex probes start no turn: $0.

## 1. Claude Code — what exists and what it is good for

Sources (all read 20 Oct 2026): [channels](https://code.claude.com/docs/en/channels),
[channels-reference](https://code.claude.com/docs/en/channels-reference),
[hooks](https://code.claude.com/docs/en/hooks), [plugins / MCP](https://code.claude.com/docs/en/mcp),
[headless (`claude -p`)](https://code.claude.com/docs/en/headless), [sessions](https://code.claude.com/docs/en/sessions),
[agent view (`claude agents`)](https://code.claude.com/docs/en/agent-view),
[cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging),
[remote control](https://code.claude.com/docs/en/remote-control), [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview).

### 1.1 Discovery of sessions the owner started by hand — VERIFIED, documented
- `claude agents --json` "prints active sessions as a JSON array and exits"; the docs call it **the supported way to
  read session state from outside Claude Code** (the files under `~/.claude/` are not a stable interface).
  Fields: `cwd`, `kind` (`interactive` / `background`), `startedAt` (ms), `pid` + `status` (`busy` / `waiting` /
  `idle`) while the process is alive, `waitingFor` (`permission prompt`, `input needed`, `sandbox request`,
  `worker request`, `dialog open`), `sessionId` (UUID, usable with `--resume`), `name`, and for background
  sessions `id` + `state` (`working`, `blocked`, `done`, `failed`, `stopped`). `--cwd <path>` filters, `--all`
  adds finished background sessions.
- **Verified on this PC:** both interactive terminal sessions that were running (`C:\work\jarvis` busy,
  `C:\work\copyapp` idle) were listed with pid, sessionId, name and status. The command is read-only, costs nothing
  and does not touch the session.
- Used by JARVES as the **observe** path for every live Claude Code session, plugin or not: state, waiting-for,
  project and age, without a turn and without interrupting a busy worker (#49 clarification).
- Not used: `~/.claude/sessions/<pid>.json` (same data, internal format) and the `.key` files next to it (secrets).
- Past sessions: transcripts are `~/.claude/projects/<project>/<session-id>.jsonl`; the docs say the entry format is
  internal and changes between versions, so JARVES lists past sessions **from its own registry** (sessions it has
  seen through hooks, `claude agents --json` or launched itself), and resumes them only by ID through the CLI.

### 1.2 Hooks — VERIFIED (docs), the plugin's passive channel
- Events used: `SessionStart` (`source`: startup/resume/clear/compact/fork, `model`), `UserPromptSubmit` (`prompt`),
  `PostToolUse` (`tool_name`, `tool_input`, `tool_response`, `duration_ms`; throttled by JARVES), `Notification`
  (`message`, `title`, `notification_type`: `permission_prompt`, `idle_prompt`, `agent_needs_input`,
  `agent_completed`, ...), `PermissionRequest` (`tool_name`, `tool_input`, `mcp_server`), `Stop`
  (`stop_hook_active`, `last_assistant_message`), `SubagentStop`, `StopFailure` (`error`: `rate_limit`,
  `authentication_failed`, `billing_error`, ...), `SessionEnd`. Common fields: `session_id`, `transcript_path`,
  `cwd`, `hook_event_name`, `permission_mode`.
- Decisions: `PermissionRequest` → `hookSpecificOutput.decision.behavior` `allow`/`deny` (with `message` on
  deny); `Stop` → `{"decision":"block","reason": ...}` keeps Claude working with `reason` as the next instruction
  (loop cap: 8 consecutive blocks, reset by a tool call; `stop_hook_active` tells a continuation);
  `UserPromptSubmit` / `SessionStart` → `hookSpecificOutput.additionalContext`.
- **Limit (as #49 says):** a hook is not an arbitrarily timed prompt-injection API. It runs only at its event. A
  question JARVES queues for a session is delivered at the next `Stop` (end of the current turn) or the next
  `UserPromptSubmit` — the "next safe boundary".
- Plugin layout (docs): `.claude-plugin/plugin.json`, `hooks/hooks.json` (`${CLAUDE_PLUGIN_ROOT}`), `.mcp.json` at
  the plugin root; plugin MCP servers start with the plugin. Install: `claude plugin marketplace add <dir>` +
  `claude plugin install <name>@<marketplace>` (user scope); uninstall the same way.

### 1.3 Channels — the only documented way to push a message into a running session — REQUIRES the launch flag
- An MCP server (stdio child of the session) that declares `capabilities.experimental["claude/channel"] = {}` and
  emits `notifications/claude/channel` with `{content, meta}` (meta keys: letters, digits, `_` only) puts a
  `<channel source=... ...>` event into the **already-running** session. Events queue and are delivered in order;
  while Claude is busy they arrive together on the next turn. Claude Code does not acknowledge notifications, so a
  two-way channel needs a reply tool (JARVES: `jarves_reply`).
- **Permission relay:** with `capabilities.experimental["claude/channel/permission"] = {}`, Claude Code sends
  `notifications/claude/channel/permission_request` (`request_id` = 5 letters a-z without `l`, `tool_name`,
  `description`, `input_preview`, sanitized and credential-masked since 2.1.211 / 2.1.234) and accepts
  `notifications/claude/channel/permission` `{request_id, behavior: allow|deny}`; the terminal dialog stays open,
  first answer wins, an unknown ID is dropped.
- **Gates:** research preview; the flags are hidden from `claude --help` (verified: not listed in 2.1.304's help);
  Pro/Max users without an organization may use them; a custom channel is not on the allow-list, so the session
  must be started with `claude --dangerously-load-development-channels plugin:jarves@jarves-local` (a one-time
  confirmation dialog at the terminal). Ignored in `-p` mode. On the v2 MCP runtime a channel server must **not**
  negotiate protocol revision 2026-07-28 (JARVES's server answers an older revision).
- **Consequence (owner decision OD-20 = yes):** a session the owner starts *without* the flag cannot receive a
  pushed message. JARVES then answers "ask" from the latest snapshot (age stated) or queues it for the next `Stop`,
  and says which (`NO_LIVE_CHANNEL`). It never restarts or seizes the session.

### 1.4 Cross-session messaging — documented, NOT used by JARVES
- Same-machine sessions have an inbox (named pipe on Windows) and other Claude sessions message them with the
  `SendMessage` tool. External scripts may post only to **their own** session's socket with the per-session
  `CLAUDE_CODE_MESSAGING_TOKEN`; on native Windows the auth line is mandatory and the per-session key is a secret.
  The message wire format after the auth line is not documented. Using it from JARVES would mean handing a session
  token to another process: rejected (secret handling, undocumented framing). Listed as a future option.

### 1.5 Headless / Agent SDK for sessions JARVES starts — VERIFIED live
- `claude -p --input-format stream-json --output-format stream-json --verbose` keeps one session open: JARVES
  writes `{"type":"user","message":{"role":"user","content":...}}` lines (tell / continue) and reads `system/init`
  (`session_id`, `apiKeySource`, `permissionMode`, `claude_code_version`, `capabilities`), `assistant`, `result`
  (`total_cost_usd`, `usage`, `is_error`, `permission_denials`, `num_turns`). **Verified:** two turns in one
  process, `apiKeySource: none` (subscription), `permissionMode: default`.
- Interrupt: `{"type":"control_request","request_id":...,"request":{"subtype":"interrupt"}}` → **verified**
  `control_response` `subtype: success` (capability `interrupt_receipt_v1` in `system/init`).
- Permissions in `-p` Manual mode: a call that needs approval is denied unless a `PermissionRequest` hook or a
  `--permission-prompt-tool` approves it. JARVES loads its plugin with `--plugin-dir`, so the same
  `PermissionRequest` hook routes the request to the owner's approvals popup. JARVES never passes
  `--dangerously-skip-permissions` / `--allow-dangerously-skip-permissions` / `--permission-mode bypassPermissions`.
- Resume: `claude -p --resume <session-id>`; when that session is a live *background* session Claude Code opens it
  instead, and when it is a live *interactive* session two processes would write one transcript (docs: "messages
  from both interleave"). JARVES refuses to resume a session that `claude agents --json` shows alive (one writer).
- Terms: the Agent SDK docs limit *third-party products* offering claude.ai login. JARVES runs the owner's own
  installed `claude` CLI under the owner's own login on the owner's PC, started only after an on-screen approval
  (OD-23). If only API billing were possible, JARVES errors instead of switching (`AUTH_REQUIRED`).

### 1.6 Remote Control — evaluated, not used
A user-facing UI (claude.ai / mobile app) for a local session, not a programmatic attach API for a third party.
Mentioned to the owner as the manual fallback for steering a session started without the channel flag.

## 2. OpenAI Codex — what exists

Sources: [App Server](https://learn.chatgpt.com/docs/app-server),
[codex-rs/app-server](https://github.com/openai/codex/tree/main/codex-rs/app-server), the Python SDK docs, and the
schema printed by `codex app-server generate-json-schema` (0.163.0, the authority for this PC).

- Transport: `codex app-server` on stdio (default), JSON-RPC 2.0 **without** the `"jsonrpc"` field, one JSON
  object per line. `initialize` `{clientInfo:{name,title,version}, capabilities?}` → `{codexHome, platformFamily,
  platformOs, userAgent}`, then the `initialized` notification. **Verified** (1.5 s to initialize).
- Account: `account/read` → `{account:{type:"chatgpt", planType}, requiresOpenaiAuth}` — **verified `chatgpt` /
  `plus`**. OD-21 (subscription only): JARVES refuses to start a turn when the type is `apiKey`
  (`AUTH_REQUIRED`), never switches.
- Threads: `thread/start` (`cwd`, `model`, `approvalPolicy`, `sandbox`, `serviceName`), `thread/resume`
  (`threadId`), `thread/fork`, `thread/list` (`cwd`, `limit`, `sourceKinds`, `searchTerm`, ...), `thread/read`
  (`includeTurns`), `thread/loaded/list`. Thread fields: `id`, `sessionId`, `cwd`, `source`
  (`cli`/`vscode`/`exec`/`appServer`/...), `status` (`notLoaded` / `idle` / `active` with `activeFlags`
  `waitingOnApproval` / `waitingOnUserInput` / `systemError`), `preview`, `name`, `createdAt`, `updatedAt`,
  `cliVersion`, `gitInfo`.
- Turns: `turn/start` (`threadId`, `input:[{type:"text",text}]`), `turn/steer` (`threadId`, `input`,
  `expectedTurnId`), `turn/interrupt` (`threadId`, `turnId`). Events: `thread/started`, `thread/status/changed`,
  `turn/started`, `turn/completed` (`turn.status`: `completed` / `interrupted` / `failed`), `item/started`,
  `item/completed` (items `agentMessage{text}`, `commandExecution{command,cwd,status}`, `fileChange`, ...),
  `item/agentMessage/delta`, `thread/tokenUsage/updated` (`tokenUsage.total/last`: input/cached/output/reasoning
  tokens), `account/rateLimits/updated`, `error`.
- Approvals are **server requests** to the client: `item/commandExecution/requestApproval` (`threadId`, `turnId`,
  `itemId`, `command`, `cwd`, `reason`) and `item/fileChange/requestApproval` (`grantRoot`, `reason`); the client
  answers `{decision: accept | acceptForSession | decline | cancel}`. JARVES answers only `accept` (after the
  owner approved on the screen) or `decline` / `cancel`, never `acceptForSession`.
- **Verified:** `thread/list` on a freshly started app-server lists the owner's existing threads (source `vscode` =
  the Codex app / IDE) from the state DB with status `notLoaded`; `thread/loaded/list` is empty; `thread/read` of
  an unknown ID errors `thread not loaded`. So a separate app-server **sees** threads started elsewhere (observe:
  metadata and stored turns) but is not attached to their live turns.
- Shared daemon: `codex app-server daemon start|stop|version` and `codex app-server proxy` (stdio to the running
  daemon's control socket, `~/.codex/app-server-control/app-server-control.sock`); `codex agents` "browses all
  agent sessions on the shared local app-server daemon"; a TUI joins with `codex --remote unix://`. On this PC the
  daemon was **not running** (`daemon version`: connection refused). When it runs, JARVES connects through
  `codex app-server proxy` and then sees and can steer threads loaded in it — the supported attach route.
- **Limit (as #49 says):** a Codex TUI or the Codex app running its own in-process server is **not attachable**
  by a third party: JARVES lists it as `observe` with `UNSUPPORTED_ATTACH` and offers "resume this thread under
  JARVES" (owner approval, refused while the thread shows activity: never two writers).
- Not used: `codex exec --json` (a one-shot run; the App Server covers start/steer/interrupt), WebSocket listener
  (experimental and a TCP port), ACP / A2A (later mapping targets only; "Buzz" stays unconfirmed and unused).

## 3. Capability matrix (provider × mode × operation)

V = VERIFIED on this PC (probe or live run), D = documented and implemented, contract-tested against fakes that
speak the recorded protocol, not yet run against the real tool on this PC; U = UNSUPPORTED (reason given);
O = needs the owner (gate) for the live check.

| Provider / mode | observe | ask | steer (tell/continue) | resume | start | interrupt | approve | auth label | cost |
|---|---|---|---|---|---|---|---|---|---|
| Claude, pre-existing terminal, no plugin | **V** `claude agents --json` (state, waiting-for, cwd, age) | snapshot only (`NO_LIVE_CHANNEL`) | U: no documented push without the flag | U while alive (one writer); after exit **D** via `claude -p --resume` | n/a | U (no API; the owner presses Esc) | U (terminal dialog only) | U (no hook) | U |
| Claude, pre-existing terminal, plugin installed (OD-20) | **D** + hooks (progress, summary on Stop, blocker) | **D** next safe boundary (`Stop` block / `UserPromptSubmit` context), else snapshot | **D** at the next boundary | as above | n/a | U | **D** `PermissionRequest` hook → popup → allow/deny | **D** from hook input (none: label "unknown") | D: usage not in hooks; status-line data not used |
| Claude, terminal started with the JARVES channel flag | as above | **D** live push (`notifications/claude/channel` + `jarves_reply`) | **D** live push | as above | n/a | U | **D** channel permission relay **and** hook | as above | as above |
| Claude, pre-existing VS Code panel | **O** (docs do not say VS Code sessions are in `claude agents --json`; OE-07) | hooks apply in the extension (same settings) **O** | as plugin row **O** | as above | n/a | U | hook **O** | as above | as above |
| Claude, JARVES-started managed (`claude -p` stream-json) | **V** events | **V** live (stdin turn) | **V** live | **D** `--resume` | **V** (owner approval first) | **V** `control_request interrupt` | **D** hook via `--plugin-dir` | **V** `apiKeySource` | **V** `total_cost_usd` estimate + usage |
| Claude, resumable old session | registry + `--resume` by ID | n/a | n/a | **D** (owner approval; refused while alive) | n/a | n/a | n/a | n/a | n/a |
| Claude, cloud session | U (only via Remote Control UI) | U | U | U | U | U | U | U | U |
| Codex, JARVES-started managed (App Server stdio) | **V** protocol + live events (started / progress / completed) | **V** `turn/start` (live) | **D** `turn/steer` / **V** `turn/start` | **D** `thread/resume` | **V** `thread/start` + `turn/start` (live, owner approval) | **D** `turn/interrupt` | **D** server request → popup → accept/decline | **V** `account/read` = chatgpt | **D** `thread/tokenUsage/updated` tokens (no dollar figure: subscription) |
| Codex, thread in the shared daemon (`codex --remote`) | **D** via `codex app-server proxy` | **D** | **D** | n/a | n/a | **D** | **D** | **V** | **D** |
| Codex, pre-existing TUI / Codex app (own server) | **V** metadata + stored turns via `thread/list` / `thread/read` | snapshot only | U `UNSUPPORTED_ATTACH` | **D** offer "resume under JARVES" (owner approval, never while active) | n/a | U | U (its own UI) | **V** | U |
| Codex, cloud task | U (not on this PC) | U | U | U | U | U | U | U | U |

Every D cell is covered by a contract test against `tests/fakes/fake_claude_host.py` / `fake_claude.py` /
`fake_codex_app_server.py` (which emit the recorded shapes in `tests/fixtures/agents/`). The O cells and the
real-tool runs of D cells are the owner script rows OE-01..OE-14 (see `SWV4_E_ORCHESTRATOR.md`).

## 4. Chosen design (smallest that works)

- **One local transport**: a named pipe `\\.\pipe\jarves-agents-<hash of the profile home>` on Windows, a Unix socket
  (0600) on Linux, through `multiprocessing.connection` (stdlib) with an HMAC challenge keyed by a per-instance token
  in `<home>/agents/bridge.token` (written owner-only). Only `send_bytes`/`recv_bytes` (JSON) are used — never
  pickle. No TCP port, no Redis/Kafka/Docker. The Codex adapter needs no bridge (JARVES is the App Server's client
  over stdio).
- **Plugin** `jarves` (generated into `<home>/agents/claude-plugin/` by `jarvis agents install-plugin claude`): the
  hooks call `python -m jarves.orchestrator.hook --home <home>`; `.mcp.json` starts
  `python -m jarves.orchestrator.channel --home <home>` (a stdio MCP server: channel + permission relay + the tools
  `jarves_report_status`, `jarves_reply`, `jarves_heartbeat`, `jarves_inbox`). When the bridge is down, hook events
  go to a bounded spool and are replayed on the next start (UC6).
- **Registry / inbox / audit**: `<home>/agents.db` (SQLite WAL), one per profile — kept out of `store.db` so that
  "delete all conversations" and history off do not wipe the agent registry. Envelope as in #49.
- Event-driven: no polling loop. `claude agents --json` runs on demand (list/status/digest), at most once per 30 s
  in the background only while a session is registered as live.


**Update after implementation (20 Oct 2026):** the orchestrator itself was run against the real tools (`core/tools/agents_live_check.py`): managed Claude start / tell / stop with cost and auth label, and managed Codex start + turn with completion and token events, are VERIFIED live. See [SWV4_E_ORCHESTRATOR.md](SWV4_E_ORCHESTRATOR.md) section 3.
