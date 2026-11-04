# Performance overhaul 4.5 (ISSUE-159 / #159): baseline, plan status, re-measure

Date: 2026-11-04. Base: jarves/main `b32112e2`. Measured headless on the owner PC (Ryzen 3 5300U, Windows 11)
with a throw-away data folder, no microphone, no voice, no sound, no popups. Load was NOT controlled
(other agents running), so wall times are noisy; module counts are the stable numbers.

## Before (24 Oct 2026, main `ecc01e4`, from the issue)
`jarvis --version` 4.06 s (95 jarves modules); `status` 4.97 s (105); `features list` 9.9 s; Core spawn to
CORE_READY 27.7 s under load; `jarvis stop` after start 29 s; `import openai` 8-12 s; `session_config()` 0.3-0.5 s
per live session; `Dashboard()` init 4.1 s; `/api/agents/doctor` and `/api/dev/status` over 15 s.

## Plan status (code already on main, so this task adds no code)
| Plan item | Landed | Evidence |
|---|---|---|
| 1 CLI lazy command modules | PR #235 | test_cli_lazy.py, help output identical for all 72 commands |
| 2 Tool schema cache, guide re-stat throttle | PR #197 | test_perf_overhaul_schemas.py |
| 3 Voice import at Core start, stop does not wait | PR #244 | see issue comment (round 2) |
| 4 Dashboard listens first, hidden-tab polls paused, slow endpoints cached | PR #226, #249 | test_perf_overhaul_dashboard.py |
| (extra) compact health.json | PR #241 | issue comment |
| 5 Re-measure | this report (partial) | below |

## After (2026-11-04, main `b32112e2`, best of 3, noisy PC)
| Metric | Before | After |
|---|---|---|
| `jarvis --version` wall | 4.06 s | 2.93 s (runs 6.44 / 2.93 / 2.94) |
| `jarvis status` wall | 4.97 s | 3.44 s (3.55 / 3.44 / 3.60) |
| `jarvis features` wall | 9.9 s | 5.17 s (12.0 / 5.2 / 6.1) |
| `status` jarves modules / total | 105 / 581 | 26 / 342 |
| speedtest features_list_ms | n/a | 27.3 ms (budget 50) |
| speedtest dashboard_first_load_ms | n/a | 284.4 ms (budget 500) |
| speedtest dashboard_api_max_ms | n/a | 41.5 ms (budget 500) |
| speedtest openai_roundtrip_ms | n/a | 1672.8 ms, OVER budget 1500 (network, single sample; was 505.9 ms on 31 Oct) |

Read: the module-count drop (105 to 26) is the reliable CLI result. Wall times here are 2-3x the quiet-PC numbers
posted on 31 Oct (1.05 / 1.19 / 2.22 s) because the PC was loaded now; do not read it as a regression.
The openai round trip is network-dependent; re-run before treating it as a defect.

## Not measured / UNVERIFIED
- Core spawn to CORE_READY, `jarvis stop` latency, voice load+connect, greeting latency, idle CPU/RSS/disk:
  need a running Core, live key and microphone (speedtest rows are n/a). UNVERIFIED on the installed product;
  related #122 (real-device voice latency).
- Installed-product acceptance of the perf changes: not done here (source tree run, not the installed beta).
- Hidden-tab polling behaviour is covered by tests, not observed in a real browser tab by this task.
- Known trade-off: cached endpoints can be stale up to their TTL (3-30 s).

## Remaining proposals (out of this task's scope, not started)
Re-measure on a quiet PC against the installed beta; Core start / stop timings with a headless Core; confirm the
openai_roundtrip over-budget reading with 5 samples.
