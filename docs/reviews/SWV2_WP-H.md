# SW-V2 WP-H: dashboard wiring, exact costs, settings, AI review, open-on-request (Issue #17)

Date: 19 Oct 2026. Model: Claude Opus 5.5. Branch: WP-H worktree, based on `swv2-integrate` (WP-A to WP-G and WP-I merged in). Not pushed; the lead integrates it.

## What was built

| Area | Files | What it does |
|---|---|---|
| Workspace editing | `dashboard.py`, `dashboard_static/pages.js` | Talks to WP-D's `jarves.workspace.Workspace` directly. Supports list, read, **preview diff**, save (validated by WP-D: secrets, size and generated files refused), **history**, compare a version, **restore** and **undo**. Daily curator notes are readable. Opened from inside the Core, the dashboard shares the Core's Workspace, so a SOUL/USER edit also reaches a running conversation. |
| Memory editing | `dashboard.py`, `pages.js` | Add, edit, delete, and **forget one conversation's auto-learned facts** (same semantics as `Curator.forget_session`). A persistent **undo** journal (store.db meta, last 30 actions) brings back deleted facts with the same ids. Learning switches are linked to the Features page. |
| Exact costs | `store.py`, hooks in `brain.py` | The store reads WP-C's `*_BACKEND_START` / `*_DONE` events: `model` and `tier` (the server's real `service_tier`), `usage_in`, `usage_out`, `usage_cached` and `web_searches`. `web_searches` counts **calls**, repeats included, billed at $10 per 1k; `searches` (distinct queries) is the fallback for old logs. Each request's open row (`started`) shows up before it finishes, so a stall is visible. Dated model snapshots are priced by longest prefix. Typed chat `AI_REPLY` now logs model, cached tokens and web-search calls. New **typed conversations**: core `REPLY` now carries `utterance` (secret-looking text such as codes is masked to its length) and `typed`, and the store groups exchanges less than 10 min apart. |
| Background tasks | `dashboard.py`, `pages.js` | Tasks page from `tasks.jsonl` (`tasks.read_history`). Tasks also appear in their conversation's transcript. With privacy on, only lengths are shown. |
| Local time + UTC | `pages.js` | Every time shown is local, with UTC next to it (the log is in UTC). |
| Settings | `config_edit.py` (new), `dashboard.py`, `pages.js` | Edits an **allowlist** of 26 non-secret `config.toml` values, e.g. `voice.backend_deadline_s` (default 25), `voice.idle_disconnect_s`, `voice.greeting_delay_s`, cost limits, tiers, privacy, history thresholds, `history.llm_review_daily_max` and pricing. Every value is coerced to its pydantic field type, and the **whole file is validated by `Config`** (bounds, choices, loopback, no secrets) before writing. The file is edited **line by line**, so comments survive. The old file is backed up (`config.toml.bak-*`, 10 kept), the write is atomic and **Undo last save** is available. All of these values apply at the next start. Voice choice is a runtime override that applies to the next conversation. On/off switches (greeting, chimes, tasks, AI review, curator) are on WP-I's **Features page**, so each thing has one switch. |
| Self-diagnosis AI review | `diagnose.py`, hook in `features.py` | After the rules have run on a finished voice conversation, **ONE** `gpt-6-luna` request (`history.llm_review_model`, "" = `ai.text_model`) gets the redacted transcript and the issues already found. It reports what the rules miss, at most 5 findings, labelled **"AI review: …"** (kind `ai_review`, evidence "it can be wrong"). Conditions: it runs in its own thread and never blocks the log writer. It is API-key route only, reserves 1 `ai_requests` from the shared daily Budget, runs at most `history.llm_review_daily_max` (20) times a day and once per conversation, and is skipped with `log.privacy` on. Its exact cost is added to the conversation. Switch: the WP-I feature `diagnosis_llm_pass`, now **ON by default**, with its default taken from `history.llm_review`. There is an "AI review now" button per conversation. |
| Logs page | `logview.py` (new), `dashboard.py`, `pages.js` | Read-only view of `jarves.log` and its rotations, newest first. Filters: level, module, event code (exact or `LIVE_*` glob), hide codes, time range, free text, Core session, file and conversation. Noisy codes are hidden by default, and follow mode refreshes every 3 s. Click a line to see its raw JSON and **jump to its conversation**; a conversation has a "Log lines" link back. Paging uses a cursor, and **Download filtered (.jsonl)** exports the current view. Masking: `logs.scrub`, with content masked again when privacy is on now. Credentials and e-mail addresses are never shown. |
| Conversations page | `dashboard.py`, `pages.js` | Search by text, date range, tool used, has-issue, errors only, voice vs typed, minimum cost and duration range. Sort by time, cost, length, turns, issues or tools, with pages. The transcript shows tools with arguments and results, backend calls (model, tier, tokens, searches), tasks, timings, dead air, the cost split, local + UTC times, and a **Raw JSON** view. |
| Open on request | `dashboard.py` (`DashboardSkill`), `app.py`, `link.py`, `godot/face/assistant_ui.gd` | Three ways in: `jarvis dashboard` (CLI, already existed); the tool **`open_dashboard`** (action, pre-authorised), which starts the dashboard once inside the Core, opens the browser and returns the local URL **without** the one-time key; and a face **"Open dashboard"** button that sends `ui.open_dashboard`, which the Core answers with `dashboard.opened {url, browser}`. Tests and silent runs (`use_real_audio=False`) never open a browser. |
| Voice wording (item 6) | `voice/live.py` | `FRONT_INSTRUCTIONS` gains the background-task line (a [task] note gives its result naturally; never say "עוד רגע" / "one more moment" more than twice; never say it is almost ready). `PROGRESS_NOTE` is now neutral ("עדיין בודק"), with no false "כמעט שם". Opening the dashboard was added to the delegate list. |

## Evidence

- `python -m pytest core/tests -q` (from `core/`, its uv venv): **533 passed, 3 skipped**.
  - New tests: `tests/test_wph_costs.py`, 6 tests: exact cost, web searches, typed conversations, AI review with a fake client (budget, once, privacy), neutral notes.
  - `tests/test_dashboard_wph.py`, 8 real-HTTP tests: workspace, memory, settings, logs, conversations, tasks, static, and the Core tool plus face message.
- `GODOT_EXE=…Godot_v4.7.2-stable_win64_console.exe pytest tests/test_face_protocol_godot.py`: **1 passed**. This includes new `dashboard.opened` messages, among them a malformed one, which caught a real type bug in the first version of the button handler.
- Packaging: `uv build --wheel` gives `jarves-0.1.0-py3-none-any.whl`, which contains `jarves/dashboard_static/{index.html, app.js, pages.js, style.css}`, `dashboard.py`, `logview.py` and `config_edit.py`.
- Screenshots: automatic headless Edge captures of the real dashboard, made by `uv run python tools/dashboard_screens.py`. It serves the 19 Oct fixture plus synthetic typed, task, memory and workspace data. These are not a hands-on UI check. Files are in [swv2_dashboard/](swv2_dashboard/):
  - [conversations](swv2_dashboard/wph_conversations_light.png)
  - [transcript](swv2_dashboard/wph_transcript_light.png) / [dark](swv2_dashboard/wph_transcript_dark.png)
  - [raw](swv2_dashboard/wph_raw_light.png)
  - [logs](swv2_dashboard/wph_logs_light.png) / [one conversation, dark](swv2_dashboard/wph_logs_conversation_dark.png)
  - [costs](swv2_dashboard/wph_costs_light.png)
  - [tasks](swv2_dashboard/wph_tasks_light.png)
  - [memory](swv2_dashboard/wph_memory_light.png)
  - [workspace](swv2_dashboard/wph_workspace_light.png)
  - [settings](swv2_dashboard/wph_settings_light.png)
  - [issues](swv2_dashboard/wph_issues_light.png)
- Cost: **$0**. No paid API call; the AI review is tested with a fake client.

## Integration hooks (files owned by others; smallest change)

- `brain.py` (WP-C):
  - `BackendTrace` counts web-search calls (`search_calls`).
  - It logs `web_searches`, `model`, `tier` and `usage_cached` on `*_DONE`, and records `cached_tokens` in its usage when it is non-zero.
  - `AI_REPLY` adds `model`, `web_searches` and cached tokens.
- `app.py`:
  - `DashboardSkill` registration and `open_dashboard()`.
  - `ui.open_dashboard` handling, `self.open_url` (None without real audio) and dashboard stop at shutdown.
  - `REPLY` gains `utterance` (masked when secret-looking) and `typed`.
- `link.py`: `ui.open_dashboard` is added to the face allow-list.
- `voice/live.py` (WP-A): the `FRONT_INSTRUCTIONS` lines above, `PROGRESS_NOTE` / `PROGRESS_NOTE_AGAIN` wording, and the config.py comment.
- `config.py`: `history.llm_review` (default true), `llm_review_daily_max` (20) and `llm_review_model` ("").
- `features.py` (WP-I): `diagnosis_llm_pass` now has default True and `config="history.llm_review"`; `test_features.py` was adjusted to match.
- `godot/face/assistant_ui.gd`: "Open dashboard" button and `dashboard.opened` caption.
- `tests/test_face_protocol_godot.py`: new message cases.
- `tests/test_dashboard.py`: the cleanup now removes only the POST route it added.
- `jarvis.cmd`: usage comment.
- `cli.py`: `_log_files` now uses `logview.log_files`.

## Open questions for the user

1. **AI review ON by default.** It costs about $0.0005-0.001 per conversation at gpt-6-luna prices and is capped at 20 a day. It sends the transcript (redacted: no keys, no e-mail addresses) to OpenAI and is skipped when privacy is on. Keep it on? The switch is on the Features page.
2. **Typed text in the log.** `REPLY` now logs what you typed (`utterance`) in development mode, so typed conversations appear in the dashboard. Text that looks like a code or password is masked. Is that acceptable, or should typed text follow `log.privacy` only?
3. **Web-search billing assumption.** Only search actions are counted as billed calls; open-page and find-in-page steps are not. Check this against your next OpenAI invoice.
