# Performance baseline: idle CPU, RSS, wake-listener cost, log growth (JAR-65)

Date: 2026-11-09. Base: origin/main (after `de2ff562`'s parent line). Measure-only: no code or behavior change.
Host: owner PC (Ryzen 3 5300U, Windows 11), loaded by other agents, so wall times are noisy; idle CPU/RSS are steady-state.
Follows `2026-11-04-perf-baseline.md`, which listed idle CPU/RSS/disk as not measured.

## Method
- `python core/tools/bench_startup.py --runs 2 --idle-s 60 --scenario core|cloud` (real `jarves run --background` Core,
  throw-away `JARVES_HOME`, own port, no secrets, `wake_enabled=false`, no mic, nothing billable). `cloud` = dummy API key
  so the live-voice prewarm path runs; no request is sent.
- Log growth: separate throw-away Core (voice off), `jarves.log` size at t=40 s and t=220 s (180 s idle window), event counts by code.
- Log bound from code: `logs.setup` uses `RotatingFileHandler` 1,000,000 bytes x 5 backups (`core/src/jarves/logs.py:763`).

## Results (median of 2 runs)
| Metric | core (no audio) | cloud (live voice selected) |
|---|---|---|
| idle CPU | 0.64 % | 0.62 % |
| RSS at CORE_READY | 74.9 MB | 70.1 MB |
| peak RSS during start | 76.0 MB | 114.5 MB |
| private bytes | 64.4 MB | 121.9 MB |
| steady RSS after settle (working set trimmed on Windows, not a leak signal) | 14.4 MB | 32.0 MB |
| threads | 10.5 | 13 |
| idle disk write / read | 8.5 / 55.6 KB/min | 5.1 / 39.5 KB/min |
| files rewritten while idle | health.json, runtime.json, memory.db-wal | runtime.json, memory.db-wal |
| spawn to ready | 10.2 s | 22.1 s |
| startup CPU | 6.2 s | 16.3 s |

Log growth, idle Core: 25,427 bytes at t=40 s and 25,427 bytes at t=220 s = 0 bytes/hour while idle. Startup writes ~25 KB
(56 `SKILL_REGISTERED`, 5 `JOB_DRAFT`, 5 `JOB_ACTIVE`, a few `PREFLIGHT_ITEM`). Worst case on disk is bounded at 5 MB (1 MB x 5).

## Read
- Idle cost is low: under 1 % CPU, under 10 KB/min disk writes (the heartbeat files only), no child processes.
- The big cost is start-up (cloud prewarm: +40 MB peak, +10 s CPU), not idle. This was already known; no change proposed here.
- Wall times (spawn/stop) varied 2x between runs under load; do not read them as a regression.

## Wins considered
None applied. No idle hot spot was found that is safe, test-covered and measurable above noise, so this PR changes no code.

## UNVERIFIED / not measured
- Wake-listener idle cost: needs the wake model and a microphone; both were absent (preflight `wake_model` red, no audio).
  All numbers above are with the wake word off. Measure with `wake_enabled=true` on a PC with a mic.
- Pi 5 (target runtime) numbers: not measured at all.
- Idle log growth was observed for 180 s only; per-hour rate is extrapolated (0). A long soak with real activity
  (conversations, alerts, connected Godot face) was not run.
- Installed beta product not measured (source tree only).

## Proposed next task (not started)
Wake-on run of `bench_startup.py` on a mic-equipped PC and on the Pi 5; a 1 h soak logging jarves.log growth.
