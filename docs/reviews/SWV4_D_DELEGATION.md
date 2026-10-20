# SW-V4 D — Client-mode GPT-Live delegation: strict budget and plan-backed reasoning (#14, #17)

Package D of `docs/JARVES_SW_V4_WORK_PACKAGE.md`. Branch `v4-d-delegation`. Author: Opus 5.5 (agent). This file is
the dated research note (scope item 1), the design record, and the test/live evidence.

## 1. Research note (checked 2026-10-20)

Sources: [OpenAI live-delegation guide](https://developers.openai.com/api/docs/guides/live-delegation) (fetched
2026-10-20), the installed `openai` SDK **3.35.1** types (`openai/types/live/client_delegation_param.py`,
`delegation_created_event.py`, `commentary_append_event.py`, `session_config_param.py`), and one live protocol probe
against the real `gpt-live-1` (20 Oct 2026, recording: `core/tests/fixtures/live_client_delegation_events.json`,
session id and audio redacted).

| Question | Finding |
|---|---|
| How is client mode chosen? | `session.start` with `"delegation": {"type": "client"}`. Fixed at session creation: changing the delegation of a running session fails (`immutable_field_update`), so a mode change applies to the NEXT conversation. Other documented types: `responses`, `decisions` (SDK). |
| What does the delegation event carry? | `session.delegation.created {event_id, offset_ms, delegation: {id, type: "delegation", target: "client", response_id: null}}`. **Metadata only, no task text.** The app builds the backend request from the transcript (`session.input_transcript.delta` / `session.output_transcript.delta`) and its own state. |
| When does it arrive? | Probe: 0.38 s after the last input-transcript delta of the question; the user's words were complete at that moment (before the turn is flushed), so JARVES takes the still-unflushed words too (`LiveVoice._convo_snapshot`). |
| How is the result returned? | No separate result event. `session.commentary.append {delegation_id, content, event_id}` (spoken; "GPT-Live is trained to paraphrase") or `session.thinking.append` (silent context). `content` ≤ 500 tokens. Several appends per delegation id are allowed. A non-null `delegation_id` must be a KNOWN client delegation, else an `error` with our `client_event_id`. Acks: `session.commentary.appended` / `session.thinking.appended` with `client_event_id`. Probe: the appended result was spoken 0.14 s after its ack. |
| Progress | `session.thinking.append` (quiet) or `session.commentary.append` (spoken) with the delegation id; JARVES keeps its existing spoken "still checking" note (after `voice.progress_after_s` of silence). |
| Timeout / cancel | **No documented timeout and no client cancel event.** "Interrupting the spoken conversation leaves backend work running": the app must cancel its own backend work and confirm before telling the user. JARVES cancels its HTTP stream, settles the reservation, and confirms silently (thinking) to GPT-Live. |
| Errors | No client-specific error codes are documented; an unknown delegation id is invalid. JARVES treats a rejected append, or a session error mentioning delegation, as a PROTOCOL failure -> fallback ladder. |
| Billing | The page has no billing information. In client mode GPT-Live bills only its voice minutes; the backend request is whatever JARVES sends (Responses API on the key, or the ChatGPT plan). |
| Concurrency | Not described. "Live speech and delegated work continue independently." JARVES runs several delegations at once, each reserved separately; a delegation for a NEWER user turn supersedes (cancels) older ones, two of the same turn both run. |

### Measured added latency (10 runs each, live, 20 Oct 2026)

`core/tools/suite_v4_delegation.py --latency 10`: the same 10 questions in server mode (GPT-Live's own Responses
delegation) and client mode (JARVES runs the same `gpt-6-luna` priority backend itself). Metric: last input-transcript
delta of the question -> first assistant transcript after the backend result. Raw data:
`docs/reviews/SWV4_D_DELEGATION_LATENCY.json`.

| Mode | n | p50 (s) | p95 (s) |
|---|---|---|---|
| server (Responses delegation) | 10 | 5.96 | 11.29 |
| client (JARVES runs the backend) | 8 | 5.54 | 7.73 |
| **added by client mode** | | **-0.42** | **-3.56** |

Two client runs (sunset time, Bitcoin) are not counted. They hit the per-question cap bug found by this run: the worst-case estimates were summed, so a get_time continuation was refused. It is fixed in 1c6f15a, with a regression test. Those two questions also show that the backend called get_time before searching. Backend time dominates both modes.

Acceptance "added p95 ≤ 400 ms over server delegation": **PASS** (client is not slower; JARVES calls the backend
directly over a warm HTTP connection while server mode adds GPT-Live's own hand-over).

## 2. Design (what was built)

| Piece | Where |
|---|---|
| Delegation engine: receive the event, build the input from the transcript, reserve, run, stream progress, return the result, settle | `core/src/jarves/voice/delegation_client.py` (`ClientDelegation`) |
| Strict cap: 1 `ai_requests` unit + the request's worst-case micro-USD reserved in ONE SQLite transaction (cross-process, the existing reservation table) BEFORE sending | `Budget.reserve_many`, kind `delegation_usd_micros` (`core/src/jarves/budget.py`) |
| Worst case per request | every request byte as one input token, `max_output_tokens`, at most `ai.delegation_max_searches` web searches (Responses `max_tool_calls`) each with $10/1k + 8k search-content tokens, the service-tier multiplier; unknown model = the priciest known price |
| Settlement | provider-reported usage x `[pricing]` (once); a request that provably never left: released; sent with unknown outcome (cancel, drop, timeout): counted at input + output allowance + every search seen (+1 unseen unless the answer text had started), never more than its reservation |
| No double billing | SDK `max_retries=0` for delegation (a hidden SDK retry would be an unreserved second request); no automatic retry; every settle is once (expiry-counted rows add nothing) |
| Per question | `ai.delegation_turn_usd` (settled spend of the turn + the next worst case) and `ai.delegation_turn_requests` (tool steps) |
| Daily | `ai.delegation_daily_usd` (strict, micro-USD) + the existing `ai.daily_request_limit` |
| Honest cap answer | spoken via commentary: "הגעתי לתקציב היומי לחיפושים, אז את זה אני לא יכול לבדוק עכשיו. אפשר לשאול אותי משהו שאני יודע בלי לחפש." (English when the user speaks English); health "degraded" with the action |
| Routes | `api_key` -> Responses API (live Skill tools + hosted web search, `store:false`); `chatgpt`/`combined` -> the official ChatGPT-plan route (`ChatGPTBrain._request`), $0 API for the reasoning. Plan limit -> AI pauses, honest answer, **never** the API (owner rule since cc5d317) |
| Cancellation | a newer user turn supersedes older lookups; a bare "עזוב / לא משנה / never mind" while a lookup runs cancels it; session end cancels all; a Skill side effect is never cut in half (the tool finishes, the turn stops) |
| Timeout | `ai.delegation_timeout_s` (25 s): cancelled, settled, "לא הצלחתי להביא את זה בזמן, לנסות שוב?" |
| CV-15 hand-over in client mode | the Core runs the turn itself through the engine (reserved first), appends with `delegation_id: null` |
| Fallback ladder | `DelegationState`: client -> (protocol failure, and only with `ai.delegation_fallback = "server"`) server delegation with the counting cap -> text-only (no `delegation` in the session; GPT-Live says it can't look things up). Logged `DELEG_FALLBACK`, health `delegation` degraded, persisted in `delegation_state.json`; a new Core start or `jarvis budget reset-ladder` tries client again. A rejected append is still delivered in the same conversation (general commentary). Never a silent route or billing change |
| Status | health component `delegation`; Core snapshot `delegation` (mode, step, reason, in-flight jobs, counters, caps, today's USD used/held/limit, live reservations of all processes) |
| Settings | `ai.delegation_mode` (= the `client_delegation` feature switch; default `server` until OD-15), `ai.delegation_fallback`, `ai.delegation_daily_usd`, `ai.delegation_turn_usd`, `ai.delegation_turn_requests`, `ai.delegation_max_searches`, `ai.delegation_timeout_s`; on the dashboard Settings page (Cost limits) |
| CLI | `jarvis budget status [--json]`, `jarvis budget test-cap` (free offline proof), `jarvis budget set --daily-usd/--turn-usd/--turn-requests/--searches/--timeout/--fallback/--mode`, `jarvis budget reset-ladder`; parity table `client_delegation -> budget` |
| Dashboard | Costs page card "Spoken lookups: strict budget" (`dashboard_static/delegation.js`, `GET /api/delegation`, refreshed every 2 s: used / held now / cap / left, mode + ladder step, open reservations); English + Hebrew catalog keys |
| Feature registration | `client_delegation` (features.py, risk medium, default off, capability row Windows `implemented_unverified`, Pi `planned` -> #14), guide `guides/data/client_delegation.toml` (Costs), wizard group Voice, add-on bundle check, Hebrew texts (hebrew.py) |
| Billing truth | `billing_routes`: combined + client -> `voice_backend = chatgpt_plan` |

## 3. Automatic tests

`core/tests/test_delegation_client.py` (38 tests, offline, free, event-driven waits; plus the package regression run: 287 tests across the delegation, live, log-lint, redaction, CLI-parity, guides, Hebrew and recall suites, 0 failed, 0 skipped):

- property test: **50 random overlapping-turn schedules** (fixed seed 20261020) through the real LiveVoice + engine +
  Budget: spend never exceeds the cap, every reservation settled or released;
- cross-process race: 3 processes reserve against one SQLite file: exactly 14 x $0.007 fit under $0.10;
- crash recovery: a dead owner's reservation is COUNTED by the next start, never freed, settled once;
- the real `openai` SDK against a local HTTP Responses server (`openai_fakes.ResponsesHTTPServer`): answer from the
  transcript, cap reached (the request never left), tool call + continuation, per-question cap, barge-in cancel,
  supersede, two delegations of one turn, 30 s-style slow backend timeout, network drop, HTTP 500 (no hidden retry);
- the ChatGPT-plan route (mock transport): combined mode answers with $0 API, plan 429 pauses and never touches the API;
- the fallback ladder (rejected append -> text-only / server, resend, reset), a start error;
- contract: the recorded real session validates against the SDK model and drives the engine;
- surfaces: billing routes, feature switch <-> config mirror, wizard/parity, CLI (`status`, `set`, `test-cap`,
  `reset-ladder`), dashboard API + static wiring;
- a real Core end to end (features, budget, health, snapshot, log codes; no key in the log).

Fakes: `core/tests/openai_fakes.py` (`FakeLiveServer` = a scripted GPT-Live that validates delegation ids like the
real one; `ResponsesHTTPServer` = real HTTP + SSE for the real SDK); `core/src/jarves/voice/delegation_selftest.py`
(the same harness for `jarvis budget test-cap`).

## 4. Live scenarios 51-62 (real GPT-Live, virtual mic/speaker, silent)

`uv --directory core run --no-sync python tools/suite_v3_voice.py --group delegation` (scenarios in
`core/tools/suite_v4_delegation.py`). Evidence class: `virtual`.

Final status of each scenario after fixes. Several were re-run after a live finding was fixed; the reason is given.

| # | Scenario | Result | Evidence (key numbers) |
|---|---|---|---|
| 51 | simple lookup | PASS | DELEG_DONE in 4.26 s, $0.0115 settled, 0 open reservations |
| 52 | web search | PASS | 4 web-search stage events; answer spoken |
| 53 | two overlapping questions | PASS (3rd run) | Haifa weather and New York time both said; 0 open. Run 1: the criteria were too weak and the second question went unanswered. Run 2: an unsaid first result was dropped. Fixed: unsaid results are carried into the next request, and a question asked during a lookup is handed back. The scenario now runs full duplex, because half duplex cuts overlapping speech |
| 54 | barge-in "עזוב, לא משנה" | PASS (4th run) | DELEG_CANCELLED user_cancel; 0 open; no re-delegation; counted $0.0247 (sent, outcome unknown). Fixes along the way: the cancel regex now accepts several phrases; the CV-15 watchdog no longer hands over a cancel phrase or anything in the 10 s after a cancel; a deterministic 10 s local backend is used; full duplex |
| 55 | cap reached mid-conversation | PASS (2nd run) | First lookup answered ($0.0102). The cap was then lowered to $0.0202, and the next 2 requests were refused before sending. No backend start after the refusal; spend $0.0102 ≤ cap; spoken "הגעתי לתקציב היומי לחיפושים...". Run 1, with a $0.03 cap from the start, refused even the first request: the worst case of one request with 2 searches is about $0.025-0.036 |
| 56 | plan route (combined) | PASS (2nd run) | Route chatgpt_plan, 2 plan responses, 0 API backend requests, API USD $0 for the reasoning. Run 1 asked a general-knowledge question, which GPT-Live answered without delegating |
| 57 | plan limit (fake 429) | PASS | 1 plan hit, CHATGPT_LIMIT, 0 API requests, honest pause message |
| 58 | network drop | PASS | DELEG_FAILED; exactly 1 request (no hidden retry); honest failure; 0 open |
| 59 | 30 s slow backend | PASS | "עדיין בודק" / "זה לוקח קצת יותר זמן", then DELEG_TIMEOUT at 25 s and "לא הצלחתי להביא את זה בזמן, לנסות שוב?"; 0 open |
| 60 | tool call in a delegated turn | PASS | remember ran in the Core; memory saved; 2 requests in the turn, each reserved |
| 61 | Hebrew and English | PASS (2nd run) | Hebrew 101/0 letters, English 0/94. Run 1: GPT-Live gave no answer at all to the English question after the existing CV-05 language note (no delegation happened). This is not a delegation issue; it is reported to package C (voice) |
| 62 | cost reconciliation | BLOCKED (OD-05) | No read-only admin key for OpenAI's Usage API. JARVES side: every settled value is the provider's per-response usage × [pricing] |

Acceptance status:
- Live 51-61: PASS (virtual evidence).
- Added latency: PASS (p95 is lower than server mode).
- Cap test: spend ≤ cap + 0.
- Combined mode: $0 API for the reasoning.
- ±10 % reconciliation: BLOCKED on OD-05.

## 5. Spend

Within the $5 slice of the shared $25 cap. The figures are estimates from JARVES's own meters. The OpenAI bill is the truth.

- **GPT-Live voice:** 1120 s across 19 test homes, plus the raw protocol probe (about 13 s): about **$0.94**.
- **Delegated backend (client mode, settled):** $0.395 counted. $0.086 of that was counted against fake local backends and was not billed. The real figure is about **$0.31**.
- **Server-mode backend** (10 latency turns, not metered in USD by JARVES): about **$0.11** (10 × ~$0.011).
- **Total: about $1.4.**

## 6. Owner decisions and open items

- **OD-15** (default mode): recommendation from the measurement: client mode can be the default (no added latency
  measured, strict cap); fallback `local` (honest "can't look it up") unless the owner allows `server`.
- **OD-05** (read-only admin key): scenario 62 (reconciliation against OpenAI's Usage API) needs it.
- Owner rows OD-T1..OD-T3 (7.10) need the owner; agent rows AD-01..AD-04 are covered by scenarios 51-62.
