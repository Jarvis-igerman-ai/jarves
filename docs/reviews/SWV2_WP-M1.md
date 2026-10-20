# SW-V2 WP-M1: always-on desktop runtime (spec 4.1, 4.2, 6.1 Home)

Issue: #17 (SW-V2). Pi gaps: #20 (part of #18; service delivery #19). Branch of WP-M1, merged by the lead.

## What changed

| Area | Files |
|---|---|
| New runtime package | `core/src/jarves/runtime/` (`__init__.py` features + display Skill + dashboard routes, `service.py` the four states, `viewer.py` Godot window, `tray.py`, `autostart.py`, `lock.py`, `power.py`) |
| Core wiring (small hooks) | `app.py` (attach runtime, `desktop.close` no longer stops the Core, `runtime.set`, mute persists, wake mic stall guard + audio recovery), `link.py` (`runtime.set` for the cli role), `config.py` (`[runtime]`), `dashboard.py` (plugin `jarves.runtime`) |
| CLI | `cli.py`: `jarves run --background [--show]`, `start [--show]`, `show`, `hide`, `autostart on/off/status`, `status --brief`; instance lock for every `run` |
| Launcher | `jarvis.cmd`: `jarvis start`, already-running `jarvis` sends `show`; installs the `tray` extra |
| Face | `godot/face/assistant_ui.gd`: `display.close` closes the window itself (the Core keeps running) |
| Dashboard Home | `dashboard_static/app.js` (`pageHome`, now the default page), `index.html` (Home link), `style.css` |
| Deps | `pyproject.toml` extra `tray = [pystray>=0.19.5,<0.20, pillow>=11]`, `uv.lock` |
| Docs | `core/README.md` (Always on), `core/config.example.toml` (`[runtime]`) |

## Behaviour

- **Background Core:** `jarvis start` spawns `pythonw.exe -m jarves run --background` (detached, breaks away from `uv run`'s job object). No console, no window; tray icon; local wake word.
- **Window close = hide** (feature `background_core`, default on; off restores the old "close = quit"). The Core answers `desktop.closed`, remembers "hidden" and keeps the voice session and wake word running.
- **Show / hide:** voice tools `show_yourself` / `hide_yourself` (feature `display_control`, action permission, pre-authorized like music). Tray, dashboard and CLI use the same `Runtime.show/hide`. At most one owned viewer; a second show while opening/visible is a no-op. Hide sends `display.close` (the real face exits itself with code 0) and terminates after 3 s otherwise. A viewer crash sets display `crashed` + health `display: degraded`; the Core is untouched.
- **Four separate states:** display (shown/hidden/starting/crashed), software mic mute, proactive alerts paused, Core (running/stopping/stopped). They never imply each other (tested). The user's choices persist in `<state_dir>/runtime.json`, so a restart (new login, crash, update) keeps mute and pause; the dashboard/`status` read the saved view truthfully as `stopped` when the Core is down. `runtime.alerts_paused(home)` is the hook for the WP-M3 alerts engine.
- **One Core per user:** OS file lock on `<state_dir>/core.lock` (msvcrt byte lock / fcntl flock; released by the OS on crash). A second `run` exits with code 3 (`RestartPreventExitStatus=3` for systemd).
- **Sleep/resume:** a 5 s local timer detects a wall-clock jump >= `runtime.resume_gap_s` (30 s): it ends a voice session whose connection died asleep (no reconnect loop, no billing), closes the wake stream, re-scans PortAudio devices and re-checks audio.
- **Headset unplug:** PortAudio silently stops calling back; the wake loop used to wait forever. `stall_guard` raises `AUDIO_MIC_STALLED` after `runtime.mic_stall_s` (6 s) without frames; the runtime re-scans devices and the loop re-opens the (new default) mic.
- **Network loss:** unchanged existing path (live session ends with an error, back to the local wake word; `LIVE_RESUME_LIMIT` guards loops).
- **Idle = no billable connection:** verified by test in hidden background mode with cloud voice configured: wake mic active, no live session, no speech, `cloud_connection_open: false`. The background mode never calls `prepare_desktop` (which would open the conversation-mode session).
- **Autostart:** `jarvis autostart on|off` or the dashboard switch. Windows: HKCU `...\CurrentVersion\Run` value `JARVES` = `pythonw.exe -m jarves run --background` (no admin, visible in Settings > Apps > Startup). Linux/Pi: `~/.config/systemd/user/jarves.service` (non-root, `Restart=on-failure`); headless boot needs `loginctl enable-linger`, which JARVES never runs itself. The feature switch is applied to the OS only when the user ever set it.

## Tray library decision (checked 2026-10-19, pypi.org JSON API)

| Option | Version / date | Verdict |
|---|---|---|
| **pystray** | 0.19.5, 2023-09 (LGPLv3) | **Chosen**: small, pure Python (ctypes Win32 / AppIndicator / GTK / Xorg), only needs Pillow 13.0.0 (2026-10-16). Stable de-facto standard; no newer release needed for our use. |
| PySide6 QSystemTrayIcon | 6.12.0, 2026-10-08 | Maintained but ~250 MB Qt and a second GUI loop for one icon: too heavy for the installer and the Pi. |
| infi.systray | 0.1.12.1, 2025-01 | Windows only. |
| tray-manager / trayapp | wrappers | Built on pystray; no gain. |

pystray sits behind `TrayBackend`; menu logic (`menu_entries`) is pure and tested without a tray. Menu: Dashboard (opens the Core's in-process WP-H dashboard), Show/Hide, Mute microphone (software), Pause proactive alerts, Status (notification), Diagnostics (redacted bundle, revealed in Explorer), Quit.

## Tests and evidence

- `core/tests/test_runtime.py` (27 tests, fakes only): window close keeps the Core (and the legacy switch-off path), show/hide tools without a second window, terminate fallback, display switch hides tools, viewer crash, Core stop closes the window, four states independent + persisted across a Core restart, face may not send `runtime.set`, tray menu + tray actions from another thread, real pystray menu built (not run), instance lock in-process and across processes, `cmd_run` exit 3, Windows Run-key and systemd unit adapters, autostart switch sync, resume detection with a fake clock, resume ends a dead session, stall guard, wake loop re-opens a stalled mic, hidden idle opens no cloud voice, desktop mode adopts the window and survives its close, dashboard `/api/runtime` live and stopped, truthful platform status.
- `core/tests/test_desktop_voice.py`: updated regression (closing the window no longer stops the Core or the conversation).
- `core/tests/test_face_protocol_godot.py::test_face_closes_itself_on_display_close`: REAL headless Godot 4.7.2 exits by itself (code 0) after `display.close`.
- `conftest.py`: autouse guard so no test can touch the real Run key / systemd units.
- Full suite: `cd core; uv run pytest tests -q` -> **593 passed, 2 skipped** (the 2 Godot tests skip without `GODOT_EXE`; with it both pass).
- **Real hidden-mode run** (2026-10-19, this PC, isolated `JARVES_HOME`, `ai.provider = offline`, `voice.mode = off`, so no mic/speaker, no network, $0): `jarves start` returned in 12 s; the Core ran as `pythonw.exe` with no window (`MainWindowHandle = 0`) and survived the `uv run` exit; real pystray tray started (`tray shown`); a second `jarves run --background` was refused with exit 3 naming the pid; `start` again reported "already running"; `runtime.set` mute / pause / hide / show worked and stayed independent; `show` with a missing Godot reported the truthful error and the Core stayed up; `jarves stop` ended the process, `runtime.json` says `stopped`, the lock was released; log codes RUNTIME_STARTED, MIC_MUTE_CHANGED, ALERTS_PAUSED, DISPLAY_HIDDEN, VIEWER_START_FAILED, CORE_STOPPED.
- Cost: **$0** (no paid API calls).

## Windows / Pi status (in `runtime.PLATFORM_STATUS`, attached to the features as `.targets`, shown on Home)

| Capability | Windows | Pi (Linux ARM64) |
|---|---|---|
| background_core | verified_on_device (real hidden run above) | implemented_unverified (#20) |
| single_instance | verified_on_device (exit 3 in the real run) | implemented_unverified (#20) |
| display_control | implemented_unverified (real Godot closes itself; spawn hands-on pending) | planned: Godot Pi performance unmeasured (#20) |
| tray_icon | implemented_unverified (started for real; nobody looked at / clicked it) | not_applicable, equivalent: dashboard Home (#20) |
| autostart | implemented_unverified (fake registry; real login not exercised) | implemented_unverified (#20) |
| resume_recovery | implemented_unverified (simulated sleep and unplug) | implemented_unverified (#20) |
| idle_no_cloud | implemented_unverified (contract test; real mic is user QA) | implemented_unverified (#20) |

## Open questions for the user

1. **Close button:** closing the window now only hides JARVES (spec 4.1). OK as default, or should the first close show a one-time notice "JARVES keeps running in the tray"? (Recommendation: add the notice in WP-M2's face work; behaviour stays.)
2. **Autostart:** default off. Should the M6 installer offer "Start JARVES when I log in" checked by default? (Recommendation: offer it checked, the user decides at install.)
3. **Hands-on check (2 min):** `jarvis start`, look at the tray icon, click Show/Hide, Mute, Status, Quit; close the window once and say "Hey Jarvis". This moves tray_icon/display_control to verified_on_device.

## Integration hooks (files owned by others)

- `dashboard.py`: `"jarves.runtime"` added to `PLUGIN_MODULES` (WP-B/WP-H).
- `dashboard_static/app.js`: `pageHome` + router: Home is the default page; other pages still come from `PAGES` (pages.js) first. `index.html` Home link; `style.css` `.grid2.home`. A placeholder `#usageStrip` text says meters are not connected; WP-M5's footer strip is unaffected.
- `link.py`: `runtime.set` allowed for the cli role only.
- `app.py`: runtime attach after DevicePort; `_write_tools_md()` called again after it (TOOLS.md now also lists late-registered camera/display tools); Runtime reuses the Core's `open_dashboard()` (WP-H).
- `godot/face/assistant_ui.gd`: `display.close` handler (WP-M2 appearance work should keep it in every renderer).
- WP-M3: call `jarves.runtime.alerts_paused(home)` before delivering a proactive alert.
- Note: one early test run wrote a fake `JARVES` value into the real HKCU Run key (an empty fake registry was falsy); it was removed at once, the adapter now checks `is not None`, and conftest blocks real autostart in every test.
