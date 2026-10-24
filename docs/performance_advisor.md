# JARVES Performance Advisor — measured suggestions and one rolling GitHub issue

## Owner intent (24 October 2026)

JARVES should understand its own performance, advise on useful improvements, and maintain one **Performance** issue on the development repository. Do not create one issue per metric, post on every test, increase test frequency, launch a separate polling service, or burn model/API credits on routine diagnostics.

## Existing tools reused

- `jarvis speedtest`, `speedtest.py`: numeric measurements and per-metric budgets, real historical percentiles when sample counts allow
- `selfspeed.History`: at most 60 completed test runs, with a weekly scheduled speedtest already configured (Sunday 13:00); manual on-demand testing remains available
- `perf_gate.py`: CI performance budgets and regression failures, unchanged
- `issue_registry.py`: local content-free, deduplicated findings, with links to GitHub when actually reported
- `#/speed` Dashboard and voice tool `performance_advice`: recommendations visible even when GitHub posting is off

## Reporting policy

1. **Local by default.** Once the authenticated owner enables **GitHub auto reporting** on the speed page, JARVES may create/update its consolidated `[Performance] JARVES measured performance & recommendations` issue without a new approval for every update. The owner can disable it anytime.
2. **Measure normally.** Only a finished and recorded speed test can trigger evaluation; no new jobs, background polls, or new external AI calls. Quiet hours and active voice-session controls of the existing speedtest scheduler are unchanged.
3. **Require recurring evidence.** At least two of the latest three comparable readings must be warning/failure and the latest must be slow. An over-budget failure must repeat; a warning requires three earlier comparable baseline readings and at least a 25% slowdown. Owner and CI machine classes never mix.
4. **Collect all confirmed metrics.** The consolidated issue shows each confirmed metric, severity, measured median, sample count, and a **hypothesis** to investigate; JARVES does not claim a proven root cause.
5. **Write sparingly.** At most one GitHub issue-body write per **24 hours** and only when a confirmed metric appears, disappears, changes severity, or changes more than **20%** from the last reported median. Small variance does not create comments or edits. The `perf_advisor.json` cursor stores the last published sanitized measurement set and issue number — never API tokens.
6. **One issue.** Search existing `from-diagnostics` issues for the durable `jarves-performance-advisor:v1` marker, reuse that issue, then `PATCH` its body in place. Subsequent checks use the known issue number and verify its marker. No issue-per-metric, repetitive comments, or re-creation if the lookup fails. If the owner closes the issue, do not reopen it automatically.
7. **Privacy/failure isolation.** Send only fixed allowlisted metric IDs and numeric summaries plus maintained diagnostic hints. Never send conversations, files, emails, paths, tokens, raw logs or telemetry events. If GitHub credentials, API lookup, or permissions are missing, retain local suggestions and don't pretend a report succeeded.

## Acceptance

- Dashboard: measurements, trend charts, suggestions, reporting toggle and shared issue link
- Voice: owner can ask what is slow and hear the highest-impact measured investigation
- Regression tests: baseline and repeated evidence, machine-class separation, noisy readings, zero-network default, multi-metric single issue, update cooldown, GitHub duplicate lookup and closed issue
- CI: required checks and CodeQL pass; full release/device voice response timing still requires physical testing. Do not mark unavailable microphone or paid live tests as passed.
