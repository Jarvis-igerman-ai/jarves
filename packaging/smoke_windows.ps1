<#
Silent install smoke test of a built JARVES installer (CI: jarves-checks `installer` job; also runs locally).
Run on a disposable Windows runner: program/data use temp folders, but HKCU, PATH and Start menu are real.
Parallel scenarios MUST use separate Windows users/VMs; do not run them together on a personal PC. No audio is played, no network service is called, no key is needed.

  1 clean install (/VERYSILENT, per user, no tasks)      -> files, Start-menu entry, uninstaller present
  2 jarvis --version / status / features / doctor         -> build identity, "NOT running", update_check listed
  2b jarvis setup --check-addons                          -> every add-on's code, models and data are installed
  3 offline Core start + orderly stop through the link    -> CORE_READY / CORE_STOPPED in the log
  4 exported face: loading screen reaches the Core; room + face self-checks (headless) -> "... RESULT: PASS"
  5 upgrade: same installer again over the install         -> still runs; /SETUPANSWERS= applies wizard answers;
                                                             setup --defaults re-run keeps them (idempotent)
  6 uninstall (silent)                                     -> program folder gone, user data KEPT
  7 install + uninstall /PURGE=1                           -> user data removed only when asked

SW-V4 L (clean machine, upgrade, rollback, budgets). Every run also does a residue audit (packaging\install_audit.py:
HKCU Run key, user PATH, scheduled tasks, Start menu, program and data folders before the install, after it and after
each uninstall; autostart must be OFF after a default install, nothing may be left behind, data is kept byte-for-byte unless
/PURGE=1) and times the silent steps (-MetricsOut writes them for packaging\installer_metrics.py / perf_gate.py).
  -DefaultTasks         step 1 installs WITHOUT /TASKS (the real defaults: PATH task on, autostart OFF) - for a CI runner
  -UpgradeFrom EXE      instead of steps 1-7: install the OLD installer (0.1.0.x or 3.x), seed an old-layout data folder,
                        silently upgrade to -Installer, start the Core (data migration, backup first), verify every seeded
                        row, settings and the autostart state are unchanged and doctor completes
  -Rollback             with -UpgradeFrom: record the previous version like the in-app updater does, run
                        `jarvis update --rollback --yes`, wait for the old version, verify version and data are back

  pwsh packaging\smoke_windows.ps1 -Installer dist\JARVES-Setup-0.1.0.57.exe [-Version 0.1.0.57] [-RequireFace]
#>
param(
    [Parameter(Mandatory)] [string]$Installer,
    [string]$Version = "",
    [string]$Work = (Join-Path ([IO.Path]::GetTempPath()) "jarves-smoke-$PID"),
    [ValidateSet("full", "functional", "autostart-purge")] [string]$Scenario = "full",
    [switch]$RequireFace,
    [switch]$CheckAutostart,     # CI: the reinstall ticks "start at login" (writes HKCU Run\JARVES); uninstall removes it
    [switch]$SkipDoctor,         # local runs: doctor records 1.5 s from the default microphone (CI has none)
    [switch]$DefaultTasks,       # step 1 without /TASKS: the installer's real defaults (see above)
    [string]$UpgradeFrom = "",   # an older installer: run the upgrade scenario instead of steps 1-7
    [switch]$Rollback,           # with -UpgradeFrom: also test `jarvis update --rollback`
    [string]$MetricsOut = "",    # write the silent install / uninstall / upgrade / rollback timings (seconds) as JSON
    [string]$AuditPython = ""    # python for packaging\install_audit.py / seed_legacy.py (default: python on PATH)
)
$ErrorActionPreference = "Stop"
$Installer = (Resolve-Path $Installer).Path
$App = Join-Path $Work "app"
$Home_ = Join-Path $Work "home"
$env:JARVES_HOME = $Home_                   # data dir of this test only (installer + Core + uninstaller read it)
$env:OPENAI_API_KEY = ""
$failures = 0
function Check([bool]$ok, [string]$what) {
    if ($ok) { Write-Host "[ OK ] $what" } else { Write-Host "[FAIL] $what"; $script:failures++ }
}
function Jarvis([string[]]$argv, [int]$timeoutS = 120) {
    $out = Join-Path $Work "out.txt"
    $p = Start-Process "cmd.exe" -ArgumentList (@("/c", "`"`"$App\jarvis.cmd`" $($argv -join ' ') > `"$out`" 2>&1`"")) `
        -NoNewWindow -PassThru
    if (-not $p.WaitForExit($timeoutS * 1000)) { $p.Kill($true); return @{ code = -1; text = "TIMEOUT" } }
    return @{ code = $p.ExitCode; text = (Get-Content $out -Raw -ErrorAction SilentlyContinue) + "" }
}
$Times = [ordered]@{}
$InstallStages = [ordered]@{}
function Install([string]$tasks = "", [string[]]$extra = @(), [string]$exe = "", [string]$time = "") {
    # $tasks = "DEFAULT": no /TASKS at all (the installer's own defaults); "" : no task at all
    if (-not $exe) { $exe = $Installer }
    $taskArg = if ($tasks -eq "DEFAULT") { @() } else { @("/TASKS=`"$tasks`"") }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    # Wait for SETUP only: `Start-Process -Wait` waits for the whole process tree, and a setup started with
    # /RESTARTJARVES=1 starts the Core (nowait) as its child, so -Wait never returned (the job hit its timeout)
    $p = Start-Process $exe -WindowStyle Hidden -PassThru -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/SP-") + $taskArg + @("/DIR=`"$App`"", "/LOG=`"$(Join-Path $Work "install-$(Get-Random).log")`"") + $extra)
    $null = $p.Handle                                   # keep the handle so ExitCode is readable after the exit
    if (-not $p.WaitForExit(600000)) { Write-Host "       setup did not finish within 10 minutes"; $p.Kill($true); return -1 }
    if ($time) {
        $Times[$time] = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        $stateFile = Join-Path $App "install-state.json"
        if (Test-Path $stateFile) {
            $installState = Get-Content $stateFile -Raw | ConvertFrom-Json
            $InstallStages[$time] = $installState.stage_timings_s
        }
    }
    return $p.ExitCode
}
function Uninstall([string[]]$extra = @(), [string]$time = "") {
    $u = Join-Path $App "unins000.exe"
    if (-not (Test-Path $u)) { return -2 }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process $u -WindowStyle Hidden -Wait -PassThru -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") + $extra)
    for ($i = 0; $i -lt 120 -and (Test-Path $u); $i++) { Start-Sleep -Milliseconds 500 }   # the copy in %TEMP% finishes
    if ($time) { $Times[$time] = [math]::Round($sw.Elapsed.TotalSeconds, 1) }
    return $p.ExitCode
}
# residue audit helpers (stdlib python; reads only the HKCU Run key, user PATH, task names, Start menu and the folders)
$AuditPy = if ($AuditPython) { $AuditPython } else { (Get-Command python -ErrorAction Stop).Source }
$Tools = $PSScriptRoot
function Collect([string]$name) {
    $f = Join-Path $Work "state-$name.json"
    & $AuditPy -I (Join-Path $Tools "install_audit.py") collect --app $App --home $Home_ --out $f
    return $f
}
function Audit([string]$mode, [string]$before, [string]$after, [string[]]$extra = @()) {
    $o = & $AuditPy -I (Join-Path $Tools "install_audit.py") $mode --before $before --after $after @extra 2>&1
    $o | ForEach-Object { Write-Host "       $_" }
    return ($LASTEXITCODE -eq 0)
}
function Seed([string]$cmd, [string[]]$extra = @()) {
    $o = & $AuditPy -I (Join-Path $Tools "seed_legacy.py") $cmd --home $Home_ @extra 2>&1
    $o | ForEach-Object { Write-Host "       $_" }
    return ($LASTEXITCODE -eq 0)
}
function Version-Of() {
    $f = Join-Path $App "python\Lib\site-packages\jarves\_build_info.json"
    if (-not (Test-Path $f)) { return "" }
    return ((Get-Content $f -Raw | ConvertFrom-Json).version) + ""
}
function Save-Times() {
    if ($MetricsOut) {
        $args_ = @("-I", (Join-Path $Tools "installer_metrics.py"), "measure", "--dist", (Split-Path $Installer -Parent), "--out", $MetricsOut)
        $map = @{ silent_install_s = "--install-s"; silent_uninstall_s = "--uninstall-s"; silent_upgrade_s = "--upgrade-s"; silent_rollback_s = "--rollback-s" }
        foreach ($k in $map.Keys) { if ($Times.Contains($k)) { $args_ += @($map[$k], $Times[$k]) } }
        & $AuditPy @args_ | Out-Host
        $report = Get-Content $MetricsOut -Raw | ConvertFrom-Json -AsHashtable
        $report["smoke_stages_s"] = $Times
        $report["install_stages_s"] = $InstallStages
        $report | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8NoBOM $MetricsOut
    }
}
function Wait-Log([string]$pattern, [int]$timeoutS = 60) {
    $log = Join-Path $Home_ "logs\jarves.log"
    for ($i = 0; $i -lt $timeoutS * 2; $i++) {
        if ((Test-Path $log) -and (Select-String -Path $log -Pattern $pattern -Quiet)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

New-Item -ItemType Directory -Force $Work, $Home_ | Out-Null
# Offline Core on a private port: no voice, no music, no AI.
@"
[voice]
mode = "off"

[music]
provider = "none"

[link]
port = 18765
"@ | Set-Content -Encoding utf8NoBOM (Join-Path $Home_ "config.toml")
"chatgpt" | Set-Content (Join-Path $Home_ "setup_done")

# ---------------------------------------------------------------- SW-V4 L: upgrade (and rollback) scenario
if ($UpgradeFrom) {
    $UpgradeFrom = (Resolve-Path $UpgradeFrom).Path
    $before = Collect "before"
    Check ((Install "autostart,desktopicon" @() $UpgradeFrom "old_install_s") -eq 0) "old installer installs silently: $(Split-Path $UpgradeFrom -Leaf)"
    $oldVer = Version-Of
    Check ($oldVer -match '^\d+\.\d+\.\d+\.\d+$') "old version is $oldVer"
    Check (Seed "seed") "old-layout data seeded (conversation, memory, job, settings)"
    $settingsBefore = @("config.toml", "features.json", "identity.json", "secrets.env", "models/owner-wake.onnx", "google/token.json", "profiles/owner/config.toml") | ForEach-Object { (Get-FileHash (Join-Path $Home_ $_)).Hash }
    $desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "JARVES.lnk"
    Check (Test-Path $desktop) "old desktop shortcut created for preservation test"
    $mid = Collect "old-installed"                     # Run key / PATH with the old version installed
    # #153: a file locked by a process outside the program folder (an editor, a backup or AV scan) cannot be ended by
    # setup: the upgrade must be refused cleanly (no runtime error) and leave the old version complete and working.
    $lockFile = Join-Path $App "python\smoke-lock.txt"
    $locker = Start-Process "pwsh" -WindowStyle Hidden -PassThru -ArgumentList @("-NoProfile", "-Command",
        "`$f = [IO.File]::Open('$lockFile', 'OpenOrCreate', 'ReadWrite', 'None'); Start-Sleep 900")
    for ($i = 0; $i -lt 40 -and -not (Test-Path $lockFile); $i++) { Start-Sleep -Milliseconds 250 }
    Start-Sleep 1
    Check ((Install "DEFAULT" @() $Installer "") -ne 0) "file locked by a process outside the program folder: the upgrade is refused (setup exit code is not 0)"
    Check (@(Select-String -Path (Join-Path $Work "install-*.log") -Pattern "Cannot retain previous program: python" -ErrorAction SilentlyContinue).Count -ge 1) "the refusal is logged with the part that was in use"
    Check ((Version-Of) -eq $oldVer -and (Test-Path (Join-Path $App "python\python.exe")) -and (Test-Path (Join-Path $App "jarvis.cmd"))) "the refused upgrade left the old version $oldVer complete"
    Check (-not (Test-Path (Join-Path $App "rollback\python"))) "nothing was left half-moved into the rollback folder"
    Check ((Jarvis @("--version")).text -match [regex]::Escape($oldVer)) "the old version still runs after the refused upgrade"
    Stop-Process -Id $locker.Id -Force -ErrorAction SilentlyContinue
    $locker.WaitForExit(10000) | Out-Null
    Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
    # #153 review P2-7: an undeletable leftover rollback folder (a file in it held open by a process outside the program
    # folder) must stop the upgrade BEFORE anything moves. The old code deleted what it could, moved python, failed on
    # face and then "restored" the stale rollback\face over the good face. Discriminating: {app}\face must be untouched.
    $part = if (Test-Path (Join-Path $App "face")) { "face" } else { "python" }
    $goodFiles = @(Get-ChildItem -LiteralPath (Join-Path $App $part) -Recurse -File).Count
    New-Item -ItemType Directory -Force (Join-Path $App "rollback\$part") | Out-Null
    $staleLocked = Join-Path $App "rollback\$part\stale-locked-from-older-run.txt"
    $locker = Start-Process "pwsh" -WindowStyle Hidden -PassThru -ArgumentList @("-NoProfile", "-Command",
        "`$f = [IO.File]::Open('$staleLocked', 'OpenOrCreate', 'ReadWrite', 'None'); Start-Sleep 900")
    for ($i = 0; $i -lt 40 -and -not (Test-Path $staleLocked); $i++) { Start-Sleep -Milliseconds 250 }
    Start-Sleep 1
    Check ((Install "DEFAULT" @() $Installer "") -ne 0) "undeletable leftover rollback folder: the upgrade is refused (setup exit code is not 0)"
    Check (@(Select-String -Path (Join-Path $Work "install-*.log") -Pattern "Old rollback folder could not be deleted or moved" -ErrorAction SilentlyContinue).Count -ge 1) "undeletable leftover rollback folder: the refusal is logged"
    Check ((Test-Path (Join-Path $App "python\python.exe")) -and (Test-Path (Join-Path $App "jarvis.cmd")) -and (Version-Of) -eq $oldVer) "undeletable leftover rollback folder: the old version $oldVer is complete"
    Check (@(Get-ChildItem -LiteralPath (Join-Path $App $part) -Recurse -File -ErrorAction SilentlyContinue).Count -eq $goodFiles -and -not (Test-Path (Join-Path $App "$part\stale-locked-from-older-run.txt"))) "undeletable leftover rollback folder: the working $part was not replaced by the stale one"
    Check ((Jarvis @("--version")).text -match [regex]::Escape($oldVer)) "undeletable leftover rollback folder: the old version still runs"
    Stop-Process -Id $locker.Id -Force -ErrorAction SilentlyContinue
    $locker.WaitForExit(10000) | Out-Null
    Remove-Item -LiteralPath (Join-Path $App "rollback") -Recurse -Force -ErrorAction SilentlyContinue

    # #165: low disk. /DISKRESERVEMB= demands a huge amount of extra free space (stricter only), so the pre-check refuses
    # BEFORE JARVES is stopped, before the backup and before anything is moved: plain message, exit code not 0.
    $backupsBefore = @(Get-ChildItem (Join-Path $Home_ "backups") -ErrorAction SilentlyContinue).Count
    Check ((Install "DEFAULT" @("/DISKRESERVEMB=900000000") $Installer "") -ne 0) "low disk: the upgrade is refused up front (setup exit code is not 0)"
    $diskLog = Get-ChildItem (Join-Path $Work "install-*.log") | Sort-Object LastWriteTime | Select-Object -Last 1
    Check (@(Select-String -Path $diskLog.FullName -Pattern "Disk space check result code: 4" -ErrorAction SilentlyContinue).Count -ge 1) "the low-disk refusal is logged"
    Check (@(Select-String -Path $diskLog.FullName -Pattern "Stopping JARVES before the upgrade" -ErrorAction SilentlyContinue).Count -eq 0) "low disk: refused before JARVES was stopped (review P3-1)"
    Check ((Version-Of) -eq $oldVer -and (Test-Path (Join-Path $App "python\python.exe"))) "low disk: the old version $oldVer is untouched"
    Check (-not (Test-Path (Join-Path $App "rollback\python"))) "low disk: nothing was moved into the rollback folder"
    Check (@(Get-ChildItem (Join-Path $Home_ "backups") -ErrorAction SilentlyContinue).Count -eq $backupsBefore) "low disk: no backup was started"
    # #164: a candidate whose Core never becomes ready (the update record names a version that never starts, so the
    # Core never marks it healthy) is undone by setup: the previous program is back, data untouched, the owner notice
    # is recorded, nothing is deleted. The data folder is restored afterwards so the later cases see the seeded data.
    $homeCopy = Join-Path $Work "home-before-health-case"
    Copy-Item -LiteralPath $Home_ -Destination $homeCopy -Recurse -Force
    # #166: the old Core is RUNNING when setup starts, so setup restarts it and runs the health check (a Core that was not
    # running is neither restarted nor judged).
    $oldCore = Start-Process "cmd.exe" -ArgumentList "/c `"`"$App\jarvis.cmd`" run > `"$Work\core-health.out`" 2>&1`"" -NoNewWindow -PassThru
    Check (Wait-Log "CORE_READY" 90) "the old Core is running before the candidate is installed"
    $pendingFile = Join-Path $Home_ "updates\pending.json"
    New-Item -ItemType Directory -Force (Split-Path $pendingFile) | Out-Null
    (@{ state = "installing"; from = $oldVer; to = "9.9.9"; attempts = 0 } | ConvertTo-Json) | Set-Content $pendingFile
    function Schema-Of() {
        $m = Join-Path $Home_ "schema.json"
        if (Test-Path $m) { return [int]((Get-Content $m -Raw | ConvertFrom-Json).schema) }
        return -1
    }
    $schemaBefore = Schema-Of
    # /HEALTHEXPECT=9.9.9: the health check judges only an update record for the version being installed (review P2-1);
    # the smoke fakes a record for 9.9.9, so it tells setup that this is the version to expect
    Check ((Install "DEFAULT" @("/RESTARTJARVES=1", "/HEALTHTIMEOUT=5", "/HEALTHEXPECT=9.9.9") $Installer "") -eq 0) "candidate that never becomes ready: setup finishes (it undoes itself)"
    $healthRec = Get-Content (Join-Path $Home_ "updates\health.json") -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    Check (@(Select-String -Path (Join-Path $Work "install-*.log") -Pattern "Health check result code: 3" -ErrorAction SilentlyContinue).Count -ge 1) "the health check reported 'not ready' (code 3)"
    # review P1-2 / #260 F2: the check ran with the PREVIOUS python ({app}\rollback\python), never the new runtime
    Check (@(Select-String -Path (Join-Path $Work "install-*.log") -Pattern 'Health check runs with .*\\rollback\\python\\python\.exe' -ErrorAction SilentlyContinue).Count -ge 1) "the health check ran with the previous python (rollback\python\python.exe)"
    # #260 F2: the pinned old version opens the same data schema as the candidate, so this fixture MUST take the swap
    # branch (a program-only rollback); health_failed here would mean the schema guard is wrong
    if ($healthRec.status -eq "health_failed") {
        Write-Host "       data schema before $schemaBefore, now $(Schema-Of); install-state.json:"
        Get-Content (Join-Path $App "install-state.json") -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "       $_" }
    }
    Check ($healthRec.status -eq "rolled_back" -and -not $healthRec.notified) "the fixture takes the swap branch: the automatic rollback is recorded (rolled_back) for the one-time owner notice"
    Check ((Version-Of) -eq $oldVer -and (Test-Path (Join-Path $App "python\python.exe")) -and (Test-Path (Join-Path $App "jarvis.cmd"))) "health check failed: the previous program $oldVer is back"
    Check (@(Get-ChildItem $App -Directory -Filter "failed-upgrade-health-*" -ErrorAction SilentlyContinue).Count -ge 1) "the candidate's program was kept aside, not deleted"
    Check (@(Select-String -Path (Join-Path $Work "install-*.log") -Pattern "Health rollback done" -ErrorAction SilentlyContinue).Count -ge 1) "the health rollback is logged"
    Check ((Jarvis @("--version")).text -match [regex]::Escape($oldVer)) "the previous version runs after the automatic rollback"
    # review P2-8: prove the OLD Core really starts on the migrated data: the process holding core.lock runs from
    # {app}\python (the old program) and logged CORE_READY after it took the lock
    $readyOld = $false
    $pyDir = (Join-Path $App "python") + "\"
    for ($i = 0; $i -lt 180 -and -not $readyOld; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $fs = [IO.File]::Open((Join-Path $Home_ "core.lock"), "Open", "Read", "ReadWrite")
            try { [void]$fs.Seek(1, "Begin"); $buf = New-Object byte[] 4096; $n = $fs.Read($buf, 0, 4096) } finally { $fs.Dispose() }
            $holder = [Text.Encoding]::UTF8.GetString($buf, 0, $n).Trim() | ConvertFrom-Json
            $proc = Get-Process -Id ([int]$holder.pid) -ErrorAction Stop
            if ($proc.Path -and $proc.Path.StartsWith($pyDir, [StringComparison]::OrdinalIgnoreCase)) {
                $since = [double]$holder.started - 1
                foreach ($line in @(Get-Content (Join-Path $Home_ "logs\jarves.log") -ErrorAction Stop | Where-Object { $_ -match '"CORE_READY"' })) {
                    if ($line -match '"ts":\s*"([^"]+)"') {
                        $t = [DateTimeOffset]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture).ToUnixTimeMilliseconds() / 1000.0
                        if ($t -ge $since) { $readyOld = $true }
                    }
                }
            }
        } catch {}
    }
    Check $readyOld "after the automatic rollback the OLD Core starts and reaches CORE_READY (lock holder runs from {app}\python)"
    Jarvis @("stop") | Out-Null
    Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    Remove-Item -LiteralPath $Home_ -Recurse -Force
    Move-Item -LiteralPath $homeCopy -Destination $Home_
    Get-ChildItem $App -Directory -Filter "failed-upgrade-health-*" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # #260 F2: the cases below must upgrade from the OLD version, never silently new -> new: put $oldVer back if needed
    if ((Version-Of) -ne $oldVer) {
        Write-Host "       the health case left $(Version-Of) installed: reinstalling $oldVer for the upgrade cases"
        Install "DEFAULT" @("/ROLLBACK=1") $UpgradeFrom "" | Out-Null
    }
    Check ((Version-Of) -eq $oldVer) "the following upgrade cases start from the old version $oldVer"
    # #153: a leftover rollback folder from an older run must not block the upgrade
    New-Item -ItemType Directory -Force (Join-Path $App "rollback\python") | Out-Null
    $stale = Join-Path $App "rollback\python\stale-from-older-run.txt"
    "old" | Set-Content $stale
    # #153: a windowed process under the program folder (face / music player stand-in) is asked to close first
    # ONE pre-quoted argument string: Start-Process joins an array WITHOUT quoting, so `-c "import ..."` arrived as
    # `-c import` and python exited at once (the stand-ins never ran during setup, so nothing proved the stop step)
    $windowed = Start-Process (Join-Path $App "python\pythonw.exe") -PassThru -ArgumentList (
        '-I -c "import tkinter as t; r = t.Tk(); r.title(''JARVES smoke window''); r.mainloop()"')
    for ($i = 0; $i -lt 40 -and $windowed.MainWindowHandle -eq 0 -and -not $windowed.HasExited; $i++) { Start-Sleep -Milliseconds 250; $windowed.Refresh() }
    $hasWindow = (-not $windowed.HasExited) -and $windowed.MainWindowHandle -ne 0
    # issue #141: a process running from the old program folder (like `jarves dashboard`) must not block the upgrade
    $stray = Start-Process (Join-Path $App "python\python.exe") -ArgumentList '-I -c "import time; time.sleep(900)"' -WindowStyle Hidden -PassThru
    Start-Sleep 2
    Check (-not $stray.HasExited) "process running from the old program folder (dashboard stand-in) is alive before the upgrade (2 s after start)"
    # silent upgrade over it: no /TASKS (the installer keeps its defaults), same AppId, data folder untouched
    Check ((Install "DEFAULT" @("/KEEPDATA=0") $Installer "silent_upgrade_s") -eq 0) "silent upgrade $oldVer -> $Version"
    Check (Test-Path (Join-Path $App "rollback\python\python.exe")) "previous program retained for offline rollback"
    Check ($stray.WaitForExit(5000)) "the stray old-folder process was ended by the installer"
    # evidence for the CI log: what the installer's stop step did (stray pid $($stray.Id), windowed pid $($windowed.Id))
    $upLog = Get-ChildItem (Join-Path $Work "install-*.log") | Sort-Object LastWriteTime | Select-Object -Last 1
    Write-Host "       stray pid $($stray.Id), windowed pid $($windowed.Id); setup log $($upLog.Name):"
    Select-String -Path $upLog.FullName -Pattern "Stopping JARVES|Retaining|retain|Restart Manager|RestartManager|close" -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "       $($_.Line)" }
    Check (@(Select-String -Path (Join-Path $Work "install-*.log") -Pattern "Stopping JARVES before the upgrade: hard-stopped: python \(pid $($stray.Id)\)" -ErrorAction SilentlyContinue).Count -ge 1) "the installer logged the process it had to hard-stop, by name"
    Check (-not (Test-Path (Join-Path $App "rollback\python\stale-from-older-run.txt"))) "leftover rollback folder from an older run was replaced"
    Check ($windowed.WaitForExit(5000)) "windowed process under the program folder ended"
    if ($hasWindow) {
        Check (@(Select-String -Path (Join-Path $Work "install-*.log") -Pattern "asked to close: pythonw \(pid $($windowed.Id)\)" -ErrorAction SilentlyContinue).Count -ge 1) "windowed process under the program folder was asked to close before any hard stop"
    } else { Write-Host "[SKIP] windowed process under the program folder had no window on this runner (no desktop); graceful close not checked" }
    $state = Get-Content (Join-Path $App "install-state.json") -Raw | ConvertFrom-Json
    Check ($state.complete -and $state.backups.Count -ge 1) "installer verified backup and completed migration"
    $newVer = Version-Of
    Check ($newVer -ne $oldVer -and (-not $Version -or $newVer -eq $Version)) "installed version is now $newVer"
    $settingsAfter = @("config.toml", "features.json", "identity.json", "secrets.env", "models/owner-wake.onnx", "google/token.json", "profiles/owner/config.toml") | ForEach-Object { (Get-FileHash (Join-Path $Home_ $_)).Hash }
    Check (($settingsBefore -join ",") -eq ($settingsAfter -join ",")) "settings files are byte-for-byte unchanged by the upgrade"
    $after = Collect "upgraded"
    $m = Get-Content $mid -Raw | ConvertFrom-Json
    $a = Get-Content $after -Raw | ConvertFrom-Json
    Check ((($m.run | ConvertTo-Json -Compress) -eq ($a.run | ConvertTo-Json -Compress))) "autostart (HKCU Run) is exactly as before the upgrade"
    Check ($m.run.JARVES -and $a.run.JARVES) "enabled start-with-Windows survives a silent upgrade"
    Check (Test-Path $desktop) "desktop shortcut survives a silent upgrade"
    Check (Seed "verify") "installer migration preserves all seeded rows before Core startup"
    # first start of the new version: backup, data migration, CORE_READY
    $core = Start-Process "cmd.exe" -ArgumentList "/c `"`"$App\jarvis.cmd`" run > `"$Work\core.out`" 2>&1`"" -NoNewWindow -PassThru
    Check (Wait-Log "CORE_READY" 90) "the upgraded Core starts offline (CORE_READY)"
    $r = Jarvis @("stop")
    Check (Wait-Log "CORE_STOPPED" 30) "jarvis stop ends it (CORE_STOPPED)"
    if (-not $core.WaitForExit(20000)) { $core.Kill($true) }
    Check (Seed "verify") "every seeded row is present after the upgrade, migrated to the newest schema, backup taken first"
    if (-not $SkipDoctor) {
        $r = Jarvis @("doctor") 180
        Check ($r.text -match "JARVES doctor" -and $r.text -match "(problem\(s\) found|All checks passed)") "jarvis doctor completes after the upgrade"
    }
    # #166 (review P2-9): nothing runs, setup is started like the in-app updater (/RESTARTJARVES=1): after it, nothing
    # runs from the program folder, and the health check is skipped (nothing was started to judge)
    Jarvis @("stop") | Out-Null
    Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    Check ((Install "DEFAULT" @("/RESTARTJARVES=1") $Installer "") -eq 0) "nothing running: an updater-style setup (/RESTARTJARVES=1) finishes"
    $nrLog = Get-ChildItem (Join-Path $Work "install-*.log") | Sort-Object LastWriteTime | Select-Object -Last 1
    Check (@(Select-String -Path $nrLog.FullName -Pattern "Running before the update: nothing" -ErrorAction SilentlyContinue).Count -ge 1) "nothing running: setup detected that nothing ran"
    Check (@(Select-String -Path $nrLog.FullName -Pattern "Health check skipped: the Core was not running" -ErrorAction SilentlyContinue).Count -ge 1) "nothing running: the health check is skipped and logged"
    Start-Sleep 5
    $running = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) })
    Check ($running.Count -eq 0) "nothing was running before the update: nothing is started after it ($($running.Count) process(es) under the program folder)"
    # #293: the in-app path stops the Core itself, so setup detects nothing; it tells setup with /WASRUNNING=core. The Core
    # that "ran before" is started and health-checked even though nothing runs at setup start.
    Check ((Install "DEFAULT" @("/RESTARTJARVES=1", "/WASRUNNING=core") $Installer "") -eq 0) "told the Core ran (/WASRUNNING=core): setup finishes"
    $wrLog = Get-ChildItem (Join-Path $Work "install-*.log") | Sort-Object LastWriteTime | Select-Object -Last 1
    Check (@(Select-String -Path $wrLog.FullName -Pattern "Told by the caller what ran before the update" -ErrorAction SilentlyContinue).Count -ge 1) "told the Core ran: setup logged the caller's record"
    Check (@(Select-String -Path $wrLog.FullName -Pattern "Health check skipped" -ErrorAction SilentlyContinue).Count -eq 0) "told the Core ran: the health check is not skipped"
    Jarvis @("stop") | Out-Null
    Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
    if ($Rollback) {
        # the in-app updater records the previous version before it starts setup; record the same thing for the test
        Check (Seed "previous" @("--installer", $UpgradeFrom, "--old-version", $oldVer, "--new-version", $newVer)) "previous version recorded like the updater does"
        $readyBefore = @(Select-String -Path (Join-Path $Home_ "logs\jarves.log") -Pattern CORE_READY -ErrorAction SilentlyContinue).Count
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = Jarvis @("update", "--rollback", "--yes") 240
        Check ($r.code -eq 0) "jarvis update --rollback --yes: $($r.text.Trim().Split("`n")[-1])"
        # The version file is extracted before Setup finishes: wait for the bootstrap process too.
        foreach ($setup in @(Get-Process -Name "JARVES-Setup-$oldVer" -ErrorAction SilentlyContinue)) {
            if (-not $setup.WaitForExit(240000)) { throw "rollback installer did not finish" }
        }
        for ($i = 0; $i -lt 480 -and (Version-Of) -ne $oldVer; $i++) { Start-Sleep -Milliseconds 500 }
        $Times["silent_rollback_s"] = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        Check ((Version-Of) -eq $oldVer) "version is back to $oldVer"
        # The pinned 4.0.0 old version also uses schema 3 and migrates restored legacy data on restart.
        for ($i = 0; $i -lt 120; $i++) {
            $readyNow = @(Select-String -Path (Join-Path $Home_ "logs\jarves.log") -Pattern CORE_READY -ErrorAction SilentlyContinue).Count
            if ($readyNow -gt $readyBefore) { break }
            Start-Sleep -Milliseconds 500
        }
        Check ($readyNow -gt $readyBefore) "rolled-back version restarts successfully"
        $r = Jarvis @("stop")
        Check (Seed "verify") "rolled-back version retains every seeded row and setting"
        $pending = Get-Content (Join-Path $Home_ "updates\pending.json") -Raw -ErrorAction SilentlyContinue
        Check ($pending -match "rolled_back") "the rollback is recorded (pending.json: rolled_back)"
    }
    Check ((Uninstall @("/PURGE=1") "silent_uninstall_s") -eq 0) "uninstall /PURGE=1"
    Check (-not (Test-Path $desktop)) "uninstall removes the desktop shortcut"
    $final = Collect "final"
    Check (Audit "uninstalled" $before $final @("--purged")) "no residue after uninstall (Run key, PATH, tasks, Start menu, folders)"
    Save-Times
    if (-not $failures) { Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue }
    if ($failures) { Write-Host "SMOKE RESULT: FAIL ($failures)"; exit 1 }
    Write-Host "SMOKE RESULT: PASS (upgrade$(if ($Rollback) { ' + rollback' }))"
    exit 0
}

# Independent of CLI/Core/face and answers checks; this runner has its own HKCU/AppId.
if ($Scenario -eq "autostart-purge") {
    if ($UpgradeFrom) { throw "autostart-purge cannot be combined with UpgradeFrom" }
    $stBefore = Collect "before"
    Check (Seed "seed") "retained old data exists before the fresh program install"
    Check ((Install "autostart" @() "" "silent_install_s") -eq 0) "install with start-at-login"
    $entry = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name JARVES -ErrorAction SilentlyContinue).JARVES
    Check ($entry -and $entry -like "*$App*pythonw.exe*") "start-at-login points to the installed Core"
    Check (Seed "verify") "data-only upgrade preserves seeded rows, model and settings"
    Check ((Uninstall @("/PURGE=1") "silent_uninstall_s") -eq 0) "uninstall /PURGE=1"
    Check (Audit "uninstalled" $stBefore (Collect "purged") @("--purged")) "no residue after autostart/purge scenario"
    Save-Times
    if (-not $failures) { Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue }
    if ($failures) { Write-Host "SMOKE RESULT: FAIL ($failures)"; exit 1 }
    Write-Host "SMOKE RESULT: PASS (autostart-purge)"
    exit 0
}

# 1 clean install
$stBefore = Collect "before"
$tasks1 = if ($DefaultTasks) { "DEFAULT" } else { "" }
Check ((Install $tasks1 @() "" "silent_install_s") -eq 0) "silent per-user install"
$stInstalled = Collect "installed"
$auditExtra = @("--path-entry", $App)
Check (Audit "installed" $stBefore $stInstalled $auditExtra) "clean machine: autostart OFF by default, no scheduled task, PATH as expected"
foreach ($f in @("jarvis.cmd", "python\python.exe", "unins000.exe", "THIRD_PARTY_NOTICES.txt")) {
    Check (Test-Path (Join-Path $App $f)) "installed $f"
}
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\JARVES\JARVES.lnk"
Check (Test-Path $menu) "Start menu entry"
$face = Join-Path $App "face\JARVES Face.console.exe"
if ($RequireFace) { Check (Test-Path $face) "exported face installed" }

# 2 CLI
$r = Jarvis @("--version")
Check ($r.code -eq 0 -and $r.text -match "^JARVES ") "jarvis --version: $($r.text.Trim())"
if ($Version) { Check ($r.text -match [regex]::Escape($Version)) "version is $Version" }
$r = Jarvis @("status")
Check ($r.text -match "Core is NOT running") "jarvis status (not running)"
$r = Jarvis @("features")
Check ($r.text -match "update_check") "jarvis features lists update_check"
# 2b every add-on shipped in the payload: imports (OpenCV, PortAudio, wake models, tray, tzdata...), YuNet, dashboard
#    and guide data. No camera or microphone is opened, nothing is downloaded.
$r = Jarvis @("setup", "--check-addons")
Check ($r.code -eq 0 -and $r.text -match "ADDON CHECK: PASS") "all add-ons are in the installed app (jarvis setup --check-addons)"
if ($r.text -notmatch "ADDON CHECK: PASS") { $r.text }
if (-not $SkipDoctor) {
    $r = Jarvis @("doctor") 180
    Check ($r.text -match "JARVES doctor" -and $r.text -match "(problem\(s\) found|All checks passed)") "jarvis doctor completes"
}

# 3 offline Core start / stop
$log = Join-Path $Home_ "logs\jarves.log"
$core = Start-Process "cmd.exe" -ArgumentList "/c `"`"$App\jarvis.cmd`" run > `"$Work\core.out`" 2>&1`"" -NoNewWindow -PassThru
for ($i = 0; $i -lt 120 -and -not ((Test-Path $log) -and (Select-String -Path $log -Pattern CORE_READY -Quiet)); $i++) {
    Start-Sleep -Milliseconds 500
}
Check ((Test-Path $log) -and (Select-String -Path $log -Pattern CORE_READY -Quiet)) "installed Core starts offline (CORE_READY)"
function Face([string]$scene, [string[]]$userArgs, [int]$timeoutS = 60) {
    # The shipped override.cfg starts the loading screen; a self-check scene is set the same way (release templates
    # refuse a scene path argument), then the shipped file is restored.
    $cfg = Join-Path $App "face\override.cfg"
    $keep = [IO.File]::ReadAllText($cfg)
    if ($scene) { [IO.File]::WriteAllText($cfg, "[application]`n`nrun/main_scene=`"$scene`"`n") }
    $out = Join-Path $Work "face.out"
    try {
        $quit = if ($scene) { @("--quit-after", "3000") } else { @() }     # the loading check quits by itself
        $p = Start-Process $face -ArgumentList (@("--headless") + $quit + @("--") + $userArgs) -NoNewWindow -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError "$out.err"
        if (-not $p.WaitForExit($timeoutS * 1000)) { $p.Kill($true) }
        return (Get-Content $out, "$out.err" -Raw -ErrorAction SilentlyContinue) -join "`n"
    }
    finally { [IO.File]::WriteAllText($cfg, $keep) }
}
function Tail([string]$o) { $o.Substring([Math]::Max(0, $o.Length - 2000)) }
if (Test-Path $face) {
    # 4a the installed launch path: face -> loading screen -> reaches the running Core's readiness
    $o = Face "" @("--loading-check", "--core-port=18765") 45
    Check ($o -match "LOADING CHECK RESULT: PASS") "exported face loading screen reaches the installed Core"
    if ($o -notmatch "LOADING CHECK RESULT: PASS") { Tail $o }
}
$r = Jarvis @("stop")
for ($i = 0; $i -lt 60 -and -not (Select-String -Path $log -Pattern CORE_STOPPED -Quiet); $i++) { Start-Sleep -Milliseconds 500 }
Check (Select-String -Path $log -Pattern CORE_STOPPED -Quiet) "jarvis stop ends it (CORE_STOPPED)"
if (-not $core.WaitForExit(20000)) { $core.Kill($true) }
if ($failures) { Get-Content "$Work\core.out" -Tail 30 -ErrorAction SilentlyContinue }

# 4b exported room + face self-checks (same markers as the Godot CI job)
if (Test-Path $face) {
    $o = Face "res://main.tscn" @("--check", "--no-core")
    Check ($o -match "CHECK RESULT: PASS") "exported room viewer self-check"
    if ($o -notmatch "CHECK RESULT: PASS") { Tail $o }
    $o = Face "res://face/face_app.tscn" @("--check")
    Check ($o -match "FACE CHECK RESULT: PASS") "exported face self-check"
    if ($o -notmatch "FACE CHECK RESULT: PASS") { Tail $o }
}
# 5 upgrade in place, with a setup answers file (the silent-install path of the first-run wizard)
$answers = Join-Path $Work "answers.json"
'{"language": "en", "connection": "chatgpt", "display_name": "SmokeMax", "spoken_name": "Max","features": {"chimes": false, "camera": false}}' |
    Set-Content -Encoding utf8NoBOM $answers
Check ((Install "" @("/SETUPANSWERS=`"$answers`"") "" "silent_upgrade_s") -eq 0) "upgrade over the existing install with /SETUPANSWERS"
$ident = Get-Content (Join-Path $Home_ "identity.json") -Raw -Encoding utf8 -ErrorAction SilentlyContinue
$feats = Get-Content (Join-Path $Home_ "features.json") -Raw -Encoding utf8 -ErrorAction SilentlyContinue
Check (($ident -match "SmokeMax") -and ($feats -match '"chimes": false')) "installer applied the setup answers (name, add-on switch)"
$r = Jarvis @("setup", "--defaults", "--lang", "en")
Check ($r.code -eq 0 -and $r.text -match "Setup complete") "jarvis setup --defaults re-run completes"
$ident2 = Get-Content (Join-Path $Home_ "identity.json") -Raw -Encoding utf8 -ErrorAction SilentlyContinue
Check ($ident2 -eq $ident) "re-run with defaults keeps the earlier choices (idempotent)"
$r = Jarvis @("--version")
Check ($r.code -eq 0) "runs after upgrade"
New-Item -ItemType File -Force (Join-Path $Home_ "memory.db") | Out-Null
"OPENAI_API_KEY=sk-smoke-not-real" | Set-Content (Join-Path $Home_ "secrets.env")

# 6 uninstall keeps data
$stPreUn = Collect "pre-uninstall"
Check ((Uninstall @() "silent_uninstall_s") -eq 0) "silent uninstall"
$stUn = Collect "uninstalled"
Check (Audit "uninstalled" $stBefore $stUn @("--keep-data", "--data-before", $stPreUn)) "no residue after uninstall; user data kept byte-for-byte"
Check (-not (Test-Path (Join-Path $App "python"))) "program files removed"
Check (-not (Test-Path $menu)) "Start menu entry removed"
Check ((Test-Path (Join-Path $Home_ "memory.db")) -and (Test-Path (Join-Path $Home_ "secrets.env"))) "user data kept"

# Functional and integration/purge scenarios run on independent Windows VMs in CI.
if ($Scenario -eq "full") {
# 7 purge only when asked
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if ($CheckAutostart) {
    Check ((Install "autostart" @() "" "autostart_install_s") -eq 0) "reinstall with start-at-login"
    $entry = (Get-ItemProperty $run -Name JARVES -ErrorAction SilentlyContinue).JARVES
    Check ($entry -and $entry -like "*$App*pythonw.exe*") "start-at-login entry points to the installed Core: $entry"
} else {
    Check ((Install "" @() "" "reinstall_s") -eq 0) "reinstall"
}
Check ((Uninstall @("/PURGE=1")) -eq 0) "uninstall /PURGE=1"
Check (-not (Test-Path $Home_)) "user data removed on request"
Check (Audit "uninstalled" $stBefore (Collect "purged") @("--purged")) "no residue after uninstall /PURGE=1"
if ($CheckAutostart) {
    Check (-not (Get-ItemProperty $run -Name JARVES -ErrorAction SilentlyContinue)) "uninstall removed start-at-login"
}

}

Save-Times
if (-not $failures) { Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue }
if ($failures) { Write-Host "SMOKE RESULT: FAIL ($failures)"; exit 1 }
Write-Host "SMOKE RESULT: PASS"
