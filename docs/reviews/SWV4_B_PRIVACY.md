# SW-V4 B — Privacy hardening and full redaction audit (issue #15)

Branch `v4-b-privacy` (from `origin/main` 5a98f13). Scope: `docs/JARVES_SW_V4_WORK_PACKAGE.md` section B, testing
6.1 layer 9, agent rows AR-01..AR-05, issue #15. Owner decisions applied: English + Hebrew only; the owner's
real-profile switches untouched (`log.privacy` default unchanged, see OD-12 below); no licence.

## 1. What changed

### 1.1 Structured log messages (#15 fix)
- **`logs.*(component, CODE, msg, **fields)`: `msg` is a fixed developer-written literal.** All 466 call sites of the
  package were migrated (199 f-string / variable messages, plus indirect calls through level variables and the
  `BackendTrace._log` wrapper). Every variable part is a keyword field.
- **Field registry `logs.FIELD_CLASS`** (`safe` / `secret` / `content` / `pii`), one entry per field name. Unknown
  field = `content`; a name containing a credential fragment (`token`, `secret`, `password`, `cookie`, `auth`,
  `api_key`, `credential`) is always `secret`. Nested keys follow the registry under their parent's class.
  Numbers and yes/no flags never carry text (an unregistered counter from `logs.numeric_fields` stays a number).
- **Provider error text** goes to `provider_error` (content) through `logs.error_fields(e)` (type and code are
  safe). `LiveVoice._on_error`, the Realtime error path, `BackendTrace` stream errors / failed responses, the
  ChatGPT plan error and every `str(e)` that used to be interpolated were moved.
- **Records from other loggers** (third-party libraries) are marked non-template: their text is `lib_text`
  (content); their WARNING+ records are routed into `jarves.log` as `LIB_LOG`; `httpx` & co. are held at WARNING
  (INFO would log every URL).
- **Format version** `"v": 2` on every new line: a support bundle keeps the msg of a v2 line (a template) and
  reduces an older line's free-text msg to its length.
- **Exceptions**: `exc_type` + `exc_frames` (safe: `module/file.py:line in func`, no absolute paths, no values) and
  `exc_text` (content) instead of the old one-line `exc` (which could carry private text).
- **Crash output**: `sys.excepthook`, `threading.excepthook` and the Core's asyncio exception handler log a
  `CRASH` record (frames only in privacy mode; stderr gets just the type); `jarvis run` enables `faulthandler`
  into `logs/crash.txt` (frames only; 1 MB then one rotation).
- **AST lint** `tools/log_lint.py` (+ `tests/test_log_message_lint.py`): fails on a non-literal message (f-string,
  `%`, `.format`, concatenation, variable), a field not in the registry, an unknown `**` expansion, a reserved
  keyword (`level`/`component`/`code`, which crashed `AUTONOMY_LEVEL` before), extra positionals; follows
  `level = logs.info if ... else logs.warn` variables and wrapper calls. Pragmas: `log-lint: wrapper` and
  `log-lint: fields=a,b` for the one wrapper. **466 call sites, 0 violations.**
- Consumers that parsed the old free text now read fields (with a fallback for old log lines): `store.py`
  (`model`/`backend_model`, `result_code`, `skill`, `status`), `selfcheck.py` (`result_code`).

### 1.2 Secret patterns (both modes, every string)
OpenAI `sk-` / `sk-proj-` / `sk-admin-`, Anthropic / Claude Code `sk-ant-…`, JWTs (ChatGPT OAuth id/access, Home
Assistant long-lived tokens), Google `ya29.` access, `1//` refresh, `GOCSPX-` client secrets, Telegram bot tokens
(also inside `/bot<token>/` URLs), Instagram / Meta `EAA…` / `IGQV…` / `IGAA…`, GitHub `ghp_…` / `github_pat_…`,
Slack, DPAPI blobs, `Bearer …` (WhatsApp bridge, any API), `Basic …` (only with a digit, so prose like "basic
configuration" is not touched), URL query secrets (`?t=` dashboard link token, `access_token`, `code`, `key`,
`sig`, `state`…), `key=value` / `"key": "value"` credentials, and Windows user-profile paths
(`C:\Users\<name>` → `C:\Users\<user>`, also `/` and JSON-escaped `\\`, and `/home/<name>`). `logs.has_secret`
(token shapes only) is used by backups. Already-masked values (`<N chars>`, `***`) are never re-matched.

### 1.3 Sinks fixed (full inventory: `docs/reviews/SWV4_B_SINKS.md`)
- `jarvis diag`: the **bundle rule** — content stripped, pii hashed with a per-bundle random salt (equal values
  correlate inside one bundle only), secrets dropped, malformed lines dropped and **counted**
  (`log_lines_dropped_malformed`), old free-text msg / `exc` reduced to length, stack-trace text dropped (frames
  kept), the SQLite export (store summary) without issue titles, unknown file names in the inventory
  pseudonymised, a final secret-shape scan over every member. `cli._private_line` returns "" for a malformed line.
- Self-check ticket drafts: evidence quotes only v2 templates; secrets, e-mails and phone numbers redacted from
  the whole draft and title. Self-check findings no longer copy raw health text (an account e-mail).
- Health (`health.json`, `jarvis status`, dashboard): secrets always, e-mails/phones in privacy mode removed.
- Approvals audit: privacy mode masks title and detail values (registry); secrets redacted in both modes;
  shell/install commands stay readable (they are the security record). "Mask history" now covers it too.
- `tasks.jsonl`: privacy mode masks description/result/queries/steps; secrets redacted in both modes.
- Context traces: event `data` masked by the registry in privacy mode, secrets redacted always.
- Dashboard log view and download: the registry is re-applied in privacy mode (old lines included).
- `privacy.mask_logs` / `mask_history`: re-mask old lines with the registry (`logs.private_record`), mask malformed
  lines and old event messages in `store.db`.
- Backups leave out any text file containing a token shape (not only `sk-`/JWT).
- Godot E2E check prints the reply length, not the reply (godot.log file logging).
- The history writer thread prints only the exception type.

### 1.4 Development mode said plainly
`log.privacy=false` keeps conversation text on disk (secrets are masked in both modes). The dashboard top bar shows
**"Privacy: off — conversation text is kept in logs"** (Hebrew: "פרטיות: כבויה — טקסט השיחות נשמר ביומנים") on every
page, from `/api/privacy` (`banner_en` / `banner_he`); `jarvis status` prints the same line in the CLI language,
following the runtime switch.

## 2. Audit table (planted data, real Core, both modes)

`python tools/redaction_audit.py --both-modes` plants 15 secrets, 5 content and 6 pii values (Hebrew with niqqud
included) and searches every output in raw / JSON-escaped / URL-encoded / base64 (3 alignments) / UTF-16LE / NFC /
NFD form. Drive: typed turns over the face link, local tool commands, a tool failing and one crashing with private
text, a provider error from the fake agent (`BackendTrace` error + failed response, `BrainUnavailable`, an
unexpected exception), GPT-Live error/info events, an `httpx`/`openai` warning with a URL token, crashes (main,
thread, asyncio, faulthandler), approvals, a background task, health text, a context trace, **every one of the 466
call sites** with planted values in its content/pii/secret fields (and secrets only in its safe fields), forced log
rotation, dashboard log view + download, `status --json`, a self-check run + ticket draft, a local backup and a
diag bundle. Positive control: in development mode the planted content must reach the log and the history store.

Final run (branch head, 20 Oct 2026): **36 sinks per mode, 466 call sites driven, 0 violations in both modes.**
Counts are planted values found (secret / content / pii). OFF counts > 0 in diagnostic sinks are expected
(development mode keeps the text) and are the positive control.

| Sink | Kind | privacy ON | privacy OFF |
|---|---|---|---|
| `logs/jarves.log` + `.1`..`.4` (rotations) | diagnostic | 0 / 0 / 0 | 0 / 2-4 / 4-5 |
| terminal (`ConsoleFormatter`) | diagnostic | 0 / 0 / 0 | 0 / 4 / 5 |
| `store.db` + `-wal` + `-shm` (history, issues, self-check, context traces) | diagnostic | 0 / 0 / 0 | 0 / 5 / 5 |
| `health.json` | diagnostic | 0 / 0 / 0 | 0 / 0 / 1 |
| `status --json` (Core snapshot) | diagnostic | 0 / 0 / 0 | 0 / 0 / 1 |
| `approvals/audit.jsonl` | diagnostic | 0 / 0 / 0 | 0 / 3 / 5 |
| `tasks.jsonl` | diagnostic | 0 / 0 / 0 | 0 / 2 / 5 |
| `logs/crash.txt` (faulthandler) | diagnostic | 0 / 0 / 0 | 0 / 0 / 0 |
| stderr of a main-thread crash | diagnostic | 0 / 0 / 0 | 0 / 0 / 0 |
| `runtime.json`, `link_token`, `backups/backup_state.json` | diagnostic | 0 / 0 / 0 | 0 / 0 / 0 |
| dashboard `/api/logs` | diagnostic | 0 / 0 / 0 | 0 / 4 / 4 |
| dashboard `/export/logs.jsonl` | diagnostic | 0 / 0 / 0 | 0 / 2 / 3 |
| **`jarvis diag` ZIP** (every member) | bundle | **0 / 0 / 0** | **0 / 0 / 0** |
| **self-check ticket draft** | bundle | **0 / 0 / 0** | **0 / 0 / 0** |
| `memory.db` | functional | 0 / 0 / 2 | 0 / 0 / 2 |
| local backup ZIP | functional | 0 / 2 / 6 | 0 / 12 / 21 |
| `workspace/*.md` (10 files) | functional | 0 / 0 / 0 | 0 / 0 / 0 |

Found and fixed by the audit while building it: self-check findings copied raw health text (the signed-in account
e-mail) into `store.db`; the approvals audit and `tasks.jsonl` kept secrets in development mode; context-trace
event data kept tool arguments in privacy mode; the diag file inventory showed personal file names.

## 3. Test results

All local (CI on the private archive has no minutes), Windows 11, `core/.venv`, one pytest at a time:

- Full offline suite on the final code: **1657 passed, 0 failed, 0 skipped** (2 deselected = platform markers),
  714 s. (First full run: 2 failures in older tests that asserted the pre-B behaviour - curator `facts` absent
  instead of masked, `level` as a safe field - both updated to the new rule; second run green.)
- New tests: `tests/test_log_message_lint.py` 19, `tests/test_redaction_planted.py` 28, `tests/test_diag_bundle_strip.py` 4,
  `tests/test_redaction_audit.py` 2 (system level, about 75 s); `test_tasks.py` +1.
- `python tools/log_lint.py`: 466 call sites, 0 violations. `python tools/redaction_audit.py --both-modes`: 0 violations.
- Updated old tests to the new rules: `test_backup.py` (#15 test: malformed line dropped, unregistered fields are
  content), `test_security.py` (nested registry, `safe_fields` keeps only safe names), `test_tasks.py`
  (development mode keeps text), `test_campaign_voice_fixes.py` (facts masked).

## 4. Acceptance criteria (package B)

| Criterion | Status |
|---|---|
| 0 secret hits in every sink, both modes | PASS (audit, both modes) |
| 0 content/pii hits in every diagnostic sink in privacy mode | PASS |
| 0 content/pii hits in every `jarvis diag` bundle, both modes | PASS (audit + `test_diag_bundle_strip.py`) |
| Encodings: raw, JSON, URL, base64, UTF-16LE, Hebrew NFC/NFD | PASS (`test_the_scanner_finds_every_encoding`) |
| #15 repro 1 (`logs.info` free text) and repro 2 (`_private_line` with `msg`) are regression tests | PASS (`test_issue_15_repro_1_…`, `test_issue_15_repro_2_…`) |
| AST lint covers 100 % of `logs.*` call sites; registry has no unknown fields | PASS (466 sites, 0 violations; module coverage test) |
| Every sink in the inventory has a test row | PASS (`SWV4_B_SINKS.md`; rows 15, 17, 20, 27, 28 are review-only, see risks) |
| Independent Opus review sign-off | PENDING — the reviewer must not be the author (package C / coordinator) |
| #15 closes with the final-commit CI run URL | PENDING — CI on the private repo has no minutes; tests were run locally |

## 5. Remaining risks and open points

- **R1 OD-12 (owner decision, still open):** `log.privacy` default for NEW installs. The code default is unchanged
  (`false`, development); the recommendation is ON for new installs, the owner's profile stays as it is. A one-line
  change in `config.py` / the setup wizard once decided.
- **R2 Over-redaction:** in privacy mode e-mails and 9+-digit phone numbers are also masked inside safe strings;
  a 9+ digit id written as a string could be masked. Codes, ids, timings and frames stay rich.
- **R3 Registry correctness is a review item:** a field registered `safe` is written as is. The audit plants only
  secrets in safe fields; a future call that puts personal text into a safe-named field is caught by review (and,
  in bundles, by the identifier-only rule for safe strings). New fields must be added to `FIELD_CLASS` (the lint
  forces a decision).
- **R4 Public export script** (`scripts/make_public_snapshot.py`, branch `v3-public-sync`, not on `main`): its key
  scan knows only `ghp_`, `github_pat_`, long `sk-`, `AIza` and private-key blocks. Recommendation when that branch
  merges: reuse `jarves.logs._TOKEN_SHAPES` (JWT, `ya29.`, `1//`, `GOCSPX-`, bot and Meta tokens, DPAPI).
- **R5 Functional stores keep user data by design** (memory.db, workspace, scheduler reports, local backups,
  approval popup files while open). They hold no secrets and are never in a bundle; their privacy is the
  delete paths (forget, mask/delete history, profile delete) and retention.
- **R6 Pre-V4 data:** lines written before this change keep their old free-text `msg` on disk until "mask history"
  is run or they rotate out; every reader that shares (diag, ticket, dashboard in privacy mode) already reduces them.
- **R7 E/F/G/O stores do not exist yet:** their packages must use `logs.*` fields, add registry entries
  (`device_store`, `bridge_store`, `session_store` are pre-registered as secret), a retention setting and a delete
  path, and add rows to the sink inventory and the audit drive.
- **R8 Manual rows** OR-01..OR-04 (owner: diag ZIP by eye, privacy switch on/off in the log view, `status --json`)
  are not done in this package (owner evidence).
