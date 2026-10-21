# SW-V4 package O — Self-Diagnostics 2.0 (report)

Branch `v4-o-selfdiag2` (off `origin/v4-final`), issue #17. Feature switch `self_diagnostics_2` (default ON, group
Core). Code in `core/src/jarves/diag/`, page `dashboard_static/health.js`, guide `guides/data/health.toml`,
CLI `jarvis health ...`, tools `core/tools/build_log_index.py`, `core/tools/canary.py`, hook
`core/tools/synthetic_canaries.py`.

## Research (checked 20 Oct 2026)

| Choice | Decision | Source |
|---|---|---|
| AI reviewer model | `gpt-6.1-sol` (OpenAI GPT-6 family; released 29 Sep 2026; successor of `gpt-6-sol`, price $2 / $10 per 1M tokens in `config.PricingConfig`). OpenAI, not Claude: a different model family from the one that wrote the code. Setting `health.ai_review_model`. | https://developers.openai.com/api/docs/guides/reasoning , https://zapier.com/blog/openai-models/ , https://openrouter.ai/~openai |
| API | Responses API (`client.responses.create`, `store=False`, `max_output_tokens`, `reasoning.effort`), the same plumbing as `diagnose.llm_review`; cost lands in `delegations` (kind `review`) | existing JARVES code |
| Anomaly statistics | median + MAD (x1.4826), one-sided CUSUM tuned to a 3x step (k = mu), least-squares slope + r^2; pure Python, no new dependency | standard robust statistics |
| Process stats | ctypes (`GetProcessMemoryInfo`, `GetProcessHandleCount`) on Windows, `/proc/self` on Linux / Pi; no psutil | Win32 docs |
| Root-cause index | AST scan + `git blame --porcelain` at BUILD time (packaging step), never git at runtime | |

The owner decisions OD-33..OD-37 are still open; the plan's recommended answers are the defaults and every one is a
setting (see "Questions for the user").

## What was built (the nine improvements + the owner addition)

1. **Continuous monitor** (`diag/monitor.py`, `diag/metrics.py`): a log sink counts every record per code and
   level per minute and takes only numbers from known codes (reply wait, wake-to-first-audio, dead air, tool `ms`
   (new field on `TOOL_RESULT`), tool failures per conversation, budget hits); a health listener stores component
   state changes; a 60 s sampler records CPU %, RAM, handles, threads, state-folder size, disk free, event-loop lag,
   scheduler lateness and the monitor's own CPU time. Storage `diag_metrics` / `diag_codes` / `diag_events` in
   store.db with `app_version` + `commit`; 1-minute rows 7 days, hourly roll-ups 90 days. Message text and content
   fields are never read.
2. **Baselines + anomalies** (`diag/anomaly.py`): 14-day baselines (hour-of-day buckets for CPU and latencies),
   k=6 MAD for 3 consecutive hours plus relative floors, new ERROR/WARNING code clusters, CUSUM rate steps,
   7-day slopes (RAM creep MB/h, state-folder growth MB/day), slow-turn outliers (>1.5x the baseline p99 and 2x the
   dead-air limit), one tool failing in 3+ conversations, false-action claims, wake false positives above the
   target, canary twice failing, red/amber preflight items, configuration drift. Findings reuse the V3 shape and
   have accept / dismiss / snooze / reopen (`diag_findings`).
3. **Regression between versions** (`diag/regression.py`): first 24 h of a new version vs the previous version's
   last 7 days (new codes, code rate x2, tool failure rate, medians of latencies / CPU / RAM / tool ms x1.25) ->
   a version report and one finding "Since 4.0.1: ..." with the rollback remedy.
4. **Root-cause hints** (`tools/build_log_index.py` -> `diag/log_codes.json`, `diag/logindex.py`): code ->
   file:line, function, last commit, GitHub permalink (public repo), matching guide; 10 surrounding log lines as
   the call's message TEMPLATE plus numeric fields only. The packaging build runs the index step; the file is
   git-ignored (a dev checkout simply has no hints).
5. **Ticket drafts** (`diag/tickets.py`): title, severity, version, commit, code links, counts, redacted evidence;
   filing = approvals (full text in the prompt) -> `gh issue create` on `health.ticket_repo` (default
   `igalgerman/JARVIS`), refused when the repo is not PRIVATE, duplicate fingerprint -> comment.
6. **Remediation only through approvals** (`diag/remediation.py`): the closed list restart_face_link,
   restart_wake_loop, restart_audio_device, restart_dashboard, restart_bridges, clear_speech_cache,
   clear_pear_probe_cache, clear_guide_cache, reconnect_google, fallback_local_voice, fallback_stock_wake,
   rerun_feature_setup, rollback_update. Each: risk medium, dry-run text en/he, pre-check, action, post-check,
   history row. Autonomy grants never skip the question; only restart_face_link can be granted for automatic use
   (`health.auto_face_link_restart`, default OFF until OD-37 is answered).
7. **Health page + spoken summary**: dashboard **Health** (Activity group): score + 30-day trend, component tiles,
   ranked findings (monitor + the latest V3 self-check), version report, preflight, canaries, the AI review card
   (schedule, caps, what it never sees, estimate before / cost after, accept / dismiss / snooze), repairs and their
   history, ticket drafts. Voice tools `health_summary` ("מה מצבך?" / "how are you doing?", 2-3 sentences) and
   `health_ai_review_summary` ("מה מצא הבודק?"). CLI `jarvis health [--json] [--days N]`, `health report`,
   `health remediate <id>`, `health ticket <finding> [--file]`, `health ai-review --every 2d|off|--now|--preview`,
   `health canary`, `health preflight`, `health decide`.
8. **Preflight** (`diag/preflight.py`): config, state folder writable (the only fatal item), disk >= 1 GB, ports,
   Godot, mic + speaker, wake model files + hash, presence of required keys (never values), Google sign-in expiry
   within 48 h, ON-feature prerequisites (time-boxed), schema vs current tables (read-only), last shutdown, clock.
   Hooked into `Core.start()` before the face link opens; a red item degrades only an existing component of the
   Core (never invents one) and raises one popup; amber items are shown on the Health page.
9. **Nightly canaries** (`diag/canary.py`, `tools/canary.py`, scheduler job 03:30 on the owner's PC, nightly CI
   via `synthetic_canaries.py`): voice stack (real Core, virtual mic with the marker "Hey Jarvis", STT -> reply ->
   measured speaker), music (fake player / real Pear read-only, BLOCKED "Pear closed"), calendar read on a COPY of
   the token, dashboard load in real headless Edge / Chromium (Home, Conversations, Health, Settings, 0 console
   errors, budget), installer self-check + payload hashes, bridges (BLOCKED until E/F), scheduler (moved clock).
   Each journey in its own throw-away home.
- **Owner addition: scheduled selective AI review** (`diag/ai_review.py`): daily scheduler job, due every
  `health.ai_review_every_days` (default 2); the rule layer selects only what is new (items seen before are not
  re-raised unless their evidence grows); skipped at $0 when the slice score is below 3; `guard()` on the
  serialized request (secrets, e-mails, phone numbers, long tokens, Hebrew text); excerpts of flagged conversations
  only when the OWNER explicitly stored `self_check.ai_pass` = true (not the default), `diagnosis_llm_pass` on and
  privacy off; per-run cap $0.10 and monthly cap $1 with a true upper-bound estimate (bytes x input price +
  max_output_tokens x output price) and trimming to the highest-ranked items; reservation through the shared
  `Budget`; at most 8 report items; accepting a settings item only creates an approvals proposal; undecided items
  go stale after 7 days.
- **CI**: `jarvis diag --ci --out DIR` (bundle of the TEST home + health summary, scanned; a secret-looking string
  = exit 2 and nothing kept) in `nightly.yml` (suite + audits jobs, retention 7 days) and `jarves-checks.yml`
  (core + e2e failure steps); `nightly.yml` installs Chromium for the dashboard canary.

## Tests (all local, one pytest at a time, 0 skips)

| File | What it proves | Result |
|---|---|---|
| `tests/test_diag_anomaly.py` (+ `tests/diag_replay.py`, `tests/fixtures/diag_replay/planted.json`) | each of the 12 planted anomalies alone gives exactly its finding; all 12 together; 30 boring days -> 0 high, 0 medium, 0 low (also checked with seeds 1-10); state + resolve | 16 passed |
| `tests/test_ai_review_selection.py` | slice precision and recall 100% on the fixture; 15 boring 2-day windows skipped at $0 (client never called); not due / off; cost before and after, no re-raise; planted OpenAI key, Google refresh token, WhatsApp body, contact name + number, calendar title, recalled sentence, Hebrew sentence absent in 8 encodings; owner AI path -> only the flagged excerpt, still no secrets; privacy mode; default != owner choice; cap property test (200 random cases) + monthly cap over 6 worst-case runs; accept -> proposal only, nothing applied, stale after 7 days | 12 passed |
| `tests/test_diag_monitor.py` | counts / turn metrics / tool failures per conversation from real logging; no content stored; component events on change; sampler; overhead budget (<= 0.1% CPU at 100 records/min + 1 sample/min, buffer bounded); roll-up + retention; config snapshot without names; monitor inside a real Core | 6 passed |
| `tests/test_diag_regression.py` | fake update -> version report (tool_ms 1.4x, LIVE_RECONNECT 3x) and finding; no report without update / regression; expiry | 3 passed |
| `tests/test_log_index.py` | 10 planted errors in a real throw-away git repo (two commits) -> exact file:line, function, commit, permalink; detector attaches hints; real source index; context lines carry no text | 4 passed |
| `tests/test_preflight.py` | healthy start green and within 1.5 s; 12 planted faults each on its own item, fatal only for the state folder; a red item degrades only its component; Core hook stores the result before the link opens | 15 passed |
| `tests/test_remediation.py` | every closed-list remedy end-to-end on a real Core through its ApprovalService with its post-check (bridges: pre-check BLOCKED, no ask); deny does nothing; only the face link can be automatic (autonomy grants do not skip); voice tool asks; no channel = denied | 5 passed |
| `tests/test_canary.py` | ci journeys PASS (voice stack, music, calendar, scheduler, installer) and bridges BLOCKED; dashboard in real headless Edge PASS; a deliberately broken page FAILs; two failures -> finding; owner profile reads a token COPY, real home tree unchanged; installer hash mismatch fails | 7 passed |
| `tests/test_health_api.py` | Health API over real HTTP; decide; settings validation; every-N-days -> job renamed / paused (OO-10); on-demand review needs the estimate first; remediation 409 without the Core, deny / approve; ticket: deny = nothing, approve = one issue on the private repo with the full text shown, duplicate -> comment, public repo refused; voice tools on a real Core (en/he); CLI parity; `diag --ci` leak gate; registration on every surface | 10 passed |
| touched suites (`test_cli_parity`, `test_guides`, `test_dashboard_locales`, `test_dashboard_p02`, `test_setup_wizard`, `test_capabilities`, `test_hebrew_texts`, `test_features`, `test_selfcheck`, `test_ci_pipeline`, `test_ci_workflows`, `test_packaging`, `test_cli_english`) | still green with the new feature, page, catalogs, workflows | 201 passed |

Real runs (not pytest):
- `python tools/canary.py --profile ci --browser-channel msedge`: 6 PASS, 1 BLOCKED (bridges); dashboard pages
  1.9 s / 0.9 s / 0.9 s / 1.0 s in headless Edge.
- `jarvis health`, `jarvis health preflight` (446 ms on this PC, within 1.5 s), `jarvis health ai-review --preview`,
  `jarvis diag --ci` on a temp home.
- **Live AI review** (the owner's API key, on a throw-away home filled by the replay harness: synthetic data only):
  13 slice items, estimate $0.031 (cap $0.10), **actual $0.0179** (2050 input / 1384 output tokens, 5.5 KB request),
  model `gpt-6.1-sol`, 8 ranked items that matched the planted problems (false-action grounding, dashboard canary,
  version 4.0.1 regression, calendar tool failures, 15 s waits, memory growth, false wakes, Google expiry).
  Spend: $0.018 of the $1 allowed.

## Not verified / BLOCKED (with reason)

- 24 h soak of the monitor overhead on the owner's PC (needs a running Core for 24 h): the unit budget test only.
- 5 nights of canary PASS on the owner's PC (the owner profile was not run against the REAL home: it would read
  the owner's Google account, an owner gate). Paid GPT-Live voice canary (OD-36) not built beyond the flag.
- Preflight 1.5 s measured on this PC only (446 ms CLI, ~25 ms in a test Core with feature probes skipped); the
  owner-PC number belongs to the M budget table.
- Ticket filed on the real private repo (owner present, OO-09): only against a fake `gh`.
- Remediation on the owner's real Core / devices (OO-04/05): only on a test Core with fakes. `restart_bridges`
  is BLOCKED until E/F add bridges with `restart()`; `rollback_update` needs package L's `updates.rollback`
  (pre-check says so; tested with a stand-in).
- Health page reviewed in headless Edge (canary loads it in en, 0 errors); no Opus UX review or screenshots in
  en/he at 1440/390 (package K).
- Full offline Core suite on the merged head `ab57b87` (v4-final up to a226def: B, C, D, G, M, idle writes, helper
  isolation): **1980 passed, 0 skipped, 0 failed** (21 min, one pytest process); `core/tools/log_lint.py`: 529 call
  sites, 0 violations (new health log fields registered as `safe` in `logs.FIELD_CLASS`).

## Owner questions

1. OD-33: keep the AI review every 2 days? (`jarvis health ai-review --every 3d|off`)
2. OD-34: keep it ON with $0.10/run and $1/month on the API-key route, model `gpt-6.1-sol`?
3. OD-35: file approved tickets on the private `igalgerman/JARVIS` repo (default)?
4. OD-36: paid nightly voice canary (GPT-Live connect/close, <= $0.02/night)? Default no.
5. OD-37: allow "restart the face link" to run automatically? Default OFF here until you say yes
   (`health.auto_face_link_restart`).
6. The V3 default of `self_check.ai_pass` is ON, but you answered "AI pass off by default"; the AI review sends
   conversation excerpts only after you switch it on yourself. Should the V3 default also become OFF?
