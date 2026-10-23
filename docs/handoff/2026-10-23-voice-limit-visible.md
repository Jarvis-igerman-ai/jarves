# Handoff: no silent limits (voice minutes first)

**Asked (owner incident + coordinator rule 23 Oct):** `ai.daily_realtime_minutes` (30) was used up; every GPT-Live session
then opened with ~0 s left, ended by `LIVE_TIME_LIMIT`, reconnected 4 times (`LIVE_RESUMED`) and stopped at
`LIVE_RESUME_LIMIT`, and nothing told the user it was a limit. Rule: no cap may stop or degrade a feature silently.

## What changed
- `core/src/jarves/limits.py` (new, ONE helper): turns used/max of a cap into `ok | warn (>=80%) | exhausted` with EN+HE
  words, the setting to raise and the command (`jarvis budget set ...`). Covers `voice_seconds`, `ai_requests`,
  `delegation_usd_micros`. Voice is "exhausted" below 20 s left (no useful session).
- `app.py`: `announce_limit()` = log WARNING `DAILY_LIMIT_REACHED` (limit_code, used, limit, setting), face/dashboard
  `error` with code `VOICE_DAILY_LIMIT` / `AI_DAILY_LIMIT` / `DELEGATION_DAILY_LIMIT` (+ used/max/setting), a descending
  chime and a LOCAL spoken sentence (never cloud; HE/EN by `voice.language`), debounced 30 s per kind.
  `_start_live` opens no cloud session when < 20 s of voice remain. `LIVE_ENDED` reason `timeout` with the budget gone is
  announced and NOT resumed. `Budget` has an `on_exceeded` hook, so EVERY refusal by a cap (`reserve`, `reserve_many`,
  `check`) is announced, not only the live path. `limits_refresh()` (every 20 s + on each live end) sets health
  components `voice_budget`, `request_budget`, `delegation_budget` (degraded while exhausted; at >=80% status ok with
  detail + action) and logs `DAILY_LIMIT_80` once per day per cap. Snapshot has `limits` and `limit_lines`.
  `LIVE_RESUME_LIMIT` now also shows an on-screen error (3/3 in 10 min). Idle disconnect and session max
  (`voice.idle_disconnect_s`, `voice.session_max_s`) show an on-screen line naming the setting.
- Surfaces: `jarvis status` prints `LIMIT REACHED:` / `WARNING:` lines FIRST (also when the Core is stopped);
  `jarvis doctor` and `jarvis selftest` (new check `limits`); `jarvis budget status`; tray tooltip (runtime view `limits`);
  dashboard Home alert cards (`/api/home` `limits`, status turns "warn").
- CLI to raise: `jarvis budget set --voice-minutes N --requests N [--daily-usd X]` (config_edit, validated, backed up).
- Guide `guides/data/daily_limits.toml` (EN+HE, probe `limits`); Hebrew dashboard strings via `tools/catalog_add.py`.
- Tests: `core/tests/test_limits.py` (fake budget numbers, fake live, SimAudio, fake local TTS; no mic/cloud). Updated
  `test_core` (code is now `VOICE_DAILY_LIMIT`) and `test_selfspeed` (new selftest check).

## Limits covered / not covered
Covered loudly: live voice minutes, AI requests per day (also the `budget` live-end), delegated daily USD (via the
Budget hook), `LIVE_RESUME_LIMIT`, session max and idle disconnect (on-screen line, not spoken: they are by design).
NOT covered (listed for the owner): delegation per-turn caps (`delegation_turn_usd`, `turn_requests`, `max_searches`,
`timeout_s`) and the fallback ladder in `voice/delegation_client.py` still degrade by their own messages; the scheduler job
cap (`scheduler.py:489`) and background tasks (`tasks.py`) log but do not use `announce_limit`; per-feature spend gates
elsewhere (monthly API budget alerts exist already in usage.py). The Godot face may not render the extra `error`
payload fields (`used`, `max`, `setting`); it shows `message`/`action` as before.

## Verification
`python -m pytest tests/test_limits.py tests/test_core.py tests/test_budget.py tests/test_guides.py tests/test_cli_parity.py
tests/test_dashboard_locales.py tests/test_selfspeed.py` green. `test_runtime::pystray`, two `test_selfcheck` tests failed
in this sandbox (no pystray; bundle checks) and are untouched by this change. NOT verified: a real limit on a real
microphone/speaker, the real Windows/espeak local voice reading the notice, the dashboard card visually, the tray on Windows.
