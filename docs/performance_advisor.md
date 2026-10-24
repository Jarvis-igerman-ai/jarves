# JARVES Performance Advisor — one living GitHub performance tracker

## Owner policy (24 October 2026)
JARVES must measure itself and suggest performance improvements automatically, without adding constant work, consuming paid model tokens or creating many GitHub tickets. **One persistent performance issue**, updated in place when evidence materially changes; no arbitrary one-new-issue-per-week limit.

## Reused measurements
- `jarvis speedtest` / `speedtest.py`: startup, dashboard, idle resource use, tool latency and eligible voice-history measurements against owner/CI budgets.
- `selfspeed.History`: up to 60 completed runs, with one existing weekly speed test (Sunday 13:00). The owner may run one manually. No new polling or paid sessions.
- `perf_gate.py`: existing CI performance budgets, independently enforced.
- `issue_registry.py`: local deduplicated, numeric-only issue evidence, separate from public GitHub reporting.
- `#/speed` Dashboard and voice skill `performance_advice`: read-only findings and suggested investigations even when reporting is off.

## What produces a recommendation
1. Three comparable measurements of the same metric in the same `owner` or `ci` class.
2. Two of the last three are warning/failure, including the newest; two hard failures qualify. For warnings without hard failures, at least three earlier comparable measurements must establish a baseline and the slowdown must exceed 25%.
3. Supported budgeted metrics are not silently discarded after the fourth recommendation. A missing metric or a single outlier is not evidence of a regression.
4. Recommendations are bounded diagnostic hypotheses grounded in named metrics — not claims that code causality is proven or that local voice hardware has been tested.

## Autonomous issue sync: off by default
The authenticated owner can enable or disable **GitHub performance auto-reporting** in the Dashboard. With GitHub credentials available locally, JARVES may create **one** issue titled `[Performance] JARVES performance health`, marked with `<!-- jarves-performance-health:v1 -->`. It searches existing `from-diagnostics` issues before creating and updates the same issue body for subsequent material changes. All active confirmed metrics and predefined suggestions appear together.

A material change is a new or removed confirmed metric, a severity change, or a shift in the 25-percentage-point trend bucket. Tiny measurement noise and an unchanged snapshot trigger **no GitHub request**. There is a **minimum 24-hour interval between writes**. Recovery is not inferred from one successful reading; it requires three comparable successful readings. If an issue is closed by its owner, it is not silently reopened. Ambiguous duplicate search, full-page results, missing credentials or API failure fail closed and retain local advice.

Only allowlisted metric IDs, class, aggregated numeric evidence and maintained diagnostic hints are sent. No transcript, prompt, name, address, local file path, key, raw log or full diagnostic dump is transmitted. This opt-in is limited to performance findings; **all other diagnostic issue-report workflows still require the existing per-report owner approval.** No self-modifying code, automatic code changes or automatic merge is enabled.

## Acceptance & known limits
The implementation needs green offline regressions for trend comparison, compact history, privacy, consent, duplicate/closed issue handling, one issue containing all findings, daily cooldown, and recovery. Required CI and CodeQL must pass before merge; post-merge main/package must be verified.

On real Windows hardware, microphone/provider voice stages, end-to-end wake-to-first-audio, ASR/LLM/tool/TTS phase latencies and any claimed product speedup still require owner-assisted measurement. An offline passing test or synthetic data cannot replace real hardware and paid-provider verification. Do not create fake performance incidents in the public issue tracker for smoke testing.
