# Handoff: no silent limits, the rest (follow-up to PR #106)

**Asked (owner rule 23 Oct):** no cap may stop or degrade a feature silently. #106 did voice minutes, daily AI requests and
daily delegated USD. This covers the remaining caps, with the same helper (`core/src/jarves/limits.py`).

## What changed
- `limits.py`: new EVENT limits (a cap that stopped/degraded ONE thing, not a daily counter): `EVENTS` (10 codes),
  `event_view`, `report(code, detail, value)` (never raises: logs WARNING `LIMIT_EVENT` with `limit_code`, calls the Core's
  sink), `event_spoken`, `event_lines`, `events_from_log` (last day from jarves.log, no Core needed).
- Reported where the cap bites: `voice/delegation_client.py` (`DELEGATION_TURN_USD`, `DELEGATION_TURN_REQUESTS`,
  `DELEGATION_TIMEOUT`, `DELEGATION_SEARCHES` when a request used all `max_searches`, `DELEGATION_FALLBACK` on every ladder
  step down); `tasks.py` (`TASK_LIMIT`, `TASK_TIMEOUT`); `scheduler.py` `JobBudget.reserve` (`JOB_REQUEST_LIMIT`, covers the
  plan route and the API route); `app.py` (`VOICE_SESSION_MAX`, `VOICE_IDLE_DISCONNECT`).
- Core (`app.py`): `on_limit_event` sink (thread safe), `announce_event` (face/dashboard `error` with the code + setting,
  health component `limit_events`, debounce 30 s per code), `recent_limit_events()` (1 h), snapshot `limit_events`.
  Session max and idle disconnect are now SPOKEN by the local TTS (no cloud), awaited before an automatic reconnect so the
  notice and the new conversation never overlap; the screen line stays. Delegation notes (`TURN_CAP_NOTE`, `TIMEOUT_NOTE`) now
  name the setting, because the live model speaks those; no second local voice over a live session. Tasks and scheduler jobs
  are unattended: log + face + status, not spoken.
- Surfaces: `jarvis status` prints `LIMIT HIT:` lines first (running Core); `jarvis doctor` (warn per hit, from the Core or the
  log); `jarvis selftest` check `limits` (warns on hits in the last day); dashboard Home alert cards (`/api/home`
  `limit_events`); `jarvis budget status` shows task caps and conversation caps.
- New settable caps: `jarvis budget set --task-running N --task-timeout S` (`ai.task_max_running`, `ai.task_timeout_s`, added to
  `config_edit.EDITABLE`, so also the dashboard Settings page). Hebrew labels via `tools/catalog_add.py` (part `limits-rest`).
- Guide `guides/data/daily_limits.toml` (EN+HE): new step and two problems. CLI parity unchanged (`budget` already maps the feature).
- Tests: `core/tests/test_limits_events.py` (9 tests, offline).

## Grep result for other cap paths (item 4)
Covered by the daily Budget hook already: every `budget.reserve/reserve_many/check` refusal (brain, chatgpt_brain, curator,
tasks, delegation, scheduler through the shared budget). Not covered, listed for the owner:
- `ai.delegation_max_searches` is only reported when a request used ALL allowed searches (we cannot see the model wanting more).
- Fixed, non-configurable caps stay quiet by design: `TaskManager.max_queued` (5, counted in `TASK_LIMIT`), `NOTE_MAX_CHARS`
  truncation of delegation notes, `PlanResearchRunner`/`ResearchRunner` `attempt_s` timeouts (surface as task `timeout`/failed),
  the scheduler `BACKOFF_CAP_S`, and log/rotation caps. Monthly API budget alerts live in `usage.py` (already visible).
- The Godot face may not render the extra `error` fields (`setting`); `message`/`action` show as before.

## Verification
Targeted: test_limits, test_limits_events, test_core, test_budget, test_guides, test_cli_parity, test_dashboard_locales,
test_selfspeed, test_tasks, test_m3_scheduler, test_delegation_client, test_dashboard, test_dashboard_pages green (run with
`PYTHONPATH=core/src`; the editable install in this sandbox pointed at another worktree). NOT verified: a real cap on a real
microphone/speaker, the real local voice reading the conversation-end notices, the dashboard cards visually.
