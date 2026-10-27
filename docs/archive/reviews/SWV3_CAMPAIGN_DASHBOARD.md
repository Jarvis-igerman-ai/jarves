# SW-V3 campaign, Pass D (dashboard) and log review

20 Oct 2026 · branch `v3-campaign-dash` (off `origin/v3-final` d701ef4) · issue [#17](https://github.com/igalgerman/JARVIS/issues/17) · bugs: [SWV3_CAMPAIGN_BUG_TABLE_DASH.md](../../reviews/SWV3_CAMPAIGN_BUG_TABLE_DASH.md) · plan: [FULL_TEST_PLAN](../../plans/FULL_TEST_PLAN.md). Screenshots: [swv3_campaign/](../../reviews/swv3_campaign) (68 files, test-profile and repository demo data only; the one conversation is the `session_2026-10-19.jsonl` test fixture plus a typed "what time is it" demo; no real conversations).

## Setup

- Real Core from this worktree's source (`jarves run`, offline TEST profile created with `jarvis profile create test --test`, own temp `JARVES_HOME`, link port 8768, AI offline, voice off) and the real dashboard (`jarves dashboard`, port 8791), driven by **Playwright 1.x Python, `channel="msedge"`, headless**, one browser per page group (RAM was about 1.2 GB free).
- Demo data: the repo fixtures `dashboard_screens.fill` / `m4_dashboard_screens.fill` (19 Oct fixture conversation, typed chat, task, memory, wake model) plus a **synthetic context trace** for the typed conversation (the offline profile captures none).
- Scripts (scratchpad, not committed): `dpass.py` (page sweep), `dshell.py`, `dconv.py`, `dedit.py`, `dflows.py`, `dctx.py`, `dkey.py`.

## Coverage and results

| Area | What ran | Result |
|---|---|---|
| Every page, 3 languages, 2 widths | 31 pages (Home, Controls, Connections, Devices, Conversations, Issues, Self-check, Background tasks, System log, Calendar, Jobs, Reports, Alerts, Notes, My files, Browser, Identity, Appearance, Memory, Persona files, Setup, Features, Skills, New Skills, Autonomy, Usage, Costs, Backup, Settings, Profiles, Guides) x en/he/ar x 1440/390 px = **186 loads**, final sweep after fixes | **0** console errors / 4xx pages, **0** wrong `dir` (en ltr; he, ar rtl, also `<body>` computed direction), **0** horizontal overflow after fixes (6 overflow findings before: runtime en/ar 390, costs x3 widths) |
| Dynamic pages | session, context (6 tabs), raw, guide, guide-for, workspace/NAME, features/{enabled,not_ready_on_pi,needs_attention,all}, issues/{open,resolved,all} | pass; context tabs checked in en/he/ar at 1440 and 390 |
| Navigation | 7 groups, 31 links; phone drawer opens, Escape closes, scrim; active item follows the route | pass |
| Search | Ctrl+K and `/` focus, results for pages / features / guides / conversations / log, Enter navigates, Escape closes; queries in English, Hebrew, no-hit | pass (desktop + phone) |
| Language picker | en/he/ar/ru, persists across reload, `lang`/`dir` correct each time | pass |
| Theme | toggle, persists, dark background `rgb(18,18,17)` | pass |
| Status chips, usage strip | Running, Device Windows, "Pi: 1/50 ready", mic/camera/audio chips, ChatGPT/OpenAI/Today strip on every page | pass; scrolls horizontally on phone |
| Privacy | button toggles OFF <-> ON, top-bar chip follows ("Privacy: on"), toggled back | pass. Mask/Delete not clicked |
| Mic/camera strip, "What does he see?" | strip shows Off / No camera / No speaker / No audio sent; preview button disabled | pass for the offline state; live states **BLOCKED** (no camera feature/OpenCV, Pass E) |
| Conversations | list, search (hit / no hit / clear), kind and checkbox filters, Clear, row click, **row keyboard (fixed, D-04)**, session page, Context tab, raw page | pass |
| Logs | levels (ALL...ERROR counts 465/465/465/465/6/2), component list, free text, datetime/ID/file controls present, download link | pass |
| Issues / Costs / Usage | open/resolved/all, costs chart + tables, usage meters | pass; costs 3 px overflow fixed |
| Settings editing | edit number, Save (config.toml + backup written), **Undo (fixed, D-05)**, out-of-range value rejected with the Core's message, file restored byte for byte | pass in en 1440/390, he 1440, ar 390 |
| Persona file editing | AGENTS.md, SOUL.md, MEMORY.md, USER.md: edit, Preview, Save (verified on disk), Undo last change | pass (en, desktop and phone); he/ar edits pass (undo button found by English label only) |
| Features | 50 features, 4 filters, On/Off toggle applied and logged (`FEATURE_APPLIED`) | pass |
| Guides | 53 tiles, guide opens | pass |
| Setup wizard, Connections | pages render, toggles list, Guide links | pass; add-on installs and Google/Telegram BLOCKED (accounts) |
| Approvals / Autonomy | page, mode select (3 modes), proposals with Approve/Reject | rendered; buttons not clicked |
| Self-check | page, Check now runs (score 0/100 in this venv, 15 findings, history row), Accept/Snooze/Dismiss rendered | pass; layout P3 |
| Profiles | list, create `campdemo`, open/make default/delete buttons | pass |
| Browser | allow-list add + remove, Playwright-not-installed notice | pass |
| Jobs | new job preview creates a draft, Approve button, Pause/Run now/Delete, time zone and workweek settings | pass; Hebrew/English text fixed (D-08) |
| Memory, Notes, Alerts, Backup | add + delete fact, add note, Test alert (alert listed), Back up now + Verify | pass |
| Files | grant a missing folder is rejected with "that folder does not exist" | pass (403 code, D-15) |
| Keyboard | skip link first, Tab order through nav, Enter on rows, focus rings | pass after D-04 |

Untranslated visible text (nodes, excluding the System log page which is technical English by nature): English UI 67 (all Hebrew data/voice texts: your own sentences, Core voice messages), **Hebrew 952** at 1440 (largest: Settings 156, Devices 149, Identity 121, Features 104, Skills 92), Arabic about 3,050 (by owner decision Arabic/Russian are partial; direction and English fallback are correct). Many counted nodes are one-word technical tokens (`JARVES`, `Windows`, keys); the multi-word Hebrew gaps are listed as D-10.

Focused tests run (one pytest at a time): `tests/test_dashboard.py` + `tests/test_dashboard_locales.py` **29 passed** (two new tests for D-02/D-03/D-04). The full suite was not run (RAM).

## Fixes (all on `v3-campaign-dash`, each retested)

D-01 P1 Home "JARVES is off" while the Core runs (health heartbeat), D-02 P2 429 from static files, D-03 favicon 404, D-04 row keyboard access, D-05 Undo after save, D-06 runtime table overflow, D-07 costs overflow, D-08 Hebrew schedules in the English UI, D-09 Hebrew gaps. Details in the bug table. **9 fixed, 9 open (all P3), 0 open P0-P2.**

## Log review

Inputs: this run's Core console log and `jarves.log` (test profile: 2 Core starts, ~200 INFO events, my settings/file/backup/alert/job/self-check actions), the dashboard process output, the browser console of 186 page loads, and the baseline [ALL_RETAINED_LOGS_19OCT2026.md](ALL_RETAINED_LOGS_19OCT2026.md).

| Source | WARN / ERROR | Class | Action |
|---|---|---|---|
| Core log of this run | **0 WARNING, 0 ERROR** (all INFO: SKILL_REGISTERED x101, STARTUP_SUMMARY, CORE_READY, SCHEDULER_STARTED, JOB_ACTIVE, BACKUP_DONE, ALERT_RAISED, FEATURE_APPLIED) | clean | none |
| `LINK_FACE_DISCONNECTED` (1 WARNING in the file) | belongs to the copied 19 Oct fixture, not this run; baseline says its 16 such warnings are expected at shutdown | expected | none |
| Core vanished at 10:41 and 10:54 (no `CORE_STOPPING`, no error) | killed from outside with every other `jarves.exe` on the PC | environment, expected | self-check correctly flagged "stopped without a clean shutdown" |
| Self-check findings (speech_out/winrt, Chimes/Wake word not installed, camera OpenCV) | health entries, not log WARNs; truthful for a venv without voice/camera extras | expected | none |
| Browser: `404 /favicon.ico` on every load | **noise bug** | fixed (D-03) |
| Dashboard: `429` for static files and API during a normal sweep | **bug** | fixed (D-02) |
| Dashboard: `400` on an out-of-range setting, `403` on a missing folder grant | expected validation (403 code is D-15, P3) | none / P3 |
| Home "JARVES is off" while running; Skills "snapshot 8 min old" | **bug** (health file only written on change) | fixed (D-01) |

Comparison with the 19 Oct baseline (21 WARNING, 0 ERROR: 16 face disconnects, 3 unsuccessful tool results, 1 music pairing, 1 stale budget reservation): none of those classes recurred in this offline run (no face, no music, no voice, no tools called), so this log neither confirms nor retires them; they need the voice pass (C) and the owner-assisted pass (E).

## Not covered / limits

Offline TEST profile only: no voice, camera, music, Google, Telegram or paid calls. Screenshots are viewport captures of the real pages (full-page captures are used for the 390 px set and show the sticky bar mid-page, an artifact of full-page capture). Hebrew/Arabic text was judged by script detection plus visual review of selected pages, not by a native reader.
