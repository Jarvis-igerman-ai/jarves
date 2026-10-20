# SW-V2 WP-C: web-search reliability, visible progress, background tasks

Issue [#17](https://github.com/igalgerman/JARVIS/issues/17), items 4, 5 and 6. Date: 19 Oct 2026. Branch: WP-C worktree (not merged; the lead integrates it).

## 1. Web-search stall (item 4)

**What the real session shows.** The user asked "תבדוק בחדשות מה המצב של מניית אינטל" at 14:14:57. JARVES said "שנייה, אני בודק", then gave two progress notes. After 90 s it still had no answer. The log has no `LIVE_TOOL_CALL` and no `LIVE_BACKEND_DONE`. So the delegated backend response (GPT-6 + hosted web search, run **on OpenAI's server**) never finished. Nothing was visible because every backend event was logged only at VERBOSE.

**Root cause (owning layer: Core, `voice/live.py`).** The Core had no deadline for a delegated response. GPT-Live's Responses delegation is owned by the server: the API offers no client cancel (SDK `openai.types.live`, client events checked 19 Oct). The front model therefore kept improvising "עוד רגע". **UNVERIFIED:** why the server stalled. A silent re-run of the same question answered in 6.6 s.

**Fix.**
- **Logging.** Every stage is now logged at INFO (DEBUG for text and summaries), on every route, by `brain.BackendTrace`:

  | Stage | Codes (prefix `LIVE_BACKEND` for GPT-Live, `TASK_BACKEND` for tasks, `CHATGPT_BACKEND` for the plan, `LIVE_RETRY_BACKEND` for the retry) |
  |---|---|
  | request start (response id, model) | `_START` |
  | web search in_progress / searching / completed | `_WEB_SEARCH status=…` |
  | search finished (query, sources) | `_WEB_SEARCH_DONE query=…` |
  | source page opened | `_READING` |
  | first answer text | `_WRITING` |
  | answer text / reasoning summary (DEBUG) | `_TEXT`, `_REASONING` |
  | end: status, `backend_s`, searches, `usage_in` / `usage_out` / `usage_reasoning`, error or reason | `_DONE` |

  Also logged: `LIVE_DELEGATION` (each GPT-Live delegation, with its target) and `AI_REQUEST` / `AI_REPLY` (typed chat, now with duration and usage). Usage fields avoid the word "token" because `logs.scrub` masks such fields as credentials. Query, answer and summary texts go into content fields, which privacy mode masks.
- **Hard deadline and one retry.**
  - `voice.backend_deadline_s` defaults to 25 s (measured lookups take 2 to 13 s). A Skill call or an on-screen approval does not count toward it.
  - When the deadline passes, the Core gives up on that delegation (`LIVE_BACKEND_STALL`) and stops the progress notes.
  - It then retries **once itself** (`LIVE_BACKEND_RETRY`): `tasks.make_retry_lookup` uses the API key, the user's last utterances and the backend's last search query, with its own 24 s deadline.
  - It then hands GPT-Live either the answer or the fixed honest line **"לא הצלחתי להביא את זה, ננסה שוב?"**.
  - If the original answer arrives late, it is spoken, the retry is cancelled (`LIVE_BACKEND_LATE`, `LIVE_BACKEND_RETRY_CANCELLED`) and nothing is said twice.
- **Progress notes.** At most two notes per delegation (this was already the case). A note is now never spoken while the user is talking or while their words are still being transcribed. The user can keep talking during a lookup, and GPT-Live answers them meanwhile.

## 2. Visible thinking and progress (item 5)

- `assistant.activity {kind, text, detail}` now follows the backend: "חושב…", then "מחפש ברשת…", then **"מחפש: <query>"**, then "קורא מקורות…", then "מנסח תשובה…", and "מנסה שוב…" during the retry. `detail` shows the elapsed time ("12 שנ׳") from 3 s on and is refreshed every 2 s. The text does not change, so the pill does not blink. Kinds stay within the face pill's set (thinking / searching / reading / tool).
- **Reasoning summaries:** `ai.reasoning_summary = "auto"`.
  - Requested for background tasks: effort `low`, which the API accepts. The real run logged 2 summaries ("Researching Hebrew Speech…", "Gathering Samsung info…").
  - Requested for GPT-Live delegation **only when** `voice_reasoning_effort` is not `"none"`. The default stays `"none"`, as measured in LIVE_CONVERSATION_TUNING, so the delegation config is unchanged by default.
- To see JARVES think: run `jarvis` with log level DEBUG, or `enable_debug`, and read `jarves.log` for `*_BACKEND_*` codes.

## 3. Background tasks (item 6): `core/src/jarves/tasks.py`

- **Tools** (for the backend model, both GPT-Live delegation and typed chat):
  - `task_start(description)`: action, pre-authorized by the Core;
  - `task_status(task_id?)`: read;
  - `task_cancel(task_id?)`: action.
  - The backend prompt (`brain.INSTRUCTIONS`) says: use real research → `task_start`; quick facts are never background tasks.
- **TaskManager:**
  - at most 3 running and 5 queued tasks (`ai.task_max_running`);
  - **one budget unit is reserved at start**, and the task is refused when the daily ceiling is reached; a task cancelled while queued releases its unit;
  - cancellation;
  - an overall timeout (`ai.task_timeout_s` = 180 s): two attempts of up to about 89 s each, so one retry;
  - shutdown cancels running tasks.
- **Runner:** `ResearchRunner` uses the API key (OpenAI Responses API, streamed, `store:false`, hosted web search, reasoning summary). With `ai.connection = "chatgpt"` it uses `PlanResearchRunner` on the plan instead (new `ChatGPTBrain.lookup`), so the API key is never used there. With `combined`, tasks use the API key, like the GPT-Live backend.
- **Announcement:**
  - `LiveVoice.announce()` waits for a good moment: the user quiet for 1.5 s, JARVES not speaking, no lookup, tool or approval in flight.
  - It then sends a commentary note, and GPT-Live words it naturally.
  - The server checks notes against the 500-token limit only after receiving them. A real run hit this limit, so a rejected note is resent once at half length. Instructions come first and the result last, capped at 600 characters.
  - If no conversation is open, the result is announced when the next one starts.
- **Screen (message shape, for the face UI owner):** `tasks.update {"tasks": [{id, description, status, step, elapsed_s, created, finished, result, error}]}`. It is sent on every change and lists the active tasks plus the last 5 finished ones. Status is one of `queued|running|done|failed|timeout|cancelled`. Godot ignores this message today (unknown types fall through); a task-list renderer is an integration item.
- **Dashboard hook (WP-B):**
  - Each finished task is appended to `<home>/tasks.jsonl`: `Task.record()` = id, description, status, created, started, finished, duration_s, result, error, steps, attempts, usage, queries, announced.
  - Read it with `jarves.tasks.read_history(path, limit)`: newest first, last line per id. The file is trimmed to 500 lines above 1 MB.

## 4. Relationship to #14 (client-mode delegation)

- **What #14 is.** Client-mode delegation (`delegation.target = "client"`) would let the Core own every backend request: budget before sending, real cancel and deadline, and the result returned with `session.commentary.append(delegation_id=…)`.
- **What this package does instead.** WP-C does **not** switch to client mode: that would change billing, budget and turn-taking behaviour, which needs its own measured test. The pieces built here are its building blocks:
  - `ResearchRunner`: deadline, retry, budget per attempt and tracing;
  - `BackendTrace`;
  - `TaskManager`;
  - `announce()` and `_send_note()`: commentary sent at a good moment, with rejection handling.
- **Recommendation.** Implement #14 as "client delegation → ResearchRunner with the device tools". The stall workaround in `live.py` would then disappear.

## Tests and evidence

- **Offline:** `core/tests/test_tasks.py`, 23 tests. They cover:
  - trace stages, usage and failure reasons;
  - the stall: retry answer, honest failure, late original cancels the retry, a tool run is not a stall;
  - elapsed time on the pill; no progress note over the user's speech; reasoning-summary gating;
  - announce: waits for quiet, gives up at session end, too-long note is resent shorter;
  - TaskManager: concurrency bound, limit, cancel, timeout, failure, JSONL history, budget reservation and release;
  - the TaskSkill tool policy;
  - ResearchRunner: deadline then retry, progress and budget, failure after the retry;
  - connection wiring.
- **Full suite:** `python -m pytest core/tests -q` gives **335 passed, 1 skipped**.
- **Real, silent** (virtual mic and speaker, nothing played): `uv run python tools/silent_tasks.py [--stall]`.
  - Run 1, with `--stall`:
    - the research request started a background task;
    - the joke and small talk were answered meanwhile (dead air 0.1 to 0.3 s);
    - the task finished in 17.9 s;
    - the forced 1 s deadline on a weather lookup took the LATE path correctly (stall, retry, original answer, retry cancelled; spoken once);
    - but the announcement was **rejected by the server (500-token limit)**. This was fixed and covered by a test.
  - Run 2:
    - the task finished in 16.7 s;
    - the result was announced right after the joke: "אגב, חזרתי עם ההשוואה: לאפל יש את האייפון שמונה עשרה פרו…";
    - 13 `tasks.update` messages reached the fake face, and no warnings were logged.
  - A direct `ResearchRunner` call (Intel stock) took 6.6 s and logged every stage. The API accepts `reasoning.summary=auto` at effort low.
- **Cost:** about 2.8 voice minutes (about $0.14), plus backend tokens (about 10 requests, cents). The total is about **$0.20**.
- **Not tested for real:** a server stall that never answers, which cannot be forced. The retry path is covered offline only.

## Integration hooks (files owned by other packages)

- `app.py` (minimal): build the TaskManager and register the TaskSkill after `make_agent`; pass `backend_deadline_s`, `retry_lookup` and `reasoning_summary` to `LiveVoice`; add `_tasks_changed` (sends `tasks.update`) and `_task_finished` (announce); flush unannounced results after a live session starts; `tasks.shutdown()` in `stop()`.
- `config.py`: new fields `voice.backend_deadline_s`, `ai.reasoning_summary`, `ai.tasks_enabled`, `ai.task_max_running`, `ai.task_timeout_s`, `ai.research_reasoning_effort`.
- `live.py` (outside the delegation/progress functions): user utterances are kept for the retry (`_remember_user` at the two `LIVE_USER_SAID` points); the `error` handler records rejected `jarves_stall` / `jarves_task` notes (only logged, nothing is switched off).
- **WP-A (`FRONT_INSTRUCTIONS`):** suggested, not applied. Add one line: "Background tasks: when the backend says a task runs in the background, say so in one sentence; when a [task] note arrives, give the result naturally. Never say 'one more moment' more than twice."
- **Godot face (UI owner):** render `tasks.update` as a small task list.
- **WP-B:** read `tasks.jsonl` with `tasks.read_history`.

## Open questions

1. **Stall deadline.** Is the 25 s default right? A shorter one, e.g. 18 s, means less waiting but more retries on slow news searches; 12.8 s was the slowest measured.
2. **Billing.** With `combined`, background tasks use the API key, like the GPT-Live backend. Should typed-chat tasks use the plan instead? USER DECISION.
3. **Announcement wait.** An announcement waits up to 300 s for a quiet moment, then stays queued for the next conversation. Is that acceptable, or should JARVES also show or say it outside a conversation?
