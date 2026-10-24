# V4.5 voice latency baseline (issue #122), 24 Oct 2026

**Status: automatic measurement only. NOT human-verified.** No physical microphone, speaker or camera was used.
Build: installed JARVES 4.0.1.349 (commit 07cdeb16cc5b), run with its bundled Python on this PC (Windows 11), owner's
config (`combined`, `gpt-live-1`, backend `gpt-6-luna`, greeting on), real GPT-Live over the internet, throw-away data
folder per run (the owner's data and running JARVES were not touched). Tool: `core/tools/silent_live.py` (questions
synthesised locally and injected as audio through a virtual mic; answers measured on a virtual speaker, never played).
Turns: "small talk" and "what time is it". Hebrew.

Spend: about 0.30 USD estimated (5 sessions, about 3.7 voice minutes in total, plus small backend use); cap was 2 USD.

## Per-run numbers (5 cold starts; run 2 failed)

| Stage (seconds) | r1 | r2 | r3 | r4 | r5 | median (ok runs) |
|---|---|---|---|---|---|---|
| Core ready (cold, empty data folder) | 7.8 | 6.9 | 4.9 | 6.4 | 4.8 | 6.4 |
| Loading voice -> live session started (library load + connection) | 13.7 | 11.6 | 12.2 | 12.7 | 10.2 | 12.2 |
| Session started -> first greeting audio | 2.0 | none | 1.6 | 1.5 | 1.4 | 1.5 (n=4) |
| Core ready -> first greeting audio | 15.7 | none | 13.9 | 14.2 | 11.6 | 14.0 (n=4) |
| Turn 1 dead air (end of question -> first sound) | 0.48 | - | 0.04 | -0.06 | 0.17 | 0.11 |
| Turn 2 dead air | 0.35 | - | 0.23 | 0.23 | 0.24 | 0.24 |

Pooled dead air over the 8 turns: median 0.23 s, max 0.48 s (a value at or below 0 means the model started answering
just before the injected question ended; it is early-response behaviour, not a measuring error). Answers were 1.4 to
5.2 s long. No pauses over 1 s inside answers, no disconnects in the 4 good runs.

**Finding: 1 of 5 starts failed.** Run 2: the server sent no greeting audio after `session.started`, the silent
nudge (`LIVE_GREETING_NUDGE`) did not un-stick it and the Core crashed at the 15 s start limit (`RuntimeError`, log
code `CRASH`). This is the known "greeting stall" (P0.4 Q05, previously 2 of 14); 1 of 5 here. It should be triaged as
a P1/P2 bug for the voice start path (a failed greeting must degrade to an open, silent room, not a crash).

## Tool prompt size now (before PR #71, lazy tool exposure)

Same in all runs (estimate, characters/4, from the Context trace of the live session):

| Block | chars | est. tokens |
|---|---|---|
| Tools sent to the backend (174 tools, web search on) | 13,188 | **9,819** (schemas included) |
| Front instructions (persona, self, features, guides, greeting...) | 10,168 | about 2,800 |
| Backend instructions (rules, workspace, guides) | 9,676 | about 2,300 |
| **Total** | 33,032 | **15,116** |

Compare after PR #71 with the same tool: run `silent_live.py --turns 1`, then read `context_traces` of the run's
`store.db` (`total_tokens`, `tools` count, block `backend.tools`).

## Other automatic checks (installed beta)

- `jarvis doctor`: all checks passed (key, ChatGPT plan, microphone hears sound, wake model loaded, limits 0 used).
- `jarvis selftest`: PASS, 10/10 (4.9 s).
- `jarvis speedtest --class owner` x3 (not stored): `openai_roundtrip_ms` 745, 1162, 1427 (median 1162; one tiny API
  call, network jitter). `features_list_ms` 10.8 to 13.2, `dashboard_first_load_ms` 50 to 90. Some rows
  (`spawn_to_ready_s` 18.86, `wake_to_first_audio_s` 11.3, `dead_air_p95_s` 5.22) are re-read from the stored history of
  earlier real runs and did not change between runs, so they are not new measurements. `live_started_s` and
  `first_audio_s` are n/a without `--bench`. The WARN rows compare with a history of only 1 run.

## Not measured (needs the owner at the PC)

Real microphone and speaker, echo gate, wake word to audio, barge-in, Bluetooth headphones delay (WH-CH720N),
perceived quality. Stage markers wake->ASR, tool I/O and TTS-first-audio are not exposed separately by the backend;
only the combined numbers above exist.

## Owner steps at the PC (about 10 minutes)

1. Close JARVES if running, then run `jarvis doctor` (all green) and start with `jarvis`. Do not touch anything until
   the room shows Ready.
2. Note the time you started and when the greeting begins to speak (stopwatch on a phone is enough). Repeat start 3 times
   (stop with `jarvis stop` between). Write the 3 times down.
3. With the greeting finished, ask in Hebrew: "מה השעה?" and then "מה מזג האוויר בתל אביב?". For each answer note,
   with the stopwatch, seconds from the end of your sentence to the first sound. Do this twice each.
4. Interrupt once: ask for a long answer ("ספר לי על ירושלים") and speak over it after 2 seconds. Note if he stops
   within about 1 second and answers the new sentence.
5. Wake word: stay silent 3 minutes (or `jarvis stop` conversation as usual), then say "Hey Jarvis" from normal
   distance 5 times. Note how many woke him, and the seconds until he speaks.
6. Repeat step 3 once with the laptop speaker instead of the Bluetooth headphones, volume normal.
7. Run `jarvis speedtest --class owner` and `jarvis logs` is not needed; reply on #122 with your 10 numbers and
   "worked / failed" for steps 4 and 5. Never paste private text.
