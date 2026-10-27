# Greeting retry (#179), 25 Oct 2026 - code done, real re-measurement pending

Branch `fix/greeting-retry-179` (pushed, NO PR until the owner says "queue clear"). No paid call was made; all tests use a stubbed live session.

## What changed
- `voice/live.py` `LiveVoice.wait_ready`: wait 6 s, nudge (clock note, as before), wait 4 s, ONE retry (the greeting request again as a
  `session.commentary` note, event_id `jarves_greeting_retry`), wait the rest, then raise as before (app.py logs `LIVE_GREETING_MISSED`
  and continues without a greeting, #138).
- Budget unchanged: measured from the code, the wait was 15 s (`timeout=15`: nudge at 6 s, 9 s more). Now `GREETING_BUDGET_S = 15`:
  nudge at 6 s, retry at 10 s, give up at 15 s. Startup is not extended; worst case is the same 15 s. A smaller `timeout` skips the retry.
- Outcome: `voice.greeting_outcome` = `ok` | `retry_ok` | `missed`; log codes `LIVE_GREETING_OK`, `LIVE_GREETING_RETRY` (warn),
  `LIVE_GREETING_RETRY_OK`, `LIVE_GREETING_RETRY_FAILED`, `LIVE_GREETING_MISSED`. "ok" includes a start recovered by the first nudge (`nudged=True` in the log).
- Counters: `jarvis speedtest` (history group) now reports `greeting_ok`, `greeting_retry_ok`, `greeting_missed` (14-day counts from the
  log, no trend verdict) and `greeting_missed_pct` (needs 5 starts; budget owner_max 20, ci_max 40 in `core/perf_budgets.toml`);
  the performance advisor has a suggestion for `greeting_missed_pct`. No user-facing text changed, so no guide entry.
- Tests: `core/tests/test_greeting_retry.py`.

## Open risk (honest)
The retry note type (`commentary.append` carrying the greeting text) is NOT verified against real GPT-Live: a stubbed session cannot show
whether the provider speaks it. The real run decides. If the retry never recovers a stall, the next option is a short local/cloud TTS greeting.

## Questions for the owner
1. OK to spend about 2.00 USD (cap 10 USD) for 10 cold starts to measure the retry? Default if silent: yes, 10 starts, `--turns 1`.

## Real re-measurement (another run; about 0.06 USD per start, 10 starts about 0.6-1.0 USD)
From `C:\work\wt\greeting-179\core` with the installed beta's Python or the venv, one cold start per run, a throw-away data folder each time
(do not touch the owner's data), real GPT-Live, never played aloud:

```powershell
$env:PYTHONPATH = "C:\work\wt\greeting-179\core\src;C:\work\wt\greeting-179\core"
1..10 | ForEach-Object {
  $h = "$env:TEMP\greet179_$_"; Remove-Item $h -Recurse -Force -ErrorAction SilentlyContinue
  C:\work\jarvis\core\.venv\Scripts\python.exe core\tools\silent_live.py --turns 1 --max-minutes 1 --home $h --json "$h.json"
}
```

Then count outcomes per home (expected: mostly `LIVE_GREETING_OK`; any `LIVE_GREETING_RETRY_OK` shows the retry works):

```powershell
Select-String -Path "$env:TEMP\greet179_*\logs\jarves.log" -Pattern "LIVE_GREETING_(OK|NUDGE|RETRY_OK|RETRY|MISSED)" | Group-Object { ($_.Line | ConvertFrom-Json).code } | Select Name,Count
```

Or in one data folder: `jarvis speedtest --json` shows `greeting_ok`, `greeting_retry_ok`, `greeting_missed`, `greeting_missed_pct`.
Record in `docs/reviews/V45_VOICE_LATENCY_*.md`: counts, spend, `Core ready -> first greeting audio` per run. Stop when the 10 USD cap is near.
Pass criterion: 0 of 10 missed, or `greeting_missed_pct` clearly below the earlier 20 %. Then close #179 with the numbers.
