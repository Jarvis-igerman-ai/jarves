# V5 pillars 2 and 3, software slice: Goals and Adaptive Memory

Branch `v5/pillars-goals-memory` (draft PR into `v5/mobile-access`). Rules from `docs/plans/PRIORITIES.md`: nothing leaves the PC unless needed, conservative defaults, privacy rules of V4 package B (fixed-literal log messages, classified fields, `core/tools/log_lint.py` at 0 violations). No hardware, no network, no new dependency.

## Goals (`core/src/jarves/goals.py`, tables `goals`, `goal_steps` in memory.db)
- The owner sets a goal by voice (`goal_add`), dashboard (Goals page), or CLI (`jarvis goals add`). Optional target date and steps. Progress = steps done / steps, or a percent the owner sets for a goal without steps. Status: active, paused, done, dropped.
- **A goal is a record, not an agent.** It never calls a tool, sends a message or spends money. Doing a step goes through the normal tools and their own approvals. A step flagged `outward` (other people or outside the PC) cannot be recorded as done by the model without an on-screen owner approval.
- **Weekly check-in only on request.** `goal_checkin` (voice), the Check-in button, `jarvis goals checkin`. Read-only summary (progress, stalled 7+ days, past date, next step). No scheduler job, no alert, no notification; a test asserts `jobs` and `alerts` stay empty.
- Stored locally, in the existing backup. Editable (title, details, date, percent, steps, status) and deletable. Delete asks the owner on the approval popup from the dashboard, CLI (`--yes`) and by voice (destructive tool).
- Sensitive text (passwords, long numbers, card/ID, health and money words) is refused and never stored.

## Adaptive Memory (`core/src/jarves/preferences.py`, table `learned_prefs`)
- Separate from the existing curator facts: only preferences the owner **states**. `pref_suggest` stores a `proposed` item that carries the owner's own words as evidence (no evidence = refused). A proposal influences nothing (`for_prompt()` returns confirmed, non-stale items only).
- Becoming `confirmed` needs an explicit yes: the `pref_confirm` tool always asks on screen/voice (any origin), or the owner presses Confirm / types it on the dashboard or CLI. Declined items keep only normalised text so the same suggestion is not asked again (visible, removable).
- Guards against silent profiling: sensitive filter (stricter than the curator's), at most 20 pending proposals, unanswered proposals expire after 14 days, a confirmed item not reaffirmed for 180 days is "stale" and unused until reaffirmed, similar confirmed items on one topic are reported as possible conflicts and never auto-merged.
- Every item is listable, editable, removable on the Learned preferences page, `jarvis prefs`, and by voice (`pref_show`, `pref_edit`, `pref_forget`, forget asks).
- Confirmed preferences are added to the instructions of every conversation (owner decision 21 Oct, built in `v5/pillars-followups`): small token budget, switch `prefs_in_prompt`, shown on the Context page.

## Privacy and logging
All `logs.*` calls use fixed literal messages and only classified fields (`id`, `n`, `by`, `status`); goal and preference text never reaches a log (test with a planted word). Dashboard text is shown with `dir="auto"` and not translated.

## Surfaces and tests
Features `goals`, `adaptive_memory` (guides `goals.toml`, `adaptive_memory.toml`, CLI parity entries, Hebrew catalog through `tools/catalog_add.py`). Tests: `core/tests/test_v5_pillars.py` (18, no skips, no browser, injected clocks, owner answers through the real approval service).

## Open for the owner
1. Should confirmed preferences be added to the model instructions now (small, listed on the Context page), or wait?
2. Answered 21 Oct: one calm line when a goal is finished, only when the owner is at the PC and not 22:00-07:00 (feature `goal_done_line`). Proactive offers (`proactive.py`) and local development drafts (`devmanager.py`) follow `docs/plans/PRIORITIES.md`.
