# SW-V4 R-01 defence: signed approvals, hidden codes, OS limits for helper commands

Issue #17. Branch `v4-r01-defense`, made from `origin/v4-final`. This answers residual risk R-01 in
[SWV4_C_REVIEW.md](SWV4_C_REVIEW.md), with three pieces of work: 1 (signed answers), 2 (Job Object) and
5 (status row).

**Short version.** Three paths are now closed:

- `pending.json` no longer holds the typed code.
- A file in `approvals/answers/` counts only when it carries a signature from this run's key.
- The popup's result file counts only when it carries the window's one-time nonce.

Every approved helper command now runs inside OS limits: a Windows Job Object, or POSIX rlimits plus a process
group of its own. Generated-Skill children get the same. The real state of these limits is shown as
`helper_isolation`.

**What this does not do.** It is not a file sandbox. An approved command still runs as the owner and can read
any file the owner can read, the key file included. The full fix is still a restricted token or an AppContainer
(see "Not done" below).

## Piece 1: signed answers and hidden typed codes (`approval_keys.py`, `approvals.py`, `approval_popup.py`)

| What | How |
|---|---|
| Key | 32 random bytes in `<home>/approvals/answer.key`. **Rotated on every Core start** (`ApprovalService.rotate_key`, called from `Core.start`). Other services, such as the dashboard fallback in `ask_owner` and tests, use the existing key and create one only if it is missing. The Core keeps its key in memory and never re-reads the file. A key file swapped in later is therefore not trusted. |
| Key file permissions | Windows: a **protected DACL with one ACE**, `A;;FA;;;<current user SID>`. Nothing is inherited, and Administrators and SYSTEM have no entry (an admin can still take ownership). The DACL is set on the temp file *before* the key is written, then the file is moved into place (the move keeps the DACL). POSIX: the file is 0600 and its folder 0700. |
| Never in env or logs | The key is not passed to any child: `helper.child_env()` drops `*KEY*`, and the Skill child gets an empty environment. No log line, audit record or `pending.json` contains it. Backups leave out `answer.key` (`backup.EXCLUDE_FILES`). |
| Answer files | The dashboard or CLI in another process (`submit_answer`) writes `{id, approved, typed, channel, ts, nonce, sig}`, where `sig` = HMAC-SHA256(key, canonical JSON). The Core's poller drops a file that is unsigned, has a bad signature, does not match its own file name, is stale (older than 600 s + 60 s) or replays a nonce. It logs this once per reason, at INFO, with no content (`APPROVAL_FILE_IGNORED`). |
| Stale or unsigned files from older versions | `rotate_key` deletes every `answers/*.json` and rewrites `pending.json` with no plain code. A `typed_code` left in an older `pending.json` is dropped on read and never shown or trusted. |
| Typed code at rest | `pending.json` holds `typed_salt`, `typed_hash` = HMAC(key, "code\|salt\|code") and `typed_sealed` = the code XORed with HMAC(key, "seal\|salt"). The owner's dashboard and CLI, which can read the key, unseal it to show it, and the result must match the hash. A tampered row shows no code and approves nothing. The Core checks a typed code by comparing hashes in constant time. Because the hash is keyed, 9 000 candidates cannot be tried offline without the key. |
| Popup | The window now gets the typed code and a 128-bit nonce through its **stdin pipe** (`--stdin`), not through `popup/<id>.in.json`. Its result file counts only if it echoes the nonce. |
| Flows kept | Dashboard (in-process and other-process), `jarvis approvals list / approve --code / deny / history`, the popup, the face, and voice. The voice and face rules are unchanged. |

## Piece 2: OS limits (`isolation.py`, `isolation_exec.py`, `skills/helper.py`, `skills/generated.py`)

There is one interface, `isolation.popen()` / `isolation.exec_async()`. Each returns `(process, Guard)`, and
the guard has `state`, `reason`, `kill_tree()` and `close()`.

**Windows, per command.** The child is created with `CREATE_SUSPENDED`, assigned to a new Job Object and then
resumed with `NtResumeProcess`. No instruction runs outside the job. If the job cannot be created or assigned,
the child is resumed anyway: the command runs as before, and the state is set to `unavailable`.

| Limit | Helper default (config `[helper]`) | Generated Skill child |
|---|---|---|
| `KILL_ON_JOB_CLOSE` | yes | yes |
| Active processes | 64 (`max_processes`) | 1 |
| Job memory | 2048 MB (`memory_mb`) | 512 MB (and 512 MB per process) |
| User CPU time (job) | none (`cpu_seconds = 0`; the wall-clock timeout applies) | 10 s |
| Priority | below normal | below normal |
| `DIE_ON_UNHANDLED_EXCEPTION` | yes | yes |
| Breakaway | **not allowed** (`BREAKAWAY_OK` and `SILENT_BREAKAWAY_OK` are not set, so `CREATE_BREAKAWAY_FROM_JOB` fails) | same |
| UI restrictions | desktop, clipboard read and write, global atoms, ExitWindows, display settings, system parameters | the same, plus USER handles |

Cleanup:

- **Timeout:** `TerminateJobObject`, which ends the whole tree. taskkill is used only as a fallback.
- **End of every run:** the job handle is closed, and kill-on-close ends anything the command left running. If
  the Core crashes, the OS closes the handle the same way.
- The Skill child now starts from the base interpreter (`sys._base_executable`). A venv's `python.exe` is a
  launcher that starts a second process, which the one-process limit would refuse.

**Linux / macOS.** Helper commands go through `isolation_exec.py`, a stdlib-only launcher that sets limits and
then calls `exec`. It runs in a new session, so a timeout or close kills the whole process group. The limits:

- `RLIMIT_DATA` = `memory_mb`, per process. Data and heap are counted; PROT_NONE reservations are not, so
  Node / V8 keep working.
- `RLIMIT_CPU` = `cpu_seconds`, per process, when it is set.
- `RLIMIT_NPROC` = the user's current process count + `max_processes` (Linux).
- `RLIMIT_CORE` = 0, plus `nice +5`.

The Skill child keeps its own stricter rlimits (`sandbox_child.py`) and gets only the session from this layer.

## Piece 5: `helper_isolation` status

- **States:** `active` (shown as health `ok`, with "active: …"), `degraded` (some limits failed, for example
  the UI restrictions or one rlimit) and `unavailable` (no job, the child could not be assigned, or no rlimits).
  The last real run wins over the start-up probe. The probe creates a job with the limits and closes it, or runs
  the launcher's `--probe`.
- **Where it is shown:**
  - the Core health row (updated live after every run);
  - `jarvis doctor` (`[ OK ] helper isolation: active (Windows Job Object, …)`);
  - `jarvis features` (`helper_isolation ACTIVE status …`);
  - the dashboard Features page (a card under the matrix) and the Skills & tools "Component health" table (also
    without a running Core);
  - the self-check (a finding whenever the state is not active).
- **Stronger confirmation:** when the state is `unavailable`, every helper command and install needs the typed
  code (risk high, no voice). The approval details always show the isolation line.

## Tests (`core/tests/test_r01_defense.py`)

The run on Windows (this PC): **20 passed, 0 skipped**, plus 4 `linux_only` tests that are deselected here. All
tests use harmless child programs (print, allocate, sleep), no network, and event-driven waits.

**Piece 1:**

- `pending.json` has no plain code, but the owner's dashboard and CLI still show and accept it.
- A tampered `pending.json` (other salt, an unkeyed hash, or an old plain `typed_code`) shows no code and
  approves nothing.
- These answer files are ignored and each reason is logged once: unsigned, other key, edited after signing,
  stale, wrong file name, and a replayed nonce. The legitimate submit still works.
- A rotation drops stale files, and an answer signed with the previous key does not count.
- Windows DACL = exactly one ACE for this user, protected. Linux: 0600 / 0700.
- The key is in no env, `child_env` or `pending.json`.
- The CLI `list` and `approve --code` work end to end.
- A popup result without the nonce is ignored; with the nonce it approves. The code and nonce go through stdin,
  never the file.

**Piece 2 (Windows):**

- The OS reports the requested limits: kill-on-close, process count, job memory, priority, CPU time, no
  breakaway flags, every UI flag, and the child never ran outside the job.
- A 400 MB allocation under a 128 MB job gets `MemoryError`, while 16 MB is fine.
- A second process under `max_processes=1` is refused, while it is allowed under 4.
- Kill-on-close ends a sleeping grandchild that the command left behind.
- The helper timeout ends the whole tree.
- The Skill child runs in a one-process job with USER-handle restrictions, and a real `run_sandboxed` call still
  returns its result.

**Piece 2 (Linux, CI):**

- The launcher sets `RLIMIT_DATA`, a new session and a finite `RLIMIT_NPROC`.
- An allocation over the limit gets `MemoryError`.
- Closing the guard ends the process group.

**Degradation and Piece 5:**

- With no job, the command still runs. The state is `unavailable` with its reason, listeners are told, and the
  health status is unavailable.
- The helper asks for the typed code only when the state is unavailable.
- The running Core's row matches `isolation.status()` and follows a run that degrades and then recovers.
- `jarvis features`, `/api/features`, `/api/skills` and the self-check all show the row.

**Related suites passed after the change:**

- `test_helper_approvals`, `test_review_c`, `test_skill_creation`, `test_approval_popup_ux`
- `test_log_message_lint` (0 violations)
- `test_dashboard_locales`, `test_dashboard`, `test_dashboard_p02`
- `test_core`, `test_cli_english`, `test_cli_parity`
- `test_selfcheck`, `test_features`, `test_campaign_features`, `test_m3_sandbox`
- `test_backup`, `test_diag_bundle_strip`

The full suite and CI have not been run here: RAM is limited, so the suites were run one at a time, focused on
this change.

## What is enforced on Windows, and what is not

**Enforced:**

- every approved helper command and every generated-Skill call runs from its first instruction in a Job Object;
- the process count, memory, priority and (for Skills) CPU time limits;
- kill-on-close, so nothing outlives the run, a timeout or a Core crash;
- no breakaway;
- the UI restrictions;
- signed answer files;
- no plain code in `pending.json` or the popup request file;
- a nonce on the popup result;
- an owner-only DACL on the key;
- the typed code whenever isolation is unavailable.

**Not enforced (honest limits):**

1. **File and registry access.** A job does not restrict files. An approved command (or a program it starts)
   runs as the owner and **can read `answer.key`**, the same way it can read `secrets.env`. With the key and
   `pending.json` it could compute the code and sign an answer. So R-01 is **narrowed, not closed**. The bar is
   now: build the data path at run time, read a second file that rotates on every start, and implement the HMAC
   format. Writing a file is not enough any more, and a reader of `pending.json` alone learns nothing.
2. **Network.** No limits.
3. **Behaviour change.** Programs that a helper command starts in the background end when the command ends (for
   example `start notepad`, or a dev server). This is kill-on-close working as intended. To keep a program
   running, the owner starts it himself.
4. **POSIX:**
   - `RLIMIT_DATA` and `RLIMIT_CPU` apply per process, not to the whole tree.
   - `RLIMIT_NPROC` counts all of the user's processes, so it is set to the current count + N, and it is not
     available on macOS (shown as `degraded`).
   - A process that calls `setsid` leaves the process group.
5. An administrator can take ownership of the key file. The Core never runs elevated for the helper (it already
   refuses).

## Not done (needs a decision; proposed for package L/O)

- **Windows:** start helper commands with a restricted token. The options are `CreateRestrictedToken` with a
  deny-only SID, or an AppContainer / LPAC with a capability-less profile. Grant access to the profile folder
  only through a deny ACE for that token's restricting SID. Then the child cannot open `<home>` (key, secrets,
  answers) at all. This needs `CreateProcessAsUserW` / `STARTUPINFOEX` instead of `subprocess.Popen`, and some
  helper commands (winget, npm into user folders) must be re-tested under it.
- **Linux:** a separate uid or a mount namespace (bubblewrap / landlock) that hides `<home>`. Landlock (Linux ≥
  5.13) can do it without root.
- Another option: answer through the Core's authenticated WebSocket instead of files. This does not help against
  a same-user reader of the token, so the token approach above is the real fix.
