# SW-V2 security hardening (reviewer defects) — package report

Branch based on `swv2-integrate` (`git merge swv2-integrate` done first). Issue #17.
Scope: skills/generated.py, skills/sandbox_child.py, dashboard_auth.py, dashboard.py, privacy.py, backup.py + tests.
No edits to live.py, scheduler.py, brain.py, updates.py, package.yml.

Each defect below was reproduced, fixed, and covered by a regression test. Full `pytest core/tests` must pass
(main venv + `PYTHONPATH=<worktree>\core\src`).

## (1) P1 — generated-skill sandbox escape via `string.Formatter`
Reproduced: `import string, random` + `string.Formatter().get_field("0._os.system", (random,), {})[0]` passed
`validate()` and returned `os.system` from `run_sandboxed`.
Fix (skills/generated.py):
- Removed `string` from `ALLOWED_IMPORTS` (it is a getattr primitive).
- Added `Formatter` to `FORBIDDEN_NAMES`; added `get_field, get_value, vformat, convert_field, format_field, parse,
  attrgetter, methodcaller, itemgetter` to `FORBIDDEN_ATTRS` (defence in depth; `.format`/`.format_map` were already
  blocked, closing `"{0.__class__}".format(x)`).
- Approval UI (dashboard_static/m2.js) now shows, for any draft/installed skill that ships code,
  "code is NOT OS-sandboxed on Windows: approve only code you understand". `view()` exposes `has_code`.
- Windows OS hardening implemented: the per-call child is placed in a **Job Object** (per-process memory cap 512 MB,
  active-process limit 1, kill-on-close) via `ctypes`, mirroring the Linux rlimits. It is skipped when the Core is
  already inside a job (`IsProcessInJob`), because nesting a job there can have the OS tear the child down — that case
  falls back to the AST/import/builtins layers. Restricted-token / low-integrity child was evaluated and **not**
  implemented (needs a custom CreateProcess token that asyncio's subprocess does not expose; low value now that the
  child has no OS modules, no open/eval and an empty environment) — documented in code and in the Pi roadmap (#21).
Tests: `string_formatter_getfield` and `string_import_alone` added to the malicious-draft matrix; runtime-containment
test now asserts `import string` fails inside the child.

## (7) generated-skill approve() TOCTOU
`approve()` re-read the draft files from disk with `shutil.copy2` after validating/sha-checking them — a concurrent
`propose()` could swap the bytes in that window.
Fix: write the exact validated `md`/`py` strings into the version dir; never re-read the draft.
Test: `test_approve_writes_the_reviewed_bytes_never_re_reads_the_draft` fails if `shutil.copy2` is used in approve.

## (8) _Broker.http_get DNS rebinding
`_public_host()` checked the address, then httpx re-resolved the name and connected (rebinding window).
Fix: `_public_host()` now returns one verified public IP (and refuses if ANY answer is non-public). `http_get`
connects to that pinned IP with the URL host rewritten to the IP, Host header + SNI/cert hostname kept as the real
name, `follow_redirects=False` (a 3xx is returned as text, never re-resolved and fetched).
Tests: `test_public_host_pins_the_checked_ip`, `test_http_get_connects_to_the_resolved_ip_not_a_re_resolution`.

## (2) P1 — dashboard_auth device role was a denylist
Paired LAN phones (device role) could approve/grant/enable generated skills, start the installer, add folder
grants / read files, switch on camera / camera_when_away / gmail_send, edit config.toml, stop Core, delete/restore
data, run backups.
Fix (dashboard_auth.py `allowed()`): inverted to an **allowlist** for the device role — all GETs except a small
read-deny set (`/api/devices`, `/api/audit`, `/api/features/dashboard_lan`, `/api/files`, `/api/backup`), plus only
the explicit low-risk writes (`/api/diagnose`, issue status, memory facts, notes, alert controls, one-conversation
review). Everything else (skills approve/grant/enable, updates install, file grants/read, risky feature switches,
runtime stop, privacy deletes, config edit, backup/restore) is refused by default; those stay the computer's own
loopback job.
Test: `test_device_role_is_an_allowlist_not_a_denylist` checks each denied route returns 403 from a device session,
and that reads + a safe write still work.

## (12) dashboard negative Content-Length hung request threads
`rfile.read(-1)` blocked a worker thread until EOF. Both the authenticated handler and the unauthenticated
`/api/pair` path now reject `n < 0` (and a non-numeric length) with 400, and `/api/pair` also caps at 4096.
Test: `test_negative_content_length_is_rejected_not_hung` (raw socket, 5 s timeout — a regression would hang).

## (10) privacy residue
Fix (privacy.py): mask_history and delete_conversations now set `PRAGMA secure_delete=ON` before the DML; afterwards
`_secure_purge()` drops the `memory_undo` journal (it held removed fact text), runs `VACUUM` and
`wal_checkpoint(TRUNCATE)` (best effort; logs if the Core holds a lock). They also report how many backups still
contain the data and, with `{"prune_backups": true}` (confirmed), delete those archives (masking a store.db snapshot
inside a zip is not attempted — pruning is the honest option).
Test: `test_privacy_purges_the_undo_journal_and_offers_backup_pruning`.

## (14) backup shipped dashboard_devices.json
It holds device token hashes + per-device CSRF secrets. Added to `EXCLUDE_FILES` so it is never archived (and
`verify()` would flag it if present).
Test: `test_paired_device_tokens_are_never_archived`.

## Test run
Full suite: 1018 passed, 3 skipped, 6 failed. The 6 failures (test_capabilities matrix, 3 x test_guides,
test_m3_core reminder, test_workspace_memory setup doc) fail identically on the untouched `swv2-integrate` base
(checked by restoring base `core/src`): the `google_contacts` feature has no guide/capability-matrix entry. Not in
this package's files; owner of features.py / guides should fix.

## Integration hooks
None required — all changes are within owned files. No live.py/scheduler.py/updates.py edits.

## Pi (#21) gaps
- Generated-skill OS sandbox on Linux/Pi (bubblewrap/seccomp) still open; Windows now has a Job Object cap.
- http_get IP pinning uses `getaddrinfo` + httpx; unverified on Pi OS.
