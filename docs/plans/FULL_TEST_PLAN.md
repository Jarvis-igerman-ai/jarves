# Full test plan — manual, real-system, plus log review

Goal: find real bugs before more features. Every test = steps, expected result, evidence (log excerpt/screenshot/recording), verdict PASS/FAIL/BLOCKED. Failures go to `docs/reviews/SWV3_FULL_TEST_BUG_TABLE.md` (severity P0–P3), get fixed, and are retested.

## Method
1. **Pass A – install and start**: build/install from the CI installer (clean user) or run from `main`; first-run wizard; autostart; tray; start/stop x5; privacy default.
2. **Pass B – feature-by-feature (agent-driven, real Core)**: for each registered feature (`jarvis features`), exercise ON/OFF/available/unavailable, CLI + dashboard + voice parity, guide page present, error path.
3. **Pass C – voice**: real GPT-Live with virtual mic/speaker (silent_live suites) — greeting, barge-in, interruption, Hebrew names, music control, timers/alarms, lists, memory, web search, camera-off honesty, tool confirmation prompts.
4. **Pass D – dashboard**: every page and plugin page, all locales and RTL, phone width, search, privacy button, logs and conversation filters, context tab, costs, issues, settings editing, files editing (AGENTS/SOUL/MEMORY/USER).
5. **Pass E – owner-assisted** (needs accounts/hardware): Google sign-in, Calendar/Gmail read + confirmed create, morning briefing, Telegram real send, Instagram, Browser on one allow-listed site, real speaker/mic/camera, Pear music.
6. **Pass F – failure injection**: network off, bad API key, budget exhausted, disk full, Pear closed, Core crash/restart, sleep/resume, two instances.
7. **Log review after every pass**: scan `%LOCALAPPDATA%\jarves\logs\jarves.log`, store.db issues, diagnose rules and AI review; classify every WARN/ERROR as expected, bug, or noise (fix noise). Compare to 19 Oct baseline (docs/reviews/ALL_RETAINED_LOGS_19OCT2026.md).

## Exit criteria
No open P0/P1; every feature has a PASS or an explicit BLOCKED reason; log review shows no unexplained ERROR; installer clean-install pass.

## Open questions → see HANDOFF "Questions for the user" (owner answers recorded there).

## Owner answers (20 Oct 2026)
- Target: this PC, from `main` (real Core + virtual mic/speaker); clean-installer test is in the roadmap for later.
- Live GPT-Live budget: up to about $10 total for the campaign.
- Owner can assist with: Google sign-in + Calendar/Gmail, Telegram bot, real speaker/mic/camera listening, Pear Desktop open for music. (Instagram/Browser: not confirmed yet.)
- Data: agents use a copy / separate test profile (`JARVES_HOME` pointed at a temp dir); the real memory is never modified.
