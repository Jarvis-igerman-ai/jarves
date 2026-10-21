# SW-V4 performance budgets

Machine-readable source: `core/perf_budgets.toml`. Gate: `core/tools/perf_gate.py` (CI job `perf`, nightly `perf`).
Baselines are the SW-V3 P0.3 numbers (`docs/reviews/SWV3_P03_REPORT.md`, combined scenario, owner PC: Ryzen 3 5300U,
7.5 GB, Windows 11, Python 3.12.12). `owner_max` is strict, `ci_max` is loose. The gate compares the median of the runs
with `<=`. The gate has two classes: `owner` (default on a PC) and `ci` (default when `CI` is set).

## Budgets

| metric | baseline | owner max | CI max | source |
|---|---|---|---|---|
| private commit (MB) | 92.7 | 150 | 260 | bench |
| working set (MB) | 117.0 | 175 | 300 | bench |
| threads | 11 | 14 | 22 | bench |
| child processes | 0 | 0 | 0 | bench |
| idle CPU (%) | 0.28 | 0.5 | 2.0 | bench |
| idle disk writes (KB/min) | 3.5 | 10 | 60 | bench |
| idle disk reads (KB/min) | 320 (unexplained) | 600 | 2500 | bench |
| RSS growth over the idle window (MB) | 0.8 | 2 | 6 | bench |
| Core ready (`CORE_READY` ms) | 1687 | 3000 | 8000 | bench |
| spawn to ready (s) | 2.6 | 5 | 15 | bench |
| `jarves stop` (s) | 1.65 | 3 | 8 | bench |
| `features_list` with expired slow probe (ms) | 10.5 | 50 | 150 | in-process |
| session-start probe path / `live_tools` (ms) | 18 | 50 | 150 | in-process |
| dashboard first load `GET /` (ms) | 78 | 500 | 1500 | in-process |
| dashboard slowest page API, first request (ms) | 74 | 500 | 1500 | in-process |
| LIVE_STARTED after CORE_STARTING (s) | 8.6 | 20 | 40 | real log (`--log`) |
| LIVE_FIRST_AUDIO after CORE_STARTING (s) | 10.3 | 24 | 45 | real log (`--log`) |
| tool call p50 / p95 (ms, last 14 days of TOOL_RESULT) | 120 / 2500 | 1000 / 8000 | 3000 / 20000 | `jarvis speedtest` (history; placeholder baselines) |
| wake-to-first-audio median (s) | 1.8 | 3 | 6 | `jarvis speedtest` (monitor events) |
| dead air p95 (s) | 1.44 | 1.5 | 3 | `jarvis speedtest` (monitor events) |
| OpenAI API round trip, one HEAD without key (ms) | 400 | 1500 | 4000 | `jarvis speedtest` (net) |

## Pending (need a real microphone, Godot window or billed session; owner-assisted in A / AM-P1..P3)

wake-to-ready, wake-to-first-audio (5 runs), face show/hide, sleep/resume recovery, and the budgets of packages E, F, G, O
(bridges, index search, monitor at most 0.1% CPU). They are added to `perf_budgets.toml` by the package that ships the
feature, with `source = "log"` or `"inproc"`. Idle disk reads (250-390 KB/min) are still unexplained; the ceiling only
stops growth until AM-P2 explains it.

## Updating budgets (deliberate, reviewed)

1. Reproduce: `python tools/perf_gate.py --class owner --runs 3` on the owner PC (quiet machine).
2. If the regression is real and wanted, raise the ceiling in `core/perf_budgets.toml` and add a `[[history]]` entry with
   the date, the old and new value and the reason (commit or issue). `python tools/perf_gate.py --propose` prints the
   mechanical suggestion (baseline x1.5 owner, x3 CI).
3. `tests/test_perf_gate.py` checks that the file stays well formed (baseline <= owner max <= CI max, history present).
4. Never raise a ceiling to make a noisy CI run pass: loosen only the `ci_max`, with the runner evidence in the history
   entry.

A measurement run on a busy PC (5 agents running) exceeded the strict owner ceilings for idle CPU (0.51), idle writes
(27 KB/min over a 30 s window, which over-weights the 60 s heartbeat write), Core ready (3.1 s) and spawn to ready (8.2 s);
that is why the CI job uses a 60 s idle window and loose ceilings, and why the owner class is for quiet machines.

`jarvis speedtest` (core/src/jarves/speedtest.py) judges every metric against this file (pass / warn within 15 % of the ceiling / fail) and warns when a value is more than 20 % worse than the median of the last 5 runs; CI runs it as `--class ci --gate`.
