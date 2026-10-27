# SW-V4 B — Sink inventory (every place JARVES writes data)

Branch `v4-b-privacy`, issue #15 (tracking #17). Kept current by package B; other packages add their rows here.

**Classes** (field registry `logs.FIELD_CLASS`): `safe` written as is (secret shapes redacted), `secret` always
masked, `content` masked in privacy mode and stripped from bundles, `pii` masked in privacy mode and hashed
(per-bundle salt) in bundles. Unknown field = `content`.

**Kinds of sink.** *Diagnostic*: logs, history, traces, health, audits — follow `log.privacy`. *Bundle*: meant for
sharing — always stripped. *Functional*: the user's own data that a feature needs (memory, notes, lists, schedules,
workspace, contacts, local backups) — kept in both modes by design, never in a bundle, no secrets.

**Test rows.** `P` = `tests/test_redaction_planted.py`, `D` = `tests/test_diag_bundle_strip.py`,
`A` = `tests/test_redaction_audit.py` / `tools/redaction_audit.py --both-modes` (real Core, both modes),
`L` = `tests/test_log_message_lint.py`.

| # | Sink | Kind | Writer | Rule (privacy ON / OFF) | Secrets | Test |
|---|---|---|---|---|---|---|
| 1 | `logs/jarves.log` | diagnostic | `logs.JsonFormatter` (`record_dict`) | msg = literal template; fields by class; ON: content/pii `<N chars>`; OFF: full content | redacted both modes | P, A, L |
| 2 | `logs/jarves.log.1..N` (rotations) | diagnostic | `RotatingFileHandler` | same as 1 | same | A (rotation forced) |
| 3 | Terminal (`jarvis run`, `jarvis logs`) | diagnostic | `logs.ConsoleFormatter` | same rules as 1 | same | P, A |
| 4 | `store.db` turns / tool_calls / delegations / events | diagnostic | `store.StoreSink` (formats with `JsonFormatter` first) | ON: lengths and timings only; OFF: text | never | P, A |
| 5 | `store.db` issues, selfcheck findings/runs | diagnostic | `diagnose`, `selfcheck` | titles from templates and rules; health text cleaned (`Health.components_view`) | never | A |
| 6 | `store.db` context traces / events | diagnostic | `context_trace.Trace` | ON: sizes + hashes, event data masked by the registry; OFF: text (secrets, e-mails, phones redacted) | never | P, A |
| 7 | `store.db-wal` / free pages | diagnostic | SQLite | as 4–6; `privacy.mask_history` uses `secure_delete` + `VACUUM` + WAL truncate | never | A (raw bytes scanned) |
| 8 | `memory.db` | functional | `MemoryStore` | the user's memories (by design, both modes) | never | A (secrets only) |
| 9 | `health.json`, `jarvis status [--json]`, dashboard health | diagnostic | `Health.snapshot` | detail/action: secrets redacted; ON: e-mails/phones too | redacted | P, A |
| 10 | `runtime.json` | diagnostic | `runtime.service` | states and prefs only | none | A |
| 11 | Crash: unhandled exceptions (main thread, threads, asyncio) | diagnostic | `logs.install_crash_hooks`, `logs.asyncio_handler` | CRASH record: `exc_type` + `exc_frames` (safe), `exc_text` (content); stderr gets the type only | redacted | P, A |
| 12 | `logs/crash.txt` (native crash, faulthandler) | diagnostic | `logs.enable_faulthandler` (`jarvis run`) | frames only (no values, no text); 1 MB then one rotation | none | P, A |
| 13 | `jarvis diag` ZIP | bundle | `diagbundle.build` (+ `cli._private_line`) | ALWAYS: content stripped, pii hashed, secrets dropped, malformed lines dropped and counted, old free-text msg reduced to length, unknown file names pseudonymised, store export without titles | dropped + final leak scan | D, A |
| 14 | Self-check ticket drafts | bundle | `selfcheck.ticket_draft`, `selfcheck._line` | evidence quotes only v2 templates; secrets, e-mails and phones redacted from the whole draft | redacted | P, A |
| 15 | Scheduler reports (`userdata` reports table) | functional | `scheduler` | the result the user asked for (briefing...), kept N per job | never (tool output) | review |
| 16 | `approvals/audit.jsonl` | diagnostic | `approvals.append_audit` | ON: title and detail values masked (registry), prompt length only; shell/install commands stay readable (security record); OFF: text; size-capped rotation | redacted both modes | P, A |
| 17 | Approval popup files (`approvals/popup/<id>.in/out.json`) and argv | functional (transient) | `PopupChannel.open` | the text the user must read; argv carries only file paths; files deleted after the answer | none | review |
| 18 | `tasks.jsonl` | diagnostic | `TaskManager._persist` | ON: description/result/queries/steps masked; OFF: text; size cap | redacted both modes | P, A |
| 19 | Dashboard log view `/api/logs` and download `/export/logs.jsonl` | diagnostic | `logview.present`, `dashboard.clean` | ON now: registry re-applied to every line (old lines too); e-mails always masked; "Privacy: off" banner on every page when off | redacted | P, A |
| 20 | Godot face log (`user://logs/godot.log`) and link messages | diagnostic | `face_app.gd` prints | only check/bench results; the E2E check prints the reply LENGTH; link messages carry display text in memory only | none | review |
| 21 | Third-party loggers (httpx, httpcore, openai, urllib3, websockets, playwright, asyncio, PIL, comtypes) | diagnostic | `logs.capture_third_party` | WARNING+ only, as `LIB_LOG` with `lib_text` (content); URL query tokens redacted | redacted | P, A |
| 22 | `jarvis doctor`, `jarvis status --json` environment | diagnostic | `cli` | key PRESENCE only, never values; `jarvis dashboard` prints its one-time link on purpose (with a warning) | none | A (status) |
| 23 | Telegram / Instagram / Home Assistant logs | diagnostic | their modules (migrated) | fixed messages; API errors as status + code; tokens never in fields; bot token in URLs redacted | redacted | L, A (synthetic call sites) |
| 24 | Browser skill logs | diagnostic | `browser._note` | host and outcome; `host` is content (what the user browsed) | redacted | L, A |
| 25 | Local backups (`backups/*.zip`) and profile export | functional | `backup.create` | copies of the stores; any file with a credential name or ANY token shape is left out | left out | P, A |
| 26 | `workspace/*.md` | functional | `workspace` | the user's assistant files | none | A |
| 27 | CI artifacts | — | `.github/workflows/jarves-checks.yml` | only the installer, its SHA256 and `build-info.json`; no logs or test homes | none | review |
| 28 | Public export (`scripts/make_public_snapshot.py`, branch `v3-public-sync`) | bundle | script | deletes known private docs, rewrites the owner e-mail, scans for keys; see risk R4 in the report | own scan | review |
| 29 | E/F/G/O tables (orchestrator, WhatsApp bridge, recall, self-diagnostics 2.0) | — | not built yet | must log through `logs.*` with classified fields; registry already has `device_store`, `bridge_store`, `session_store` (secret); each new store needs a retention setting and a delete path | — | their packages add rows |

**Retention / delete paths today.** History and context traces: `history.keep_days` (default 90) prune, dashboard
"mask history" / "delete all conversations" (also masks log files, tasks.jsonl and the approvals audit, then
`secure_delete` + `VACUUM`). Logs: rotation (`log.max_bytes`, `log.backups`). Crash file: 1 MB + one rotation.
Tasks and approvals audit: size caps. Self-check runs: `KEEP_RUNS`. Scheduler reports: newest N per job.
Backups: retention policy (`backup.prune`). Profile delete removes the whole profile folder.
