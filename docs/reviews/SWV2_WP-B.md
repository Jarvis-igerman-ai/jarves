# SW-V2 WP-B: dashboard, cost, self-diagnosis (#17 items 8, 9, 16; viewing side of 10, 11, 17)

Date: 19 Oct 2026. Issue: [#17](https://github.com/igalgerman/JARVIS/issues/17). Status: implemented and tested offline. Not yet tried by the user on a real conversation.

## What was built

| Part | File | What it does |
|---|---|---|
| History store | `core/src/jarves/store.py` | SQLite `<state_dir>/store.db`. It holds conversations, turns (user and JARVES text, time, measured reply wait), tool calls (result, duration), backend delegations (model, tier, duration, tokens when logged), events/errors and issues. It is filled by a **log sink**: `logs.add_sink`, then a queue, then one writer thread. The Core keeps logging as before and the store turns the existing codes (`LIVE_STARTED`, `LIVE_USER_SAID`, `LIVE_JARVES_SAID`, `LIVE_TOOL_CALL`, `TOOL_RESULT`, `LIVE_BACKEND_DONE`, `AI_REPLY`, `VOICE_ENDED`, `SKILL_REGISTERED`, warnings/errors) into rows. Records go through the same JSON formatter as the log file, so `log.privacy` applies. With privacy on, only lengths and timings are stored. Credentials are always masked. Records older than `history.keep_days` (90) are removed. |
| Cost | `store.py` (`session_cost`, `daily_costs`), new `[pricing]` in `config.py` | Voice cost: `voice_seconds` × per-minute price. Backend cost: tokens × model price × service-tier multiplier, plus web searches × $0.01. Responses with no token data use 3000 input / 150 output tokens each and are labelled as estimated. ChatGPT-plan typed replies cost $0. Everything is labelled **estimate**. |
| Self-diagnosis | `core/src/jarves/diagnose.py` | Rules (no LLM, $0). It runs in the writer thread after every `VOICE_ENDED`, from `jarvis diagnose`, or from the dashboard button. Issues are deduplicated by fingerprint: re-running changes nothing, and a new conversation with the same problem increases the count. A resolved issue that happens again is reopened. |
| Dashboard | `core/src/jarves/dashboard.py`, `dashboard_static/{index.html,app.js,style.css}` | Python stdlib `http.server`. Pages: Conversations (with a live banner), Transcript (bubbles, tool/backend chips with hover detail, silence markers, reply wait, cost, the conversation's issues; the live session refreshes every 2 s), Costs (30-day stacked bars, a table per day and per conversation, the prices used), Issues (evidence, resolve/ignore/reopen, run now), Memory, Skills & tools (tools per skill with 30-day calls/failures/avg time and component health), Workspace, Settings (read-only). Light and dark themes. Hebrew text uses `dir=auto` / `unicode-bidi: plaintext`. |
| CLI | `cli.py` | `jarvis dashboard [--port N] [--no-open]`, `jarvis cost [--days N]`, `jarvis diagnose [--import-log [PATH]] [--days N]`. The import skips records already in the store, so there are no duplicates. |

**Dashboard security:**
- It binds to 127.0.0.1 only.
- A random 24-byte token in the printed URL is exchanged for an HttpOnly, SameSite=Strict cookie, which every request needs.
- The Host header must be loopback (protects against DNS rebinding).
- Writes need an `X-Jarves: 1` header.
- Strict CSP (`default-src 'self'`, no inline script or style, `frame-ancestors 'none'`), `no-store`, nosniff.
- Credential-named keys are masked and e-mail addresses become `<email>`.
- The JS builds the page with `textContent` only, never `innerHTML`.

## Pricing research (checked 19 Oct 2026)

Source: [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing) and [gpt-live-1 model page](https://developers.openai.com/api/docs/models/gpt-live-1).

- **gpt-live-1**: $0.05 per minute, billed per second, not rounded up. Backend model and tool use are billed separately at normal rates.
- **GPT-6 models** (input / cached / output per 1M tokens):
  - gpt-6-luna: $0.10 / $0.01 / $0.50
  - gpt-6-sol: $2 / $0.20 / $10
  - gpt-6.1-sol: $2 / $0.10 / $10
  - gpt-6-astra: $10 / $1 / $50
- **Service tiers**: Fast (priority) = 2×, Flex = 0.5×.
- **Web search**: $10 per 1k calls.
- **Realtime** (only the alternative voice mode): gpt-realtime-2.1 audio is $32 / $64 per 1M tokens. The dashboard uses a rough $0.10/min for it, labelled as an estimate.

## Evidence from the real 19 Oct log (`core/tests/fixtures/session_2026-10-19.jsonl`)

| Finding | Severity | Evidence |
|---|---|---|
| Request stalled 98 s (never answered: the conversation ended while waiting) | high | "...מה המצב של מניית אינטל" at 17:14:57. It got 4 fillers ("שנייה, אני בודק." ...), the user asked "למה לוקח כל כך הרבה זמן", and the only backend response completed in 4.9 s with nothing after it. |
| Dead air while waiting: 54 s of silence | high | From 17:15:41 until the end, nothing was said or done. |
| Confusing filler "סגור" said 4× – the user asked what it means | high | "בטח. סוגר.", "סגור." ×2, "סגור.השתקתי…", then the user said "לא הבנתי מה אתה אומר כל פעם גור גור?" |
| The user did not understand JARVES | medium | Same moment, with the two preceding replies. |
| The user had to repeat himself | medium | "בסדר משה עכשיו", then "בסדר, מה השעה עכשיו". GPT-Live misheard "מה השעה". |
| Model kept answering while the user was talking | medium | `early_response_drops` = 12. |
| Slow tool music_play: 8.5 s | low | From LIVE_TOOL_CALL to TOOL_RESULT. |

Session cost estimate: **$0.172**. That is 3.27 voice minutes ($0.163) plus 11 backend responses at the priority tier (~$0.008, token use estimated).

Screenshots are automatic headless Edge captures of the actual dashboard serving the fixture plus a synthetic live session. They are not a hands-on UI check. Files in [swv2_dashboard/](swv2_dashboard/):
- [conversations](swv2_dashboard/dashboard_conversations_light.png)
- [transcript](swv2_dashboard/dashboard_transcript_light.png), [transcript dark](swv2_dashboard/dashboard_transcript_dark.png)
- [live](swv2_dashboard/dashboard_live_light.png)
- [costs](swv2_dashboard/dashboard_costs_light.png)
- [issues](swv2_dashboard/dashboard_issues_light.png)
- [skills](swv2_dashboard/dashboard_skills_light.png)
- [workspace](swv2_dashboard/dashboard_workspace_light.png)

## Tests

- `tests/test_history.py`: 11 tests (fixture reconstruction, cost math, diagnosis of the real log, dedup/reopen, privacy mode, measured dead air, live log sink + after-session diagnosis, disable switch, the live.py wait hook, CLI cost/diagnose/import).
- `tests/test_dashboard.py`: 9 real-HTTP tests (token/cookie, Host check, CSRF header, path traversal, CSP, every API, issue writes, no e-mail or secrets, workspace stub, plugin route, live session while it is being written).
- Full suite: 332 passed, 1 skipped. Cost: **$0**, with no paid API calls.

## Integration hooks (outside WP-B's new files)

- `logs.py`: `add_sink()` / `remove_sink()`. Sinks survive `setup()` and always get INFO and above.
- `app.py` (2 lines): `self.history = store.attach(self.home, c)` after `logs.setup`, and `store.detach(...)` after `CORE_STOPPED`.
- `voice/live.py`: `_note_reply_wait()` in `_play()` at the start of each reply's audio, and `wait_s` added to both `LIVE_JARVES_SAID` logs (silence from the user's last mic voice to the reply's first audio). These are the only real reply-latency numbers. Without them, dead air can only be seen as gaps.
- `config.py`: new `[pricing]` and `[history]` sections (`ModelPrice`, `PricingConfig`, `HistoryConfig`).
- **Requests to WP-C** (backend call sites, not done here):
  - Add `model`, `input_tokens`, `cached_tokens`, `output_tokens`, `web_searches` (and `tier` if it varies) to `LIVE_BACKEND_DONE`, and `input_tokens` / `output_tokens` to `AI_REPLY`. The store already reads them, and costs then stop being estimated.
  - Log a start event for each delegation (e.g. `LIVE_BACKEND_START`) so a delegation that never completes (the Intel stall) shows as an open backend call.
- **Request to WP-D**: `jarves.workspace` with `list_files()`/`list()`, `read_file(name)`/`read(name)`, `write_file(name, text)`/`write(name, text)`. Once it exists, the Workspace page becomes editable on its own. Memory editing: define `dashboard_routes(register)` in `jarves.memory` or `jarves.curator` and register e.g. `register("POST", r"/api/memory/(\d+)", handler)`. The Memory page then reports `editable: true`. The UI's edit controls for memory still need adding when that API lands.

## Open questions / limits

- **Privacy default.** `log.privacy` defaults to false (development), so `store.db` keeps full transcripts, as the log already does. Should it keep full text by default for daily use? *Recommendation:* keep full text while tuning (the dashboard is the point), and switch privacy on for daily use. USER DECISION REQUIRED.
- **Web search and tokens.** Web-search calls and real token counts are not in the log yet (WP-C hook above), so backend cost is an estimate and web searches ($0.01 each) are not counted. This is the biggest gap in the estimate. Voice minutes, which are about 95% of the cost, are exact.
- **Optional LLM review pass.** Not implemented (rules only). Proposed as opt-in, default off, a gpt-6-luna call per session (about $0.001). Not started without approval.
- **Dashboard runs separately.** It is a separate `jarvis dashboard` process; it is not started by `jarvis` automatically, and the Godot face has no button for it yet. Could be added if wanted.
