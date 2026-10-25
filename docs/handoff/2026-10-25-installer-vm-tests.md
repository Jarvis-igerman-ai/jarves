# Installer chain: manual VM tests (25 Oct 2026)

The installer chain (#153 graceful stop, #164 health rollback, #165 disk check, #166 restart only what ran) lands one
PR at a time. CI proves what a runner can (compile, silent install, the upgrade and rollback smoke). The cases below need
a desktop, Group Policy or a real bad build, so a person runs them on a **throw-away Windows 11 VM or a throw-away
Windows user**. Never on the owner's PC or the owner's installed JARVES.

Setup for every case (PowerShell 7, in the VM):

```powershell
# the two installers: OLD = the last released one, NEW = the PR's CI artifact "JARVES-Setup-<ver>.exe"
$Old = "C:\vm\JARVES-Setup-OLD.exe"; $New = "C:\vm\JARVES-Setup-NEW.exe"
$App = "$env:LOCALAPPDATA\Programs\JARVES"; $Data = "$env:LOCALAPPDATA\jarves"
& $Old /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="C:\vm\old.log"
& "$App\jarvis.cmd" --version
function Up([string]$log, [string[]]$extra = @()) {
  $p = Start-Process $New -Wait -PassThru -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"C:\vm\$log.log`"") + $extra)
  "setup exit code: $($p.ExitCode)"; Select-String -Path "C:\vm\$log.log" -Pattern "Stopping JARVES|retain|rollback|Health|Disk" | % Line
}
```

Reset between cases: `& "$App\unins000.exe" /VERYSILENT; & $Old /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` (data is kept).

## PR 1: `fix/installer-graceful-stop` (#153)

| # | Case | Commands | Pass when |
|---|---|---|---|
| 1a | Explorer + Defender lock | Defender real-time ON. `explorer "$App\python"` (leave the window open), `& "$App\jarvis.cmd" run --background`, then `Up "1a" @("/RESTARTJARVES=1")` | exit 0, log shows `the Core has closed (lock free after N s)` with N < 15 and no `hard-stopped: pythonw`; new version runs (`jarvis --version`) |
| 1b | Group Policy execution policy | Admin: `New-Item HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell -Force; Set-ItemProperty HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell EnableScripts 1; Set-ItemProperty HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell ExecutionPolicy AllSigned`. Check `Get-ExecutionPolicy -List` shows MachinePolicy AllSigned. Start a stray `& "$App\python\python.exe" -c "import time; time.sleep(900)"` in another window, then `Up "1b"` | exit 0; log has `hard-stopped: python (pid ...)`. (With the old `-File` code: no `Stopping JARVES` lines and `Cannot retain previous program`.) Remove the policy keys afterwards |
| 1c | Shared program folder | Uninstall. Install OLD with `/DIR="C:\Tools"` (a folder that also holds another program, e.g. copy `notepad.exe` to `C:\Tools\notepad.exe` and start it). Upgrade with `Up "1c" @("/DIR=`"C:\Tools`"")` | the copied notepad is still running; JARVES upgraded |
| 1d | Two setups at once | Start `Up "1d-a"` and, within 2 s, in a second window `Up "1d-b"` | the second setup waits or ends without touching anything (its log has no `prepare`/`Retaining` lines); the first completes; `$App\rollback` holds exactly one previous program |
| 1e | Windowed process | `& "$App\face\JARVES Face.exe"` (or `jarvis dashboard`), then `Up "1e" @("/RESTARTJARVES=1")` | log shows `asked to close: ...` before any `hard-stopped:` for that pid |
| 1f | Put-back that fails, then recovers | Lock a file in face (another window): `$h=[IO.File]::Open("$App\face\lock.txt",'OpenOrCreate','ReadWrite','None')`. `Up "1f"` | exit non-zero, `Cannot retain previous program: face`, `$App\python\python.exe` back in place, `$App\rollback\python` absent. Close the lock (`$h.Dispose()`), `Up "1f2"` succeeds |

## PR 3: `feat/upgrade-health-rollback-164` (to run when that PR is up)

Build the bad candidates from the PR branch with `pwsh packaging\build_windows.ps1 -Version 9.9.9.1 -SmokeBuild` after the edit shown, on a scratch branch that is never pushed.

| # | Case | How to make the candidate | Pass when |
|---|---|---|---|
| 3a | never ready | in `core/src/jarves/runtime/` make the Core exit with code 2 just before it logs CORE_READY | `Up "3a" @("/RESTARTJARVES=1", "/HEALTHTIMEOUT=60")`: exit 0, log `Health check failed`, old version back (`jarvis --version`), old Core logs CORE_READY, `$Data\updates\health.json` has the notice, `$App\failed-upgrade-health-*` holds the candidate |
| 3b | broken python | after the build, delete `dist\stage\python\python3*.dll` and recompile only the installer (`-Phase compiler`) | same as 3a; the health check ran with `{app}\rollback\python` (log line) |
| 3c | schema bump | add a no-op migration that raises `LATEST` by 1, plus the 3a "never ready" edit | the program is NOT swapped back; `health.json` status `health_failed`; the next start offers the approval-gated `jarvis update --rollback` (data + program); after approving, the old version starts and every seeded row is there |
| 3d | stale pending.json | before the upgrade write `$Data\updates\pending.json` = `{"to": "1.2.3", "state": "installing"}` | healthy candidate is NOT rolled back (`--expect` mismatch = unknown) |

## PR 4: `feat/upgrade-disk-check-165` (optional)

`JARVES_HOME` on a second, nearly full VHD (`fsutil file createnew D:\fill.bin <bytes>` until < 200 MB free), then `Up "4a"`: setup refuses before the backup with the EN/HE low-disk message, nothing moved, JARVES still running if it was.

## PR 5: `feat/upgrade-restart-166` (to run when that PR is up)

For each start set, run it, then trigger the in-app update (`jarvis update install`, which starts setup with `/RESTARTJARVES=1`), wait for setup to end, then list what runs: `Get-Process | ? { $_.Path -like "$App\*" } | select Name, Path`.

| # | Before the update | Pass when (after) |
|---|---|---|
| 5a | nothing running (`jarvis stop`; no face, no dashboard) | nothing under `$App` runs; the setup log says the health check was skipped (nothing to check) |
| 5b | Core only: `jarvis run --background` | Core only (pythonw), CORE_READY in `$Data\logs\jarves.log` after the update |
| 5c | Core + face: `jarvis run --background; & "$App\face\JARVES Face.exe"` | Core and face |
| 5d | dashboard with LAN: `jarvis dashboard --lan` | dashboard comes back with `--lan` (check `$Data\dashboard.run.json` and the URL); note: a non-default `--port` is not kept (documented) |

Report each case as PASS/FAIL with the setup log and `$Data\logs\jarves.log` attached to the PR.
