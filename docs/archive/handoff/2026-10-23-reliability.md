# Handoff 2026-10-23: reliability pass (#17), branch `fix/reliability`

Evidence came from the owner's Windows PC (`jarvis issues list`, `jarvis diagnose --days 3`). No PR opened. No new
feature, command or switch was added, so no new guide / CLI-parity entry; Hebrew finding texts are inline in
`selfcheck.py` next to their English.

## Root cause per item

1. **"Tool X never returned" (feature_set, open_dashboard).** The diagnosis pairs `LIVE_TOOL_CALL` with `TOOL_RESULT`.
   `SkillRegistry._call` logged `TOOL_RESULT` only when the handler ran. Every refusal (declined / unanswered approval,
   feature off, bad args, untrusted-turn, replay) and every cancellation (session or turn ended while an approval was
   open) left a call "open forever". Fixed: every call now ends in exactly one record: `TOOL_RESULT`, `TOOL_REFUSED`,
   `TOOL_CANCELLED` (or the existing `TOOL_REPLAYED`); `store.py` closes the pending call on any of them (ok stays
   unknown, so no false "failed"). A real hang that was provable: `feature_set` asks for approval inside its handler
   with a 30 s tool limit while the approval waits 60 s, so the limit cut the approval short ("feature_set failed").
   Fixed: `SkillRegistry._bounded` does not count the person's approval time against the tool timeout. Approval waits
   have a hard cap (`ask_cap_s`, 660 s) and the outcome is kept (`last_ask_outcome`): a "no" that is really silence is
   now `TOOL_APPROVAL_TIMEOUT` ("I asked for your approval on the screen but no answer came in time, I did nothing"),
   no screen to ask on is `TOOL_APPROVAL_UNAVAILABLE`, and `feature_set` words both. `TOOL_TIMEOUT` says "nothing was
   changed" only for read tools; for actions it says it cannot confirm. `open_dashboard`: the browser launch
   (`webbrowser.open` in a thread, can stall on Windows) and the server start are now bounded (3 s / 8 s) and the tool
   answers "the dashboard runs, the browser is still opening". NOT reproducible here: which of these was the owner's
   exact trigger; the evidence only shows missing result lines.
2. **dev_suggest failed.** The model's tool schema loses length/enum hints, so it passed `kind="idea"/"bug"`;
   `suggest()` raised ValueError ("kind must be one of") = `DEV_REFUSED`. Now aliased to the two kinds, schema is a
   Literal, the SQLite write runs off the event loop and a DB error is `DEV_STORE_FAILED` instead of `TOOL_CRASH`.
   Not proven: a `looks_sensitive` refusal (Hebrew words like "קוד ", "חוב" match) may also have been involved; left
   unchanged (shared rule).
3. **music_play MUSIC_NOT_CONFIRMED with Pear unreachable.** Offline was already an immediate error on the first HTTP
   call, but "running and not confirming" retried up to 3 candidates, and a player that closed mid-play ended as
   "not confirmed". Now `_require_player` probes first (`PearDesktopPlayer.probe`: running / unresponsive /
   not_running; a frozen app is NOT called "not running"), answers `MUSIC_PLAYER_OFFLINE` with
   `jarvis music open` (`data.fix_command`) at once, re-checks before each retry and before "not confirmed".
4. **Unclean shutdowns (5x).** Provable gaps: no signal/console handling anywhere, so closing the terminal window,
   Windows logoff/shutdown (console events) and SIGTERM ended the process with no `CORE_STOPPED`; `Core.stop()` could
   skip `CORE_STOPPED` if any step raised or hung. Fixed: `runtime/shutdown.py` routes CTRL_CLOSE/LOGOFF/SHUTDOWN
   (holding Windows back up to 4.5 s until the stop finished), Ctrl+C (second press aborts as before) and POSIX
   SIGTERM/SIGHUP/SIGINT into `Core.request_stop(reason)`; `stop()` isolates and bounds every step (10 s) and always
   writes `CORE_STOPPED` (`failed_steps`, `reason`). Stop reasons are logged (tray_quit, voice_quit, cli quit, ...).
   Classification (`selfcheck.end_kind`): `CORE_STARTING` now records the machine boot time; a process with no stop
   whose successor started after a REBOOT is a low "computer restarted / lost power" finding, a begun-but-unfinished
   stop is `stop_hung`, the rest stays "crash". Installer already runs `jarvis stop` first (jarves.iss
   PrepareToInstall). Autostart is a Run key (no relaunch loop). NOT verified: real Windows logoff/shutdown, a GUI
   process without console gets no console events (pythonw background mode relies on tray Quit / `jarvis stop` /
   Windows killing it, which still shows as a crash or, after a reboot, as "restart"). Boot time uses
   GetTickCount64 / `/proc/uptime`; Windows Fast Startup may keep the same tick base across a "shutdown", in which
   case such a stop is still reported as a crash (heuristic, documented).
5. **Slow tools / core_start_ms.** `note_delete 6.6 s` was the person's approval time counted as tool time: the store
   now uses the log's own `ms` (excludes approvals; `approval_ms` is logged separately) and slow-tool checks ignore
   refused calls. `remember` ran a difflib scan + SQLite write on the event loop (froze voice while another
   connection held the DB lock): now `asyncio.to_thread`. `camera_describe 5.5 s` is sensor open + cloud vision; no
   safe cheap win, so `CAMERA_LOOK_TIMING` (capture/encode/ask ms) is logged for the next diagnosis. `core_start_ms`:
   cProfile on this Linux box showed import/compile dominating (cold pyc) so no budget claim; cheap wins taken: the
   `STARTUP_SUMMARY` audio-device enumeration moved off the CORE_READY path, `TOOLS.md`/`FEATURES.md` generated once
   instead of twice. No perf budget was changed. Not measured on the owner's PC.

## Extra item: briefing midnight flake
`test_channels.py::test_briefing_full_with_google_weather_reminders_and_lists` used `time.time()+60`; around local
midnight that left the "today" window. `register_briefing` now accepts an injected `clock` (semantics unchanged); the
test uses a fixed clock, and `test_briefing_reminders_today_window_is_clock_independent` runs at 23:59:30 and 00:00:30
local (Asia/Jerusalem).

## Tests
`core/tests/test_reliability_17.py` (23 tests, offline, fakes). Updated `test_music_qa` (probe GET) and
`test_v5_followups` (unknown kind is mapped). Pre-existing failures also on origin/main in this Linux env:
`test_selfcheck::test_config_sanity_and_feature_gaps`, `::test_trend_against_the_previous_run`.

## Open
- Verify on the owner's PC: logoff/shutdown/close-window now leave `CORE_STOPPED`; `jarvis diagnose` after a few days.
- GUI-only (no console) background mode has no logoff hook; a `WM_ENDSESSION` hook in the tray would close that.
