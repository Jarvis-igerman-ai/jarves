<!-- manual-test-sheet v1 -->
# Manual test sheet for JARVES 4.0.1

Generated 2026-10-24 from the changes since v3.0.0. Install the LATEST beta first (`jarvis update --channel beta`), test that exact build, then sign off.

Version: 4.0.1

Results: `pass`, `fail`, `blocked`, `accepted-blocked` (needs a note), `pending`. Fill them with `jarvis release signoff` or the dashboard page Release sign-off.

| ID | Area | What to do | Expected | Result | Note |
|---|---|---|---|---|---|
| C1 | Changed | Try it in the beta: Changed: Setup and tour (#/setup) shows one card at a time with three short lines and three big answers (Works / Did not work / Later; | Works as described; nothing next to it broke | pending |  |
| C2 | Changed | Try it in the beta: Changed: developer workflow, nothing changes for users. | Works as described; nothing next to it broke | pending |  |
| C3 | Changed | Try it in the beta: Changed: developer workflow, nothing changes for users. | Works as described; nothing next to it broke | pending |  |
| C4 | Changed | Try it in the beta: Fixed: no more silent limits beyond the daily ones. | Works as described; nothing next to it broke | pending |  |
| C5 | Changed | Try it in the beta: Added: software project manager (`jarvis project ...`, Projects dashboard page, voice tools, feature `project_manager`, off by default). | Works as described; nothing next to it broke | pending |  |
| C6 | Changed | Try it in the beta: Added: software factory flows for the project manager (jarvis project flow/bug/debate/answer/decisions/github). | Works as described; nothing next to it broke | pending |  |
| C7 | Changed | Try it in the beta: Changed: the project manager is a generic orchestrator without a fixed flow: already-started projects (`open --base/--dirty`, `add --done`), split... | Works as described; nothing next to it broke | pending |  |
| C8 | Changed | Try it in the beta: Added: release notes. Dashboard page Release notes (pick any version, English and Hebrew), `jarvis release-notes [--version V] [--list] [--open] [-... | Works as described; nothing next to it broke | pending |  |
| C9 | Changed | Try it in the beta: Added: Setup and tour, one wizard for every topic (dashboard page `#/setup`, `jarvis tour`, voice "show me around"): set up (or skip) -> safe test... | Works as described; nothing next to it broke | pending |  |
| C10 | Changed | Try it in the beta: Added: update channels. `jarvis update --channel beta` (dashboard Updates page, English and Hebrew) follows every green `main` build as a signed be... | Works as described; nothing next to it broke | pending |  |
| C11 | Changed | Try it in the beta: Added: stable releases are tag-driven and gated by a manual test sign-off (`jarvis release signoff`, dashboard page Release sign-off), see docs/REL... | Works as described; nothing next to it broke | pending |  |
| C12 | Changed | Try it in the beta: Added: Issues registry. JARVES lists the problems he finds in himself (self-test, speed test, diagnostics and crash findings, tools that keep faili... | Works as described; nothing next to it broke | pending |  |
| C13 | Changed | Try it in the beta: Changed: issues registry and `jarvis diagnose` are readable and honest (#17). | Works as described; nothing next to it broke | pending |  |
| C14 | Changed | Try it in the beta: Added: self-management (`jarvis manage ...`, `jarvis undo`, the Self-management dashboard page and the `self_manage` skill): JARVES shows what is c... | Works as described; nothing next to it broke | pending |  |
| C15 | Changed | Try it in the beta: Added (V5, software only, owner-approved): Goals and Adaptive Memory (confirmed preferences in the model instructions, goal-done line), goal execut... | Works as described; nothing next to it broke | pending |  |
| C16 | Changed | Try it in the beta: Added: wake train production loop and restricted-token isolation (`winsandbox`), phone end-to-end tests, setup Hebrew console fix that keeps existi... | Works as described; nothing next to it broke | pending |  |
| C17 | Changed | Try it in the beta: Changed: privacy and delegation defaults for NEW installs (OD-12, OD-15); model-facing tool descriptions trimmed by at least 30% (prompt budget unc... | Works as described; nothing next to it broke | pending |  |
| C18 | Changed | Try it in the beta: Fixed: bug sweep 4.1 part 1 (leaked memory.db handles, quiet-wait and warm-up in tests, suite V04/V05/S12 triage). | Works as described; nothing next to it broke | pending |  |
| G1 | Guide | Open the 61 changed guide(s) on the Guides page, in English and in Hebrew, and skim each: adaptive_memory, agent_orchestrator, alerts, api_bill, api_budget, appearance, ask_full, assisted_dialing, auto_update, autonomy, backup, beta_channel, browser, camera, camera_when_away, chimes, client_delegation, context, daily_limits, dashboard, dev_manager, dev_status, diagnosis, gmail, goal_plans, goals, google_calendar, google_contacts, guided_setup, health, help, issue_reports, lan_dashboard, memory, mobile_access, morning_briefing, music, notes_files, persona, privacy, proactive, proactive_signals, profiles, project_manager, recall, release_hosting, release_signoff, runtime, scheduler, self_check, self_manage, self_tests, setup_tour, skill_creation, text_chat, updates, usage, wake_word, web_chat, whatsapp_archive, whatsapp_bridge | Each reads correctly and its first steps work as written | pending |  |
| P1 | Dashboard | Open the dashboard pages changed in this release, in English, then Hebrew: agents, approvals, browser, bugs, bugs_guard, chat, chatview, context, delegation, dialing, guides, health, mobile_admin, pillars, profiles, projects, recall, releasenotes, selfcheck, selfmanage, selfspeed, setup, updates, usage, whatsapp, wizard | Each renders, no cut-off text, keyboard works | pending |  |
| OI-02 | Standing | finish the first-run wizard | wizard completes; Home shows "running" | pending |  |
| OV-01 | Standing | say "היי ג'רוויס, מה השעה?" | wakes, answers in Hebrew, correct time | pending |  |
| OV-02 | Standing | ask an English question | answers in English, next Hebrew turn back to Hebrew | pending |  |
| OV-03 | Standing | interrupt mid-answer | stops within 0.8 s, listens | pending |  |
| OV-07 | Standing | "תצא לגמרי" (full exit) | JARVES exits fully, no dashboard opened | pending |  |
| OP-01 | Standing | trigger a medium-risk action by voice | popup on top, Hebrew per setting, inside the work area | pending |  |
| OP-02 | Standing | say "כן" | approved once, no echo self-approval | pending |  |
| OP-03 | Standing | trigger, press Esc | denied, nothing done | pending |  |
| OL-I1 | Standing | accept the in-app update to 4.0.0 | silent upgrade, data kept | pending |  |
| OL-I2 | Standing | `jarvis doctor` | green | pending |  |
| OL-I3 | Standing | `jarvis update --rollback` | back to 3.0.0.x, data as before | pending |  |
| OL-I4 | Standing | update again | works | pending |  |
| OO-02 | Standing | open Health | score, trend, tiles, findings | pending |  |
| OR-01 | Standing | `jarvis diag`, open the ZIP | no conversation text, names, numbers, keys | pending |  |
| OR-02 | Standing | have a short talk, open the log view | content masked | pending |  |

## Sign-off

- Tested beta: 
- Commit: 
- Date: 
- Signed: no
