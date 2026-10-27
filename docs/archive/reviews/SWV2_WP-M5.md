# SW-V2 WP-M5: usage monitor, backup/restore, diagnostic bundle (#17)

Spec: `docs/JARVES_SW_V2_IMPLEMENTATION_SPEC.md` M5, sections 10 and 12 (D10, D13, D17). Branch `worktree-agent-a49df9cf52fa5859c`, based on `swv2-integrate`. Pi follow-up: [#22](https://github.com/igalgerman/JARVIS/issues/22).

## What the providers expose (checked 19 Oct 2026)

| Source | What it gives | Used as |
|---|---|---|
| OpenAI API response headers `x-ratelimit-{limit,remaining,reset}-{requests,tokens}` (+ `-project-tokens`) ([rate limits guide](https://developers.openai.com/api/docs/guides/rate-limits)) | per-minute **rate** limits, not spend | `provider_authoritative`, never alerts (it refills in about a minute) |
| Costs API `GET /v1/organization/costs` ([reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs)) | organization cost in daily buckets; needs an **Admin API key** | month-to-date USD, `provider_authoritative`, only when `OPENAI_ADMIN_KEY` is set; read-only GET, at most hourly, no model call |
| Project monthly budget or hard limit | **not exposed by any API** (Settings > Limits only) | the user can type in his own budget (`limit_source: your setting`) |
| Realtime `rate_limits.updated` server event | rate limits during a GPT-Live session | `observe_realtime_rate_limits()` is ready; needs a call from `live.py` (hook below) |
| ChatGPT plan route (Sign in with ChatGPT) | no documented quota, remaining or reset. The `subscription_sharing_usage_limit_exceeded` error says the limit is reached. Codex-style `x-codex-primary-*` headers are undocumented ([search](https://community.openai.com/t/weekly-limit-for-codex-how-can-i-determine-when-it-ends-resets/1359565)). | the limit error becomes "limit reached" (`provider_authoritative`, reset Unknown). Headers are parsed only if the route ever returns them (confidence medium). Otherwise the meter shows **Unknown**. No scraping, no hidden endpoints. |

## Usage monitor (`core/src/jarves/usage.py`)

- **Meters:**
  - `chatgpt_plan.quota` (percent, or Unknown)
  - `chatgpt_plan.jarves_requests` (this install's plan requests today: a local counter, not the whole account)
  - `openai_api.spend_month` (USD; Costs API or a labelled **estimate** from store.db x [pricing])
  - `openai_api.voice_minutes` (GPT-Live minutes against `ai.daily_realtime_minutes`)
  - `openai_api.rate_*` (only when observed)
  - `jarves.ai_requests` (the shared daily cap)
- **Every meter carries:** provider, source, metric and unit, used, limit with its source, window, reset, last verified, confidence, scope and a category (`provider_authoritative` / `app_local_counter` / `estimate` / `unavailable`).
- **Percent:** shown only when both the used value and a real limit are known; otherwise `Unknown`.
- **Separate units:** tokens, requests, minutes, USD and plan percent are never mixed.
- **Alerts:**
  - Default thresholds are 75/90/100 % and can be changed.
  - Each fires once per meter and window (`usage_alerts` table in memory.db, written with `INSERT OR IGNORE`, so it holds across processes and reboots).
  - When several thresholds are crossed at once, one alert is sent for the highest.
  - A window changes only on real evidence: a new local day or month, or a provider-reported reset that has passed. A falling percent alone is not treated as a reset.
- **Delivery:** `usage.ALERT_SINK` calls `jarves.alerts.emit(alert_dict)` when WP-M3's module exists. Until then it writes a `USAGE_THRESHOLD` warning (kept in store.db events). The 100 % state stays visible on the dashboard whatever the delivery settings say.
- **No automatic fallback:** when the plan quota ends, `chatgpt_brain` pauses and never moves to the API key. This was already covered by `test_plan_limit_pauses_without_api_fallback`; the meter now says so.
- **Voice tool:** `usage_status` (read). It answers "כמה נשאר לי?" in Hebrew and says "I don't know" for Unknown values.
- **Dashboard:**
  - Usage page: meters grouped per provider, alert settings, budget, Costs API mode, alert history.
  - A two-part strip at the bottom of every page (ChatGPT plan | OpenAI API).
  - Routes: `GET /api/usage`, `POST /api/usage/settings`, `POST /api/usage/refresh`.
- **CLI:** `jarvis usage [thresholds 75,90,100 | budget 20 | costs-api auto|on|off]`.
- **Settings:** memory.db `settings` keys `usage.*`, so they are included in backups.

## Backup and restore (`core/src/jarves/backup.py`)

- **Archive:** a zip in `<data>/backups/`, written as `.partial` and renamed only after verification.
- **Databases:** every SQLite file is copied with the SQLite online backup API. The snapshot stays consistent under a live writer (test with paired-row transactions).
- **Files:** config, features, workspace, notes and other folders are included.
- **Manifest:** size and SHA-256 of every file, what was left out and why.
- **Never included:**
  - `secrets.env`, `chatgpt/`, `google/`, the link token and the Pear pairing id
  - any file with a credential-like name, or text containing an API key, JWT or refresh token
  - logs, caches and health snapshots
- **Face data:** `faces.db` is biometric, so it is left out unless the user switches `include_faces` on.
- **Verification:** zip CRC, every SHA-256, `PRAGMA integrity_check` on every database, and a check that no credential file is inside.
- **Retention:** default 7 daily + 4 weekly automatic backups, adjustable. Also kept: the last 10 manual backups and the last 5 checkpoints of each kind. Leftover `.partial` files are cleaned up.
- **Restore:**
  - The archive is verified first.
  - It refuses while a Core is running on that data folder (snapshot says running and the link port answers).
  - A `pre-restore` checkpoint is taken, files are staged on the same disk, then swapped in one by one.
  - **Any failure puts the checkpoint back** (fault-injection test: a disk-full error on the third file).
  - Files that are not in the archive are left alone.
  - Afterwards it prints which sign-ins to repeat.
- **Restore drill:** `restore_test()` restores into a throw-away isolated profile, then checks database integrity, row counts and that MemoryStore and Store open.
- **Pre-migration:**
  - `startup_checkpoint()` runs at Core start before the stores open, whenever the version differs from the last run.
  - If it fails, health shows `backup degraded` and it is retried at the next start.
  - `checkpoint()` is for the M6 updater: it raises if the backup cannot be verified.
- **Automatic backup:** a daemon thread does a first check 120 s after start, then hourly; it backs up when the last one is more than 20 h old. Feature switch `backup`.
- **CLI:** `jarvis backup [now|list|verify|test|prune|settings]` and `jarvis restore NAME [--test] [--target DIR] [--yes]`.
- **Dashboard Backup page:** back up now, verify, test restore, retention settings, recovery guidance. A real restore is CLI-only on purpose, because the Core must be stopped.

## Diagnostic bundle (`core/src/jarves/diagbundle.py`, `jarvis diag`)

- **Contents:**
  - `info.json`: which keys are present, never their values
  - `health.json`, `features.json`, `usage.json`, `backups.json` (newest backup verified)
  - `store_summary.json`: counts, open issue titles, warning codes
  - `files.json`: data-folder inventory, names and sizes only
  - `recent.log`: rotated logs included
  - `manifest.json`
- **Size limit:** default 5 MB, set with `--max-mb`. The oldest log lines are cut first.
- **Issue #15 (bundle part):**
  - Every free-form string, including `msg`, provider error messages and nested payloads, is reduced to its length.
  - Only identifier-like values survive: codes, states, model ids, numbers.
  - Non-JSON lines are dropped to their length.
  - A final scan redacts key, JWT and Bearer shapes; e-mail addresses are masked.
  - `cli._private_line` now delegates to this code.
  - The log-file side of #15 (privacy mode on disk) is not part of this package.

## Feature registration (Windows / Pi)

`usage_monitor` and `backup` are registered in `jarves.features` with a `targets` attribute:

| Feature | Windows | Pi |
|---|---|---|
| `usage_monitor` | `implemented_unverified`: unit and HTTP tests on this PC; no real plan-limit event observed yet | `implemented_unverified`: blocker "not run on a Pi", roadmap [#22](https://github.com/igalgerman/JARVIS/issues/22) |
| `backup` | `implemented_unverified`: the user's restore drill on his real data folder is pending | `implemented_unverified`: SD/SSD restore drill on a Pi not done, roadmap [#22](https://github.com/igalgerman/JARVIS/issues/22) |

`features.Feature` has no `targets` field yet. WP-M4's capability matrix should read `getattr(feature, "targets", None)`.

## Evidence

- Tests: `core/tests/test_usage.py` (12) and `core/tests/test_backup.py` (15). They cover:
  - fresh install shows Unknown
  - units never mixed
  - thresholds fire once per window, across restart, highest only, and again in a new day window
  - custom thresholds and validation; user budget gives the estimate a percent
  - plan-limit error is authoritative and clears only on a later successful reply
  - rate headers stored without cookies and never alerting
  - plan-window jitter, a falling percent and a real reset
  - Costs API only with a key, paging, cache, stale on failure
  - the Hebrew voice tool and the feature targets
  - the plan route's headers are observed by `chatgpt_brain`
  - consistent snapshot under a live writer
  - no credentials in archives
  - byte flip, missing member and junk zip are detected
  - isolated restore drill
  - failed restore rolls back; restore refused while the Core runs
  - full disk leaves no half archive
  - retention 7+4 and manual 10
  - auto backup is daily and switchable
  - pre-migration checkpoint and its failure path
  - diag size bound and sanitizing, plus #15
  - dashboard routes over real HTTP and the CLI
- Full suite: `python -m pytest core/tests -q` (from `core/`, `PYTHONPATH=src`): 559 passed, 3 skipped (19 Oct, after merging `swv2-integrate`).
- Headless screenshots (automatic captures, not a hands-on UI check). Reproduce with `python core/tools/usage_screens.py`.
  - [Usage, light](../../reviews/swv2_m5/m5_usage_light.png)
  - [Usage, dark](../../reviews/swv2_m5/m5_usage_dark.png)
  - [Backup](../../reviews/swv2_m5/m5_backup_light.png)
  - [Footer strip on Conversations](swv2_m5/m5_strip_conversations_light.png)
- Paid API spend: **$0**. No real provider calls; the Costs API and plan route were tested with fakes.

## Integration hooks (files owned by other packages, kept minimal)

| File | Change |
|---|---|
| `app.py` (start) | `backup.startup_checkpoint(home)` before `store.attach`. Then `usage.attach(home)`, register `usage.make_skill(...)`, `backup.start_auto(home, health)`. The stop flag is set in `stop()`. |
| `chatgpt_brain.py` | one line: `usage.observe_headers("chatgpt_plan", r.headers)` after the stream opens |
| `config.py` | `SECRET_ENV` also allows `OPENAI_ADMIN_KEY`, the optional read-only Costs API key, from `secrets.env` |
| `dashboard.py` | `PLUGIN_MODULES` plus `jarves.usage` and `jarves.backup` |
| `dashboard_static/index.html` | one `<script src="/static/usage.js">`. The pages register in WP-H's `PAGES`; nav links and the strip are injected by `usage.js`, and `usage.css` is loaded by it. |
| `cli.py` | `add_cli` / `cmd_*` for `backup`, `restore`, `usage`; `cmd_diag` now calls `diagbundle.build` (new `--max-mb`) |
| **Not done, needs WP-C / WP-A** | `live.py` should call `usage.observe_realtime_rate_limits(event["rate_limits"])` on `rate_limits.updated`. `brain.py` (Agents SDK) has no response-header access, so API rate meters fill only once something calls `usage.observe_headers("openai_api", headers)`. |
| **Needs WP-M3** | `jarves.alerts.emit(alert: dict)`. Keys: `kind`, `meter`, `provider`, `threshold`, `percent`, `window`, `category`, `priority`, `dedupe_key`, `title`, `title_he`, `ts`. Until then a log warning is written. |
| **Needs WP-M6** | call `backup.checkpoint(home, "update X -> Y")` before installing an update, and refuse if it raises |

## Found on the way

- `dashboard._EMAIL` (WP-B) has quadratic backtracking on long word runs: 3000 log lines of 500 characters took 11 s. `diagbundle` uses a lookbehind fix (0.6 s). The same one-line fix is suggested for `dashboard.py`.
- `test_camera.py` had 2 failures on base `b219ee1`; they are fixed by the later `swv2-integrate` that this branch merged.

## Open questions for the user

1. **Admin key for the real API bill:** add a read-only `OPENAI_ADMIN_KEY`? Without it the API meter is an estimate covering this install only. Recommendation: yes, if you are the organization owner. It is free and makes no model call.
2. **Monthly API budget:** set one, so the 75/90/100 % alerts work for money? Recommendation: set your platform budget value, e.g. $10.
3. **Face data in backups:** keep `faces.db` out of backups (the current default)? Recommendation: keep it out. It is biometric data, and you can re-enroll faces.
4. **Encrypted backup with secrets:** should a backup ever include keys, encrypted with a password? Not built. Recommendation: no; sign in again after a restore.
