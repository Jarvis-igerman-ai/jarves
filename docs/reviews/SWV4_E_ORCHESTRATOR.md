# SW-V4 package E — Agent Orchestrator for Claude Code and Codex (#49, #17)

Branch `v4-e-orchestrator` (off `origin/v4-final`, merged with it on 20 Oct 2026). Research and the capability matrix:
[SWV4_E_ORCHESTRATOR_RESEARCH.md](SWV4_E_ORCHESTRATOR_RESEARCH.md) (Claude Code 2.1.304 and codex-cli 0.163.0 on
this PC, official docs read 20 Oct 2026).

## 1. What was built

Code: `core/src/jarves/orchestrator/` (a bounded part of the Core, not a second brain).

| Module | What it does |
|---|---|
| `envelope.py` | The #49 envelope: schema_version, message_id, correlation_id, request_id, kind, expires_at, severity, hops (max 2), allow-listed payload. 9 lifecycle states. Explicit codes with English and Hebrew sentences: NO_LIVE_CHANNEL, SESSION_CLOSED, WAITING_FOR_APPROVAL, AUTH_REQUIRED, UNSUPPORTED_ATTACH, BUDGET_REACHED, AMBIGUOUS_SESSION, and more. Untrusted-text cleaning: NFC; direction, invisible and control characters removed; code blocks folded for voice; secrets redacted (logs.redact plus GitHub, GitLab, Slack, AWS, Google and private-key patterns); text capped. |
| `db.py` | `<home>/agents.db`, one per profile, WAL. Holds the registry, the durable outbox/inbox, the per-session timeline (500 rows), the approvals map, the audit (90-day retention) and day counters. Delivery is at-least-once: an idempotent enqueue, a lease plus retry with backoff (2 s to 300 s, at most 8 attempts), TTL expiry and cancellation. |
| `bridge.py` | The single local transport. On Windows it is a named pipe `\\.\pipe\jarves-agents-<hash of home>` with PIPE_REJECT_REMOTE_CLIENTS; on Linux, a 0600 Unix socket. Auth is a mutual HMAC-SHA256 challenge with a per-instance 32-byte token, rotated on every start and stored in an owner-only file. Steps time out after 5 s. Frames are JSON only (never pickle), up to 1 MB. There is no TCP port. |
| `procs.py` | The ancestor pid chain (Toolhelp32 snapshot / `/proc`). A hook or channel process proves which session started it, even through the Windows venv launcher. |
| `claude.py` | Discovery through `claude agents --json` (the documented read). It maps hook payloads to envelopes, generates, installs and uninstalls the plugin (local marketplace `jarves-local`, plugin `jarves`), and runs managed `claude -p` stream-json sessions. |
| `hook.py`, `channel.py` | The plugin's processes. The hooks: SessionStart, UserPromptSubmit, PostToolUse (throttled matcher), Notification, PermissionRequest, Stop, SubagentStop, StopFailure, SessionEnd. They spool events to `<home>/agents/spool` when JARVES is down. The MCP server provides the `claude/channel` capability, optional permission relay, and the tools `jarves_reply`, `jarves_report_status`, `jarves_heartbeat`, `jarves_inbox`. |
| `codex.py` | The App Server client: `initialize`/`initialized`, `account/read`, thread list / start / resume / read, turn start / steer / interrupt, the event stream, and approval server requests. It uses `codex app-server proxy` when the owner's shared daemon runs. |
| `policy.py` | The project allow-list (`<home>/agents/projects.json`, edited on the PC), the risky classes that need the typed code, one writer per folder, the forbidden launch flags, and writes outside the worktree. |
| `service.py` | The Orchestrator. Every operation is `call(op, ...)`, the same for voice, CLI, dashboard and the bridge. |
| `surfaces.py` | The voice tools, `jarvis agents ...`, the dashboard routes and the Core wiring. |

Wiring:
- The `agent_orchestrator` feature: default OFF, in `SAFE_OFF`, risk medium. It has a guide (`guides/data/agent_orchestrator.toml`), a capability row (Windows `implemented_unverified`, Pi `implemented_unverified` with the roadmap issue), wizard group "Coding agents / סוכני קוד", a bundle check, and Hebrew texts.
- `[agents]` config: turn cap 20 (OD-24), probe cap 10, cost-estimate cap $5/day, permission wait 60 s, refresh at most every 30 s, `allow_api_billing = false` (OD-21).
- CLI and dashboard parity entries.
- Dashboard page **Coding agents** (Activity group, en + he, 47 new catalog keys), plus a per-session page `#/agent/<id>`.
- The bridge starts and stops with the switch.
- Owner signals: a tray balloon, plus a spoken line through `live.announce` when a conversation is open and it is not quiet hours. They cover approval, blocked, done and failed, and can be set per project (`notify` in `projects.json`).

### Surfaces (the same operation everywhere)
- **CLI:** `jarvis agents list | status | ask | tell | continue | pause | interrupt | stop | start | resume | approvals | approve | deny | log | digest | install-plugin claude|codex | doctor | projects add|remove|list | reply`.
  - When the Core runs, the CLI talks to it over the bridge.
  - Without a Core it works locally: read-only views, plus queueing for the plugin's next stop.
  - `start`, `stop`, `resume` and `install-plugin` need `--yes` or a typed yes.
- **Voice tools:** `agents_list`, `agents_status`, `agents_ask`, `agents_tell` (tell / continue / pause), `agents_start`, `agents_stop`, `agents_digest`.
  - Agent text in the results is untrusted (`untrusted_output`). The registry refuses any model-origin action in the same turn; this is tested.
  - `agents_tell` needs the owner's yes. Start and stop are asked on the screen only (`voice=False`, OD-23).
- **Dashboard:** `GET /api/agents`, `/api/agents/session/<id>`, `/api/agents/approvals`, `/api/agents/log`, `/api/agents/doctor`, `/api/agents/projects`; `POST /api/agents/ask|tell|action|start|approvals/<req>`.

### How each journey works

**UC1** (a Claude Code session you started by hand):
- `claude agents --json` shows it with no plugin at all: state, waiting-for, project and age. Status and "what is it doing" come from the snapshot and are labeled with their age.
- With the plugin, hooks add the current task, files, the last summary at every Stop, and the blocker.

**UC2** (talking to that same session):
- Status questions never interrupt the worker; they are answered from the snapshot.
- Other questions and instructions take the best documented route:
  - managed session: a live turn or a steer;
  - live channel, when the session was started with the dev-channel flag: a push, and the agent replies through `jarves_reply`;
  - plugin only: queued for the next `Stop` (a block with the owner's message as the reason) or the next prompt (`additionalContext`); delivered exactly once;
  - otherwise: NO_LIVE_CHANNEL, with the install hint.

**UC3** (Codex):
- Managed threads: start, steer, interrupt and resume, with completion and token events.
- Threads started elsewhere are observed through `thread/list` and listed as `UNSUPPORTED_ATTACH`, with a "resume under JARVES" offer. Resuming needs an on-screen approval and is refused while the thread is active.

**UC4** (several agents and projects): there is a list, a digest (since the last digest, or `--hours`) with "which project is blocked", disambiguation (AMBIGUOUS_SESSION with candidates), and one writer per folder (SECOND_WRITER, with a new-worktree offer).
- New sessions start in a new worktree, `<project>/.jarves/worktrees/<slug>` on branch `jarves/<slug>`. `.jarves/` is added to `.git/info/exclude`.

**UC5** (the agent asks for approval):
- Every worker approval goes to the existing approvals popup. This covers Claude through the `PermissionRequest` hook (and optionally the channel relay) and Codex through `item/commandExecution|fileChange/requestApproval`.
- The popup shows the provider, the project and the exact command. Screen only, never voice.
- A typed code is required for: git push / merge / rebase / history rewrite, deletion, privileged commands, paid API / money, remote scripts, publish, permission bypass, and writes outside the worktree.
- Nobody answering, no screen, or no Core means no decision: the provider's own dialog stays the fallback. JARVES never approves anything by itself.
- Codex is only ever sent `accept` or `decline`, never `acceptForSession`.
- Spoofing is refused: the session's pid from `claude agents --json` must be in the hook process's ancestor chain.

**UC6** (restart): sessions of the last run show as "last known". Managed sessions become DISCONNECTED. The spool is replayed once in order. Unacked messages are leased again, delivered once, and deduplicated by message_id.

## 2. Tests (local Windows; one pytest at a time; 0 skipped)

| File | Tests | What |
|---|---|---|
| `test_agents_inbox.py` | 8 | order, idempotent enqueue, lease/retry/backoff, max attempts, TTL, cancel, crash with a reopened file, hop limit, payload allow-list and cleaning, wrapping, audit retention |
| `test_agents_bridge.py` | 8 (+1 linux_only) | auth, wrong / missing token refused at once, silent client dropped, bad and oversize frames, two profiles, token rotation, Windows pipe flag, no token in the output |
| `test_agents_contract.py` | 22 | fakes and JARVES's own requests checked against the recorded 0.163.0 schema and 2.1.304 shapes (`tests/fixtures/agents/*.json`, values-free); the channel's MCP contract; the risky-class table |
| `test_agents_claude.py` | 17 | manual sessions (fake interactive Claude Code host running the REAL plugin over the real pipe): observe without plugin, status from the snapshot, ask at the Stop with the reply, live channel push and reply, next-prompt delivery, inbox, agent status report and signal, approvals (approve / deny / typed code / wrong code / nobody), the REAL approvals popup (Tk, `--test-invoke`), spoof refused, allow-list, ambiguity, plugin install via the CLI |
| `test_agents_managed.py` | 12 | Codex start (needs approval) in a new worktree, steer, interrupt, approval accept/decline, external thread observe and resume, API key refused; Claude `-p` start / tell / pause / continue / stop with cost, permission through the plugin hook, API key refused; one writer; turn cap; forbidden flags; 2 providers × 3 projects |
| `test_agents_system.py` | 5 | real Core: voice tools, the CLI in another process over the pipe, the dashboard API, the untrusted-turn guard, start needs the screen, the switch hides everything and stops the bridge, planted secrets absent from every file in the home, crash (kill) and restart with exactly-once delivery, idle cost |

Run result after the merge with `origin/v4-final`: **72 passed, 1 deselected (linux_only), 0 skipped** (222 s).

Related suites, all passing:
- test_log_message_lint, cli_parity, guides, setup_wizard, features, capabilities, hebrew_texts, dashboard_locales, helper_approvals: 217 passed.
- redaction_planted, security, core, dashboard, recall_surfaces: 84 passed.
- `tools/log_lint.py`: 524 call sites, 0 violations.

`test_redaction_audit.py::test_planted_data_audit_of_a_real_core` fails the same way on clean `origin/v4-final`, without this package (a websocket wait timeout). It is pre-existing and was not introduced here.

Measured idle cost (`test_idle_bridge_and_registry_cost`, 10 s idle in its own process):
- CPU: 0.0 s.
- Memory: +2.5 MB working set over a Core's imports (acceptance: at most 0.2 % and 15 MB).
- Polling: none while nothing is live. `claude agents --json` runs at most once per `agents.refresh_s` (60 s; never under 30 s) and only while a session is live. The dashboard polls are rate-limited to one refresh per 30 s.

## 3. Live checks on this PC (real tools, 20 Oct 2026)

Total cost: Claude Code's client-side estimate about $0.012 across all runs, on the Max subscription (`apiKeySource: none`). The Codex turns ran on the ChatGPT Plus subscription. No API money was spent, well inside the $2 slice.

| Check | Result |
|---|---|
| `claude agents --json` | VERIFIED: the owner's 2 interactive sessions listed with pid, sessionId, status busy/idle |
| `claude -p` stream-json two turns + interrupt (probe) | VERIFIED: `system/init` (apiKeySource none), `result.total_cost_usd`, `control_response success` |
| Orchestrator managed Claude (`tools/agents_live_check.py`) | VERIFIED: start → "OK" in 19.8 s; tell → "DONE"; state, status text, cost $0.0045; graceful stop |
| Codex App Server probe | VERIFIED: initialize; account chatgpt/plus; thread/list shows the owner's threads as `notLoaded`; unknown thread → -32600 |
| Orchestrator managed Codex | VERIFIED: thread start + turn "OK" in 13.9 s, events started → progress → completed, 20 865 tokens, auth subscription |
| `jarvis agents doctor` | VERIFIED: versions, auth, agents json, bridge, projects; plugin "not installed" (not installed on the owner's Claude Code: owner gate) |

Not run live (owner gate or owner presence):
- Installing the plugin into the owner's Claude Code.
- A hand-started terminal or VS Code session with the plugin or the channel flag (UC1 end-to-end in Hebrew by voice).
- Worker approval prompts from the real tools.
- Voice scenarios 63-70 (paid GPT-Live, virtual mic).

These are owner script OE-01..OE-14 (work package 7.11). Everything the fakes cover is contract-tested against shapes recorded from the real tools.

## 4. Verified vs not

| Area | Status |
|---|---|
| Discovery of hand-started Claude sessions | VERIFIED live (agents --json) + tests |
| Plugin hooks / channel / tools on a REAL Claude Code | NOT VERIFIED live: implemented to the documented contract, run against the fake host (real plugin processes, real pipe). Needs OE-01..OE-07 |
| Channels push into a live session | NOT VERIFIED live: research preview; needs `--dangerously-load-development-channels plugin:jarves@jarves-local` (OD-20 yes) |
| Managed Claude (start / tell / stop / cost / auth label) | VERIFIED live |
| Managed Claude interrupt | VERIFIED live (probe) + tests |
| Managed Codex (start / turn / events / tokens / auth) | VERIFIED live |
| Codex steer / interrupt / approval requests | contract-tested (schema 0.163.0), not live |
| Externally started Codex = observe + UNSUPPORTED_ATTACH | VERIFIED live (`notLoaded`) + tests |
| Shared Codex daemon attach (`app-server proxy`) | implemented, NOT VERIFIED (the daemon was not running) |
| VS Code sessions in `claude agents --json` | UNKNOWN (docs silent): OE-07 |
| Approvals popup path | VERIFIED with the real Tk popup (`--test-invoke`) in tests; live with the owner: OE-06 |
| Voice by Hebrew (UC1 DoD) | NOT VERIFIED: needs the owner / paid voice suite |

## 5. Design notes and deviations

- **agents.db, not store.db.** The registry lives in its own file per profile, so "delete all conversations" and `history.enabled = false` never wipe the agent registry.
- **Package path.** The package is `jarves/orchestrator/` instead of `agents/` (the top-level name `agents` is the OpenAI Agents SDK).
- **Cross-session messaging (documented) is not used.** Posting into a manually started session from outside needs that session's secret per-session token, and the frame format is not documented. Channels plus hooks are the supported route.
- **Remote Control is not used.** It is a user UI, not an API.
- **Hook process trust.** Hook processes are matched to sessions by the ancestor pid chain, because a Windows venv `python.exe` is a launcher that adds a level. The session pid always comes from `claude agents --json`, never from the hook.
- **Untrusted paths.** Envelope paths (cwd, files) stay exact: logs.redact would rewrite the user name in them and break project routing. Paths stay local in agents.db.

## 6. Owner script (manual)

1. `jarvis features on agent_orchestrator`.
2. `jarvis agents projects add C:\work\jarvis --name jarvis --alias ג'רבס`.
3. `jarvis agents install-plugin claude` (OD-20), then `jarvis agents doctor`.
4. Start `claude` by hand in `C:\work\jarvis` → `jarvis agents list` (OE-02).
5. Ask by voice "תשאל את קלוד מה המצב בפרויקט ג'רבס" (OE-03/04).
6. For live push: start `claude --dangerously-load-development-channels plugin:jarves@jarves-local`, accept the dev-channel dialog, and ask again (OE-05).
7. Let Claude ask for `git push` → the popup with the code; deny (OE-06).
8. `jarvis agents start codex jarvis "..." --yes` (OE-08/09).
9. Then OE-10..OE-14 as in the work package.
10. To repeat the agent live check: `python core/tools/agents_live_check.py` (about a cent of subscription estimate).
