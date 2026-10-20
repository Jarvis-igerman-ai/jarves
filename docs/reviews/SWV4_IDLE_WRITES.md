# SW-V4 idle disk writes (CI gate failure: 164.75 KB/min vs ci_max 60)

Issue #17. Tools: `core/tools/idle_writes.py` (per-file tracer: size+mtime poll every 0.5 s and the process I/O counter,
60 s window after CORE_READY + 20 s warm-up, scenario `combined`, tmp home) and a `sqlite3` trace (every write statement
with its call stack) on this PC.

## Per-file table (60 s idle window, this PC)

| file | before: changes / bytes added | after |
|---|---|---|
| `health.json` (3.3 KB, rewritten whole) | 4 / 317 (heartbeat every 20 s + a change) | 1 (heartbeat only when older than 50 s) |
| `memory.db-wal` (alerts table) | 2 / 8240 (WAL pages, one 4 KB page per write) | 0 |
| `runtime.json` (1 KB) | 1 / 0 (60 s heartbeat, V3 P0.3) | 1 (unchanged, intended) |
| `store.db`, `store.db-wal`, `logs/jarves.log`, `privacy.state`, scheduler, wake, camera, appearance | 0 | 0 |

Process write rate: **25.4 KB/min before, 4.3 KB/min after** (this PC; bench_startup single run after: 7.5 KB/min,
the V3 baseline was 3.5). Store.db is NOT written while idle: the recall index, privacy field-logging and selfcheck add
nothing in steady state (their `recall_meta` writes only appear at shutdown).

## Churn found and fixed at the source

1. **Stuck alert retry counter** (`alerts.py` `flush`): a pending alert that no output can deliver (fresh home: no
   speech/popup output yet) ran `UPDATE alerts SET attempts=attempts+1` on every scheduler tick (every 30 s) = a durable
   WAL write per tick, forever. Now only the first failed try is recorded; delivery and expiry still write.
   Durability unchanged: alerts, status changes and deliveries are committed as before.
2. **health.json heartbeat** (`health.py` `heartbeat`, called by `app._health_loop` every 20 s): rewrote the file each
   time although only `ts`/`uptime_s` changed. Now re-saved only when older than 50 s; every real change still saves at
   once. Readers call a snapshot older than 120 s "off", so a 50 s refresh keeps a 2x margin.

## Measurement window

`bench_startup.WARMUP_S` = 20 s (was 15 s) before the idle window opens, so start-up work (prewarm, first scheduler
pass, first heartbeat) is excluded. Limits unchanged (owner 10, ci 60): the cause is not proven to be runner noise.

## What was NOT reproduced (open)

The 164.75 KB/min on the GitHub runner could not be reproduced here: this PC showed 25 KB/min before the fix, and the two
sources above explain that. On the runner the same write count costs more (slower disk, 4 KB WAL pages + NTFS metadata
per `WriteFile`, Defender), and the runner's 15 s warm-up may have been too short for start-up work. To settle it,
`perf_gate.py` now prints, per run, `idle write ... KB/min; files changed: <file xN>` (from `bench_startup.idle_files`)
and the CI log shows the culprit if the gate still fails. If the remaining files are only `health.json`/`runtime.json`
heartbeats the runner number is noise and the ci_max may be raised then, with this table as the proof.

## Test

`core/tests/test_swv4_idle_writes.py`: heartbeat window, stuck-alert single write, and a real idle Core (same
`bench_startup.one_run` code as the gate) asserting only `health.json`/`runtime.json` change and < 30 KB/min.
