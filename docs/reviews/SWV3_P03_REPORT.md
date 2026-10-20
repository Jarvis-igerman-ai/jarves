# SW-V3 P0.3: startup, always-on and background performance

Branch `v3-p03-perf`, based on `swv2-integrate` (e95eb6f). Issue #17. Model: Claude Opus 5.5. Date: 20 Oct 2026.

**Machine.** The owner's PC: AMD Ryzen 3 5300U (4 cores, 8 threads), 7.5 GB RAM with about 1.3 GB free during the
runs, Windows 11, Python 3.12.12.

**Scope.** Profile first, then change only what the measurements prove. Behaviour stays the same: same features, same
answers, same safety gates. Raspberry Pi / Linux is not tested here (see "Not measured").

## How it was measured (reproducible, silent)

- `core/.venv/Scripts/python.exe core/tools/bench_startup.py --runs 3 --idle-s 60 --scenario core|cloud|combined`
  - Starts the real `jarves run --background` in a throw-away `JARVES_HOME`, on its own port, with no secrets.
  - The wake word is off, so no microphone opens. No conversation starts and nothing is billed.
  - It waits for `CORE_READY`, lets the Core settle for 15 s, then samples it while idle for 60 s.
  - Measures: working set, private commit, CPU, disk I/O, threads and child processes. Then it runs `jarves stop`.
  - Uses ctypes on Windows and /proc on Linux. No extra packages.
- The three scenarios:
  - `core`: voice off.
  - `cloud`: API-key connection with GPT-Live, using a dummy key that is never sent.
  - `combined`: the owner's choice, ChatGPT plan for typed answers plus GPT-Live.
- `bench_startup.py --log %LOCALAPPDATA%\jarves\logs\jarves.log` prints per-start stage timings from a real log. It
  reads event codes only, never transcript text. Use it to verify the next real desktop starts.
- In-process profiling used cProfile around `Core.start()` and a 60 s idle window. `-X importtime` and per-module
  RSS/commit deltas were used for imports. Before/after component timings were run against a `git archive` of
  `swv2-integrate`, on the same machine, in the same session.

## Evidence from the owner's real starts (19 Oct log, before this work)

Desktop mode, live voice with `activation = conversation`, camera on. Times are seconds after `CORE_STARTING`.

| start (UTC) | Core ready | live started | first audio | face connected | prewarm took |
|---|---|---|---|---|---|
| 14:13 | 1.8 | 8.6 | 10.3 | 13.2 | 5.9 s |
| 15:38 | 1.6 | 10.1 | 12.2 | 23.0 | 6.3 s |
| 18:16 | 4.7 | 15.1 | 17.2 | 23.6 | 12.2 s |
| 19:32 | 3.9 | 14.0 | 15.9 | 20.5 | 19.7 s |
| 20:19 | 2.8 | 11.1 | 12.5 | 16.1 | 7.6 s |

- **The prewarm competed with voice start-up.** The background prewarm imported the Agents SDK, `agents.realtime` and
  openai. It usually finished *after* the live session had started, so it was competing with the critical path for the
  CPU and the GIL the whole time. GPT-Live and the ChatGPT-plan brain never use the Agents SDK.
- **Some local tools were slow for no reason.** From the same log: `features_list` took 320 / 324 ms,
  `list_skills` 325 ms and `music_pause` 340 ms, while comparable local tools took about 10 ms. The cause was the Pear
  "is it running" probe, a localhost connect with a 0.3 s timeout. It ran on the event loop whenever its 15 s cache
  had expired.
- **Turn latency is network and model bound.**
  - From the end of the user's speech to JARVES's audio: median 1.78 s, p90 2.52 s (n = 37).
  - Backend time: median 0.95 s.
  - Local hand-off from delegation to backend start: median 7 ms, p90 26 ms.

## Findings and fixes (each one measured)

| # | Finding (evidence) | Fix | Files |
|---|---|---|---|
| F1 | The prewarm always imported the Agents SDK and `agents.realtime`: 2.2–2.8 s CPU and +41 MB RSS. GPT-Live and ChatGPT-plan routes never use it. The first live session still paid 0.39–0.55 s *on the event loop* for openai's lazy imports (`openai.types.live`, `httpcore2`). | The prewarm imports only what the selected route uses, most urgent first: openai, then the live-client modules. It adds `agents.realtime` only for `voice.mode = realtime` and `agents` only for the API-key text brain. `PREWARM_DONE` now logs the module list. | `app.py` |
| F2 | numpy's OpenBLAS creates one worker per CPU when imported and commits about 32 MB for each: +231 MB private commit and 7 idle threads. The Core only does tiny vector maths. STT, wake word and camera use CTranslate2, ONNX Runtime and OpenCV, which have their own thread pools. | `jarves run` sets `OPENBLAS_NUM_THREADS=1` before numpy loads. A value the user already set wins. | `cli.py` |
| F3 | `runtime.json` was rewritten every second while idle (58 KB/min), plus a hop to a tray thread each time. The live view contains `ts`, so it always looked changed. | Compare the view without `ts`. Save on a real change, or at least every 60 s so `ts` stays recent. Refresh the tray only on a change. | `runtime/service.py` |
| F4 | With Pear closed, the music probe blocked the event loop for its 0.3 s connect timeout. This happened at start-up (TOOLS.md), at every live-session start (`live_tools`) and in `features_list` / `list_skills`. | Start-up and live-session start warm the probe in a worker thread. An *expired* result for a slow probe (only `music`) is returned at once and re-checked in a daemon thread (stale-while-revalidate). A first-ever check and `invalidate()` still probe synchronously, as before. | `features.py`, `app.py` |

Rejected or kept, with reasons:
- **Lazy import of faster-whisper (-0.26 s, about -20 MB): rejected.** The eager import is a documented crash
  workaround. Loading CTranslate2 after WinRT speech has been used crashes the process (QA 18 Oct, reproduced 2/2).
- **Skipping the PortAudio initialisation (0.56–0.67 s): kept.** Voice modes need it. Only `voice.mode = off`
  could skip it, and that mode is not used in practice.
- **Camera model loading during the owner's start-up (+4.7 s after ready): left unchanged.** It already runs in
  worker threads. Deferring it would change when presence detection starts. Measuring it needs the webcam, which
  stays off without approval.

## Before / after on the same machine (median of 3 runs, background Core, 60 s idle)

| metric | core: before | core: after | cloud: before | cloud: after | combined: before | combined: after |
|---|---|---|---|---|---|---|
| Core start (`CORE_READY` ms) | 1969 | 1640 | 1985 | 1687 | 1609 | 1687 |
| prewarm (ms, background thread) | – | – | 4437 | 4406 | 4469 | **2687** |
| steady working set (MB) | 100.6 | 99.8 | 158.8 | 156.9 | 158.7 | **117.0** |
| private commit (MB) | 300.5 | **75.3** | 357.8 | **131.4** | 357.8 | **92.7** |
| idle CPU (%) | 0.36 | 0.31 | 0.60 | **0.28** | 0.57 | **0.28** |
| idle disk writes (KB/min) | 58.2 | **3.3** | 58.1 | **3.5** | 58.6 | **3.5** |
| threads | 17 | **10** | 18 | **11** | 18 | **11** |
| child processes | 0 | 0 | 0 | 0 | 0 | 0 |
| RSS growth over 60 s (MB) | 1.2 | 1.0 | 0.7 | 0.5 | 0.7 | 0.8 |
| `jarves stop` (s) | 1.44 | 1.49 | 1.60 | 1.44 | 1.54 | 1.65 |

- Spawn-to-ready wall time ranged from 2.8 to 6.9 s across runs because the machine was busy and short of RAM. The
  `CORE_READY` time shows no regression.
- **`cloud`:** the prewarm takes the same time as before because the API-key brain still uses the Agents SDK. It now
  also loads the live-client modules.

Component timings (same session, base = `swv2-integrate`):

| path (Pear closed where relevant) | before | after |
|---|---|---|
| First live session, client + `live.connect()` on the event loop, after the prewarm | 387 / 553 ms | **9 / 9 ms** |
| Prewarm finished after `CORE_READY` (combined) | 4.3 / 5.5 s, Agents SDK loaded | **1.7 / 1.8 s**, Agents SDK not loaded |
| `live_tools()` at session start, probe cache expired | 323–349 ms | **14–22 ms** |
| `features.list()` (`features_list` tool) | 323–326 ms | **10–11 ms** |
| `registry.tools()` (`list_skills`) | 314–317 ms | **2–3 ms** |

Expected effect on wake-to-first-audio and desktop start-up:
- **Every live session start:** about 0.3 s less blocking work on the event loop (Pear closed). The first session saves
  about 0.4–0.55 s more.
- **Combined installs:** 2–3 s of background import CPU no longer competes with the first live session.
- **Not yet confirmed end to end.** Confirming this needs a real desktop start, which opens Godot and a billed live
  session. Check it with `bench_startup.py --log` after the owner's next starts.

## Acceptance budgets (from the measured baseline; regressions should fail review)

Background Core, no voice session, this PC:
- Idle CPU at most 0.5 %.
- Idle disk writes at most 10 KB/min.
- At most 14 threads.
- No child processes.
- Private commit at most 150 MB (`combined`) or 175 MB (`cloud`).
- RSS growth at most 2 MB per 60 s idle.

Event-loop and prewarm budgets:
- The first live session spends at most 50 ms on the event loop before the network.
- `live_tools` / `features_list` take at most 50 ms with Pear closed.
- The prewarm for combined or ChatGPT routes must not import `agents`.

## Not measured / UNVERIFIED (next steps)

- End-to-end wake-to-ready and wake-to-first-audio, and full desktop cold start with Godot. These need the real
  microphone or a billed live session; the expected gain above is component-measured only.
  - Next step: compare `bench_startup.py --log` on the owner's next starts against the table above.
- Dashboard load time, and show/hide of the face (Godot render/init).
  - Not run: another agent is redesigning the Dashboard UI, and the Godot viewer needs the desktop session.
- Recovery after sleep/resume, device changes and network failures. These are existing tested paths (`test_runtime`);
  their timings were not measured.
- Raspberry Pi 5 / Linux ARM64: `bench_startup.py` supports /proc, but the Pi is not available. The OpenBLAS default
  and the probe fix are platform-neutral.
- Idle disk *reads* stay at about 250–390 KB/min, unchanged. Windows I/O counters also include non-file I/O; the source
  is not identified yet.

Tests: `core/tests/test_swv3_p03_perf.py` covers:
- route-aware prewarm;
- no idle `runtime.json` churn, with the heartbeat;
- the probe warm-up and stale-while-revalidate;
- the OpenBLAS default.

The full offline suite passed on this branch: `pytest core/tests`, 1141 passed and 3 skipped.
