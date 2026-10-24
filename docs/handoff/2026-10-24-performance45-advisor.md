# JARVES Performance 4.5 — measured improvement advisor, autonomous GitHub issues

**Owner authorization:** 24 October 2026. No 4.5 release or live-device speedup is claimed.

## Existing measurements reused
Existing speedtest.py, selfspeed.py, perf_gate.py, perf_budgets.toml, issue_registry.py and the Health & speed Dashboard are reused, not duplicated. They cover startup, resource usage, dashboard, tools and voice history, per-metric budgets, owner/CI classes, scheduled local tests, trend charts and a content-free local issue registry.

## New functionality in this batch
- perf_advisor.py: deterministic offline measured advice. Requires three comparable samples in the same budget class, two bad readings in the last three including the latest; either two hard failures or a 25%+ degradation against at least three older readings. No recommendations based solely on one outlier, malformed values, or unsupported metrics.
- On a recorded speed test (manual or weekly schedule), confirmed advice becomes a stable local issue. The existing registry preserves IDs, numeric-only evidence, severity and status.
- Voice tool performance_advice, CLI jarvis performance and Health & speed Dashboard display actionable recommendations with metric-specific investigation hypotheses.
- Optional autonomous GitHub Issues: ONE-TIME OWNER OPT-IN by authenticated Dashboard or CLI --auto-github on/off, off by default. Needs GitHub authentication on the owner's device. A maximum of ONE new issue per seven days, reuse issue with same marker, abort on incomplete duplicate search, errors or missing token. Upload only whitelisted metric identifier, class, numbers and predefined diagnostic recommendation. Never prompts, voice, local paths, logs, addresses, secrets or whole bundles. Other issue categories retain their per-report approval requirement.
- No self-modification, no autonomous fixes/merges, no scheduled extra heavy performance tests.

## Tests and operational acceptance
- Unit tests: sustained vs transient regressions, owner-vs-CI class separation, missing/invalid data, local issue dedup, explicit consent, toggle-off, no token, HTTP fail-closed, GitHub deduplication, 7-day cap.
- CI result and Auto-label policy both required; CodeQL green; after merge confirm main CI/package and performance budgets.
- On the actual Windows system run 3+ comparable baselines and 5+ wake-to-audio sessions. Do not claim end-to-end ASR/LLM/tool/TTS stage breakdown until instrumented on the real provider. Any paid live validation needs owner approval.
- Opt in then test with synthetic values and controlled repo issue; verify only one real issue, no sensitive payload and no duplicates. Do not create bogus performance incidents in the production repository.
- Keep metric-specific before/after data and recommend only changes grounded by reproducible improvement.

## Deferred measured instrumentation, tracked separately
When actual device traces are available, add ASR/LLM/tool/TTS phase breakdown, concurrent-load responsiveness, controlled A/B device trials, and signed release performance trend evidence. This PR implements the measurement/advice/issue flow but does NOT claim those device-only validations or full V4.5 release acceptance.
