# GPT-Live conversation tuning (2026-10-19)

Goal (user): "make the conversation much real, like a real one.. no strange pause", "good vibes".
Method: measure, change, measure, with `core/tools/silent_live.py` (virtual mic and speaker, real GPT-Live,
nothing played or recorded). All numbers below come from those runs.

## 1. What the official docs say (read 2026-10-19)

| Source | Finding | Used for |
|---|---|---|
| guides/live-delegation | `delegation.responses` accepts `model`, `instructions`, `tools`, `tool_choice`, `parallel_tool_calls`, `max_output_tokens` (at least 16), `service_tier` (`"priority"` = Fast mode), `reasoning`, `text`. Changes can be made live with `session.update`. Latency advice: "Tune reasoning effort to the lowest level that reliably completes the task", keep answers concise, return tool results promptly, run independent lookups at the same time. Recommended starting model: `gpt-6-luna`. | backend settings |
| SDK `openai` 3.35.1 `types/live/responses_delegation_config_param.py` | `reasoning.effort`: none, minimal, low, medium, high, xhigh. `text.verbosity`: low, medium, high (backend text only, not the spoken delivery). `service_tier`: auto, default, flex, priority, ... | config literals |
| models/gpt-6-luna | `reasoning.effort` supports none to max, **default `medium`**. Fast mode is available at 2x the price ($0.10 / $0.50 per 1M tokens on Standard). | effort "none" + priority |
| guides/fast-mode | "up to 2.5x faster speeds and more consistent latency". Turned on with `service_tier` `fast`, or `priority` for the same behaviour. | `ai.voice_service_tier` |
| guides/live-prompting | Delegate only when the request needs a backend capability. Answer directly from the conversation or a result that is still current. Never guess while waiting. You can prompt the model to acknowledge while the work runs. Routine answers: "one or two short sentences". Pronunciation hints go in the prompt. "Keep listening while the user pauses to think." Greeting: put a developer message in `session.input`. | FRONT_INSTRUCTIONS |
| guides/voice-websockets, live-conversations, voice-server-controls, SDK `session_config_param.py` | **GPT-Live has no turn-detection, VAD or eagerness settings.** Turn-taking is native to the model, and the startup config holds only model, instructions, audio format and voice, delegation, input, triggers and store. The model is full duplex. The docs say nothing about echo cancellation, and "GPT-Live does not provide an output VAD or noise gate". The client events are `session.thinking.append` (silent context) and `session.commentary.append` (context to speak); with Responses delegation `delegation_id` must be `null`. `session.instructions.append` "can interrupt speech in progress", so we do not use it. | clock context, progress notes, keep the half-duplex gate |
| live-conversations | Greeting: "Test greetings where the caller starts speaking during the greeting". The greeting may not be spoken, so check the output audio. | problem 1 analysis |

Turn detection / eagerness: there is **nothing to tune** in the API (checked in the guides and the SDK types).
The half-duplex echo guard stays, because the docs give no supported echo-cancellation path for laptop speakers.
Its tail length is now `voice.echo_tail_s`.

## 2. Problem 1: the first question got no answer

Root cause: a bug in the test, not in the product's mic path. `quiet()` in `silent_live.py` compared against
`VirtualAudio.last_play = 0` at startup, so it returned at once. The greeting only starts playing after
`present()` (when the face connects), so turn 1 was injected **while the greeting was playing**. The half-duplex
gate correctly replaced it with silence (the run's "said" for turn 1 was the greeting itself). The same test bug
(it moved on once a backend answer was still pending) made the late weather and news answers talk over turns 4
and 10, so those questions were lost too.
Fix: the harness waits for the greeting to start and finish, and after each turn it waits until a backend answer
that finished after the last sound has actually been spoken. Result: 0 lost turns out of 12.

Product note: a user who talks over the greeting (or over any answer) on laptop speakers is still gated out.
That is the documented trade-off of half duplex. Use headphones or an AEC mic with `voice.half_duplex = false`.

## 3. Changes

- Backend (`[ai]`): `voice_reasoning_effort = "none"` (the model default was medium), `voice_service_tier =
  "priority"` (Fast mode, about $0.0005 per lookup on luna), `voice_max_output_tokens = 800`.
  `parallel_tool_calls` stays **false**: the Core sends one `response.create` per function call, and the F1 budget
  reserves exactly one continuation for each. Parallel calls would start duplicate spoken answers.
- Front prompt: answer DIRECTLY (small talk, jokes, feelings, maths, earlier context, time and date). Delegate only
  for fresh facts and actions. Before a web lookup, say one short, varied acknowledgement that names what is being
  checked ("רגע, אני בודק מה מזג האוויר בתל אביב"). Quick actions get at most a word. No repeated preambles.
  Short answers. Laugh at jokes and be gentle with sad news. Israeli number pronunciation and a pronunciation hint
  for the name.
- Clock context (`voice.clock_context`): once a minute, `session.thinking.append` (silent) with the local time,
  day and date. "What time is it?" no longer goes to the backend.
- Progress notes (`voice.progress_after_s = 2.5`): if a lookup is still running and there has been 0.8 s or more
  of dead air, `session.commentary.append` asks for one short "almost there". If the user still waits 4 s more,
  a second note with different words is spoken. There are never more than two per lookup, and never over speech.
  If the server rejects a clock or progress event, that feature turns off quietly (voice health stays ok).
- Backend prompt: start with the answer, 1-2 sentences, no "let me check"; news = two headlines of under 12 words
  each; one search is enough; call `recall` with no query for "what do you remember" (a query is a literal text
  match, which returned "nothing saved" in one run).
- Measuring: `LIVE_BACKEND_DONE backend_s` is logged (timing only, no content). The harness reports
  delegations, backend_s, speech segments, and the session's wall minutes.

## 4. Before / after (silent runs, same 12-turn script)

| Metric | Baseline (8a53301) | After (full run) |
|---|---|---|
| Turns lost (no answer to the real question) | 3 (#1, #4, #10) | 0 |
| Dead air avg / max | 0.32 / 0.67 s (over turns that answered, some from stale answers) | 0.40 / 0.88 s |
| Turns with a gap over 1 s inside the answer | 3, plus 2 answers that came after the next question (4-10 s) | 3 (all after a spoken "checking" sentence) |
| "What time is it" | delegated: "רגע, בודק." + 1.9 s gap, backend 0.86 + 1.09 s | direct: 0.38 s dead air, no backend |
| Weather (web) backend | 4.03 s, answer heard only during the next turn | 1.3 to 2.2 s, no gap after the ack (run 3 and full run) |
| News (web) backend | 10.2 s | 2.9 to 5.0 s typical; one outlier at 12.8 s (progress note at 5 s, then a 7 s gap; the second-note logic was added after this) |
| Math 347x18 | lost | direct, correct (6246) |
| Memory recall | ok | ok (after the recall-without-query rule) |
| Joke / sad / thanks face | -, cry, love | laugh, cry, love |
| Context ("what did I say about the colour") | ok | ok |
| Idle check (25 s) | not run | disconnected after 23.7 s of silence, 0 disconnects during turns |

## 5. Still imperfect

- Web lookups still have 1 to 2 s of quiet after the acknowledgement. That wait is explained, but it is not zero.
  Backend web search varies (2.9 s to 12.8 s for the same news question).
- GPT-Live sometimes starts answering at the user's last word (dead air of about -0.1 s, "heard" cut short).
  With half duplex the rest of the question is then gated out. There is no eagerness setting to tune.
- The second progress note is covered by offline tests only (no slow lookup has happened in a live run since).
- The silent harness uses Windows TTS for the user's voice; real voices and rooms will differ.

Live voice used for this work: about 12.5 min wall time over 7 sessions (about $0.65 voice, plus small luna tokens).
