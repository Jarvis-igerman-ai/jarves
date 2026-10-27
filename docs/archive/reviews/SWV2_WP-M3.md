# SW-V2 WP-M3 — scheduler, reports, notes, file sandbox, alerts, autonomy (#17)

Scope: spec M3 + 6.5 + 6.7 + section 7, decisions D04, D06, D07, D11, D14. Branch of `swv2-integrate`
(merged up to `8178c15`). Pi verification follow-up: **#23** (real issue).

## What was built (Core, no second framework)

| Module (new) | What it does |
|---|---|
| `core/src/jarves/userdata.py` | M3 tables in the existing **memory.db** (one user DB to back up): jobs, job_runs, reports, notes, alerts, grants, proposals, audit, agent_settings (dashboard-editable, defaults in `DEFAULTS`). |
| `core/src/jarves/scheduler.py` | Durable scheduler: 5-field cron + one-off, IANA zone (default `Asia/Jerusalem`, dashboard setting), DST rules, Hebrew/English schedule parser ("כל בוקר ב-8", "מחר בשמונה וחצי בערב", "בימי חול ב-7:15", "every monday at 10", "בעוד 20 דקות", raw cron) → **draft + preview** → approval → active. Idempotent occurrences, once-only catch-up with max age + "late" label, no overlap, bounded retries/backoff, per-job AI-request budget, permitted Skills, crash recovery. Saved reports + `report_find` / `report_read` (reads the stored text, never regenerates). |
| `core/src/jarves/notes.py` | Notes (title/text/tags/created/updated): add, search, edit/append, Markdown export (sandbox or dashboard download), delete through the registry's destructive policy. |
| `core/src/jarves/sandbox.py` | Private workspace `<state_dir>/files` + explicit, revocable folder grants (`grant:<id>/…`). Path traversal, absolute/UNC/drive/ADS, reserved names, trailing dot/space, symlinks/junctions/reparse points, hard links, overwrite (opt-in, read-only protected), document-type allowlist (no executables; nothing is ever launched), size + quota guards, atomic writes. Grants: model can only *request* (pending); approval on the dashboard; system/program/AppData/JARVES-state folders and whole drives/user folder refused. |
| `core/src/jarves/alerts.py` | Event-driven alerts: priority → channel (popup / speech / os), quiet hours (speech → quiet popup; urgent may speak, configurable), pause, dedupe window, rate limit, pending queue when no output, expiry, readback alerts wait for a voice. |
| `core/src/jarves/autonomy.py` | Levels `observe_only` / `suggest_and_ask` (default) / `approved_low_risk`; proposals from self-diagnosis issues (false wakes → wake threshold +0.05 in config.toml, applies next start; repeated tool failure → feature off (medium risk); early responses → advice); closed applier list, scoped low-risk grants (dashboard only), revert, audit log. Never calls Skills, so no path to calendar deletion. |
| `core/src/jarves/operations.py` | Core wiring (one loop: scheduler tick → alert flush → proposal sync every 10 min; only local SQLite reads, never a voice session or model call to poll), alert outputs, feature catalog entries with Windows/Pi status, all M3 dashboard routes. |
| `core/src/jarves/dashboard_static/m3.js` | Minimal pages: Jobs (preview/approve/pause/run now/history/zone), Reports (read aloud, delete), Notes, Files (browser, download, grants), Alerts (settings, pending, recent), Autonomy (level, grants, proposals, audit). |
| `core/tools/m3_screens.py` | Headless dashboard captures on synthetic data. |

Voice/agent tools (all through `SkillRegistry.call`, gated by feature switches): `schedule_preview`, `schedule_approve`,
`jobs_list`, `job_control`, `job_delete` (destructive), `report_find`, `report_read` (untrusted output), `note_add`,
`note_find`, `note_edit`, `note_delete` (destructive), `note_export`, `file_write`, `file_read` (untrusted output),
`folder_grant_request`, `alerts_list`, `alerts_control`, `proposals_list`, `proposal_decide`.

### Behaviour decisions (documented in code)
- **DST**: non-existent wall-clock time (spring forward) → first valid minute after the gap (several cron minutes in
  the gap collapse into one run); repeated time (fall back) → runs once, first occurrence.
- **Catch-up (D14)**: only the LATEST missed occurrence runs, labelled late (`missed` = how many were skipped);
  older than the job's `max_age_s` (default 6 h) → recorded as skipped, no report. `next_run` is advanced before the
  run starts; the occurrence key (`job:UTC-minute`, UNIQUE) makes a duplicate start impossible.
- **Crash mid-run**: reminder/research runs left `running` by a dead process resume once (same run row, late);
  **tool** runs are marked interrupted and never repeated (side-effect tools also get `retries = 0`).
- **Approval**: voice can only create reminder/research jobs (no tool jobs); a spoken yes activates them
  (`schedule_approve` after the read-back preview); tool jobs need the on-screen/dashboard approval.
- **Reminders vs proactive**: switching `proactive_alerts` off still delivers what the user asked for (reminders,
  readback).
- **Speech output**: open GPT-Live conversation → `announce` (waits ≤ 20 s for a good moment, else retried next
  tick); otherwise the Core TTS when nothing else is talking. OS notifications only through `core.os_notify`
  (WP-M1 tray); none on Pi yet.

## Technology check (19 Oct 2026)
- Time zones: stdlib `zoneinfo` + **tzdata 2026.5** (PyPI, resolved by `uv lock` today; required on Windows, which
  ships no IANA database). Added to `core/pyproject.toml` + `uv.lock`.
- Cron: a small in-repo evaluator (~60 lines) instead of a third-party cron library: no new dependency, and the DST
  semantics (which differ between libraries) are ours and tested explicitly (New York and Jerusalem transitions).
  Not re-checked against current cron libraries online in this package (no web lookup was done).
- Storage: existing SQLite (WAL) memory.db, no new database (spec 12).

## Tests and evidence
- New: `core/tests/test_m3_scheduler.py` (cron, DST spring/fall NY + Jerusalem, 17 Hebrew/English phrases, refusals,
  draft never runs, idempotency, **reboot catch-up once + late**, max age skip, reminder, overlap, retry/backoff,
  permanent error, job budget, **crash mid-research resume**, **crash mid-tool never repeated**, permitted Skills +
  destructive/disabled policy, scheduler feature off, idle ticks never call the model, real-time loop + run now,
  voice preview→approve→report readback without regeneration), `test_m3_sandbox.py` (22 malicious paths on the direct
  API, voice/agent tools, scheduled job, notes export and dashboard API; junction, hard link, read-only, quota,
  executables, grants lifecycle, forbidden grant roots), `test_m3_notes_alerts.py` (notes CRUD/search/export/
  delete approval; quiet hours, dedupe, rate limit, pending queue, pause, feature off, readback, broken output
  fallback, settings validation; autonomy levels, grants, revert, no destructive path, voice policy),
  `test_m3_core.py` (real Core: Skills + feature gating, reminder spoken via fake TTS then popup on a real face
  websocket, **M3 exit: missed daily job runs once after a real Core restart, stored, read back; third start runs
  nothing**, every M3 dashboard API over real HTTP).
- Full suite (worktree venv, `uv sync --extra voice --extra camera`): **637 passed, 2 skipped** (`python -m pytest
  core/tests -q`). Silent: SimAudio / fake TTS only, no network.
- Screenshots (automatic headless Edge captures on synthetic data, not a hands-on UI check):
  [jobs light](../../reviews/swv2_m3/m3_jobs_light.png), [jobs dark](../../reviews/swv2_m3/m3_jobs_dark.png),
  [reports](../../reviews/swv2_m3/m3_reports_light.png), [notes](../../reviews/swv2_m3/m3_notes_light.png), [files](../../reviews/swv2_m3/m3_files_light.png),
  [alerts](../../reviews/swv2_m3/m3_alerts_light.png), [autonomy](../../reviews/swv2_m3/m3_autonomy_light.png).
- Real API spend: **$0** (no paid calls were needed; research runs use the existing tasks.py runner, tested with
  fakes).

## Windows / Pi status (feature catalog, `/api/m3/platforms`)
All six features (`scheduler`, `reports`, `notes`, `files`, `proactive_alerts`, `autonomy`): Windows
`implemented_unverified` (unit + fault-injection tests on Windows, no real-restart drill by the user yet).
Pi: `implemented_unverified` (portable code, never run on a Pi), `proactive_alerts` = `planned` (no Linux/Pi OS
notification adapter). Blockers and the roadmap link (#23) are in `operations.M3_FEATURES`.

## Integration hooks (files owned by other packages)
1. `core/src/jarves/app.py` (Core.start): `from .operations import attach as ops_attach; self.ops = ops_attach(self)`
   right after the background-task Skill, before `_write_tools_md()`; Core.stop: `await self.ops.stop()` after
   `tasks.shutdown()`.
2. `core/src/jarves/dashboard.py`: `"jarves.operations"` added to `PLUGIN_MODULES`; `Ctx.close()` closes the
   plugin connection `_userdata`.
3. `core/src/jarves/dashboard_static/index.html`: six nav links + `<script src="/static/m3.js" defer>` after
   pages.js; m3.js registers into `PAGES` (no app.js change).
4. `core/pyproject.toml` + `core/uv.lock`: `tzdata>=2025.2` (2026.5 locked).
5. `jarves.features.register`: M3 passes `targets=` (Windows/Pi status) automatically **if** WP-M4 adds that
   parameter (checked with `inspect.signature`); until then the same data is served at `/api/m3/platforms`.
6. WP-M1 tray: set `core.os_notify = async (title, text, quiet) -> bool` to enable OS notifications; "Pause
   Proactive Alerts" can call `core.ops.alerts.pause(minutes, by="tray")` / `.resume(by="tray")`.

## Open questions
- **Quiet hours default 22:00–07:00 and urgent-may-speak = on**: provisional; user may want different hours
  (Dashboard → Alerts). USER DECISION (low stakes, editable).
- **Israeli work week for "ימי חול" = Sunday–Thursday** (English "weekdays" = Monday–Friday). Recommendation: keep.
- **Default max age 6 h** for catch-up (a morning report missed for more than 6 h is skipped). Recommendation: keep;
  per job editable.
- Shared main venv: another session's `uv sync` on `C:\work\jarvis\core\.venv` removed tzdata during this work
  (the main lock lacked it). After merge, run `uv sync` once in the main checkout; without tzdata the Core reports
  health `scheduler: unavailable` (no wrong times).
