# SW-V3 Self-check: self-diagnostics and improvement suggestions (20 Oct 2026) — #17

Branch `v3-selfdiag` (from `origin/main` @ `adaac80`). Owner request (20 Oct): JARVES runs SELF-DIAGNOSTICS and
SUGGESTS IMPROVEMENTS, on demand and on a schedule. Owning layer: Python Core (`core/src/jarves/selfcheck.py`), plus
the dashboard page (`dashboard_static/selfcheck.js`). No Godot, FreeCAD or Blender changes.

## What one Self-Check run does

The run is built on the existing pieces. It does not copy them:

| Area | Source | Findings (fingerprint) |
|---|---|---|
| Health | live Core components (`core._health_components`), else `health.json` only while the Core lock is held and the snapshot is < 120 s old | `health:<component>` (high when a core component such as ai/audio/speech_out/link/wake_word is unavailable) |
| Logs (last `self_check.days`, default 7) | `jarves.log` + rotations, JSON lines, bounded to 300k records | `crash` (a process with `CORE_STARTING`, no `CORE_STOPPED`, followed by a later start), `log:<LEVEL>:<CODE>` clusters (ERROR >= 2, WARNING >= 5; high when the ERROR comes back in >= 3 separate runs), `latency` (p50/p90/p95 of `LIVE_JARVES_SAID wait_s` vs `history.dead_air_s`), `budget:limit` (`AI_BUDGET` days), `budget:burn` (average estimated cost/day vs `pricing.daily_alert_usd`), `wake:false` (`VOICE_NO_SPEECH` vs `WAKE_WORD`), `wake:near` (`WAKE_NEAR_MISS`), `tool:<name>` (`TOOL_RESULT ok=false` across conversations), `claims` (`LIVE_FALSE_ACTION_CLAIM`), `avail:pear/google/camera` (their error codes + feature availability), `update` (cached `updates.json`, no network) |
| System | `shutil.disk_usage`, `GlobalMemoryStatusEx` (Windows) / `/proc/meminfo` (Pi), size of the state folder | `disk`, `memory`, `state_size` |
| Config sanity | the loaded `Config` | `config:link_host` (face link not on loopback), `no_ai` (daily limit 0), `no_progress` / `progress_late`, `no_deadline` / `deadline_late` (inconsistent with `history.stall_s`), `review_share`, `log_level`, `history_off`, `wake_low` |
| Features | `Features.list()`, `setup_wizard.hint/BUNDLE/first_run`, guides | `feature:<id>` (on but unavailable, with the guide's next step), `setup` (never finished), `bundle:<id>` (code/model/data missing from the install), `useful:<id>` (backup / update_check / proactive_alerts / scheduler off; never a SAFE_OFF privacy feature), `unused:<id>` (briefing / notes / scheduler / music never used in >= 3 conversations, with the guide's example phrase) |
| Conversations | `diagnose.run` (existing rules, deduplicated) then its open issues | `issue:<fingerprint>` for the top 3 high/medium issues (by issue id, no transcript), `issues:other` |

Each finding has severity, Hebrew + English title/detail/cause/fix, evidence (log lines as
`time level component CODE: redacted message`, ids; never log fields and never conversation text), a root-cause
hypothesis, a concrete fix or a setting change (`action`), effort (minutes / hour / bigger) and a count. Ranking:
severity, then category weight (health > logs > features/availability > voice/tools/budget > wake/config/system >
conversations > usage), then count. Health score = 100 − 15·high − 6·medium − 1·low.

Storage: `store.db` tables `selfcheck_runs`, `selfcheck_findings`, `selfcheck_state` (accept / dismiss / snooze per
fingerprint; a snooze ends by itself). The last 100 runs are kept. Trend vs the previous run: new / persisting /
resolved fingerprints, the score delta, and per finding new / worse / same.

## How to run it

- Voice: tools `self_check_run` ("תבדוק את עצמך" / "run a self check"), `self_check_suggestions`,
  `self_check_decide` (accept/dismiss/snooze by number; accepting a settings change asks again through
  `registry.confirm` before it is applied), `self_check_ticket`. The spoken summary looks like "מצאתי 3 בעיות.
  העיקרית: … מה לעשות: …" / "3 issues, the main one is: …".
- CLI: `jarvis diagnose --now [--days N] [--ai|--no-ai] [--json] [--ticket]`, `jarvis diagnose --history`,
  `jarvis diagnose --schedule "every sunday at 10" | off`,
  `jarvis diagnose --suggestion FINGERPRINT --op accept|dismiss|snooze|reopen [--snooze-days N]` (accept → a terminal
  y/N before applying). Plain `jarvis diagnose` keeps working as before (conversation rules, 30 days).
- Dashboard: a new **Self-check** page under Activity, with Check now (an optional AI-review box), the score and trend,
  ranked cards (evidence, cause, fix, accept / snooze 7 days / dismiss, "Approve change #n"), a ticket draft
  (Markdown in a text box, never posted) and history. APIs: `GET /api/selfcheck`, `GET /api/selfcheck/<id>`,
  `GET /api/selfcheck/<id>/ticket`, `POST /api/selfcheck/run|decide|settings`.
- Scheduled: one scheduler job (`action=tool`, `self_check_run`, owner `system`), cron `0 10 * * 0` (Sunday 10:00,
  the owner's zone), created once and approved as the owner's 20 Oct request. Its time can be changed on the Jobs
  page or with `--schedule`. A job the owner deletes is not created again. The scheduler saves each run as a report
  and raises its normal "report ready" alert.
- Automatic: after an unclean stop (`self_check.on_start` = crash by default; `always` = every start; `never`) and
  after `self_check.error_burst` (10) ERROR records within one Core run (counted by a log sink, not by polling).
  These runs happen at most once per `self_check.min_interval_s` (3600 s). They report through the alert engine
  (normal priority, high when a high finding is open), so quiet hours apply: speech becomes a silent popup.
  `--schedule` warns when the chosen time falls inside quiet hours.

## Approvals, cost, privacy

- A suggestion that changes something becomes an **autonomy proposal** in `memory.db` (the existing approvals
  flow; there is no separate approvals service on `main`). Two new closed-list appliers:
  - `config_setting`: medium risk. Only `config_edit.EDITABLE` keys, validated and backed up, revertible.
  - `feature_on`: medium risk. Refuses SAFE_OFF features and runs `feature_setup.prepare` first.

  The wake-threshold change reuses the existing `wake_threshold` applier under the same fingerprint as
  `autonomy.sync`, so the two never create duplicate proposals. Medium risk can never be granted for automatic use,
  so nothing is applied until the owner approves it: on the dashboard, with a terminal "y", or with a spoken yes.
- AI pass = only `diagnose.llm_review` (the already-enabled path). It needs `self_check.ai_pass` on, the
  `diagnosis_llm_pass` switch on, the API-key route and privacy off. It respects the daily review cap and reserves
  from the shared Budget. It skips when fewer than max(5, 10 %) daily requests remain, and reviews at most
  `self_check.ai_sessions` (2) unreviewed voice conversations. The estimated cost (delegation rows of kind `review`)
  is shown in the report, the summary, the CLI and the dashboard.
- No conversation text enters a self-check report or ticket draft (test-enforced with a planted transcript). The
  ticket draft also omits AI-review titles and replaces e-mail addresses.

## Feature registration

Feature `self_check` (on by default, `skills=["self_check"]`). It has a capability row (Windows and Pi
`implemented_unverified` with evidence/blocker, roadmap #18), the wizard group `core`, the bundle check (module,
`selfcheck.js`, guide), the guide `guides/data/self_check.toml` (aliases moved from `diagnosis`), Hebrew texts on
every finding, the nav entry and search keywords, the plugin route module, and `userdata.DEFAULTS` `self_check.*`.

## Tests (focused, one file at a time; the machine was low on RAM)

Run from `core/` with `core/.venv/Scripts/python.exe -m pytest -o pythonpath=src`. This worktree used the main
checkout's venv.

| File | Result |
|---|---|
| `tests/test_selfcheck.py` (new, 19 tests) | 19 passed |
| `test_guides.py` | 24 passed |
| `test_m3_core.py` / `test_m3_scheduler.py` / `test_m3_notes_alerts.py` | 4 / 57 / 16 passed (one invariant updated: the closed applier list now has 4 entries, the new ones medium risk) |
| `test_capabilities.py` / `test_setup_wizard.py` / `test_channels.py` | 7 / 21 / 27 passed |
| `test_dashboard.py` / `test_swv3_p01.py` / `test_features.py` / `test_core.py` / `test_feature_setup.py` | 10 / 24 / 19 / 17 / 26 passed |

How `test_selfcheck.py` works:
- It uses the real Store, UserDB, Scheduler, Autonomy, Features, config_edit, CLI `main`, dashboard HTTP server, and
  two real Core start/stop cycles, all in tmp dirs.
- It injects synthetic `jarves.log` files and uses a fixed clock (`T0`).
- Only the OpenAI client and the disk/memory numbers are faked.
- Waits are event-driven: the test gathers the spawned tasks; there are no timed sleeps.
- No skipped tests.

The full suite was **not** run, on the coordinator's instruction (RAM). Final-commit CI is not claimed.

## UNVERIFIED and known gaps

- Not run against the owner's real logs and real Core. It has not been used by voice in a live conversation.
- Pi: `/proc/meminfo` path is untested on device.
- Hebrew rules from `origin/v3-p02-dashboard` (`hebrew.py`, `tests/test_hebrew_texts.py`) are **not on main**.
  Findings carry their own `*_he` fields; the page uses `tx(he, en)`. When P0.2b merges, its
  `test_every_registered_feature_has_a_hebrew_description_evidence_and_blocker` will need `hebrew.TEXT` entries
  for the `self_check` about/evidence/blocker strings (merge note).
- A crash of the **last** process (the Core is not running now and nothing started after it) is not counted until the
  next start. That next start is exactly when the automatic crash check runs.
- `bundle:` checks import modules (cv2, agents): a self-check takes ~1–3 s on this PC; acceptable for on-demand/weekly use.
