# Installed-product check: #343 and #466 (JAR-380)

Date: 16 Nov 2026. Target: owner PC, installed JARVES 4.0.1.2860
(commit f05e671f, built 2026-11-15T18:55Z, channel main). Read-only checks;
nothing was installed, stopped or restarted for this report.

Code fixes were already on main: #383 (restart what ran before) and #522
(bridge path), with regression tests in `core/tests/test_installer_clean.py`,
`core/tests/test_hotfix_bridge_restart.py` and `core/tests/test_hotfix_slice2.py`.
No production code changes in this PR.

## #343: silent install restarts Core

Inno Setup logs in `%TEMP%`, three consecutive silent installs, each with
`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /RESTARTJARVES=1`:

| Log | Build | Line |
|---|---|---|
| Setup Log 2026-11-14 #002 | 4.0.1.2807 | `restart: Core was running -> restarted` |
| Setup Log 2026-11-15 #001 | 4.0.1.2839 | `restart: Core was running -> restarted` |
| Setup Log 2026-11-16 #001 | 4.0.1.2860 | `restart: Core was running -> restarted` |

Cross-check for the 16 Nov install (setup 01:06:24 to 01:09:35 local time):
Core `pythonw.exe` (pid 29944) was created at 01:09:10, from the install folder,
with no manual start. `jarvis status`: Core running, `core: ready`.

## #466: Core runs the pinned bridge exe

- `packaging/bridge_pin.json` sha256 `12e04d92b70d...0b86`, version 0.1.0.
- `<install>\bridge\whatsapp_bridge.exe` sha256 `12e04d92b70d...0b86` (equal).
- Running process `whatsapp_bridge.exe` (pid 8476, started 01:10:08) image path
  `<install>\bridge\whatsapp_bridge.exe`, so the running bridge is the pinned
  payload, not the older `<home>\bridge` copy seen on 26 Oct.
- `jarvis status` component `whatsapp_bridge`: ok, "connected, mode read".

## Not checked

The `<home>\bridge` fallback order is covered by tests with fake trees only.
