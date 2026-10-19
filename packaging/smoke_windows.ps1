<#
Silent install smoke test of a built JARVES installer (CI: jarves-checks `installer` job; also runs locally).
Everything happens in a temp folder: program dir, JARVES_HOME (data) and the Core's link port, so a real JARVES on
the same PC is never touched. No audio is played, no network service is called, no key is needed.

  1 clean install (/VERYSILENT, per user, no tasks)      -> files, Start-menu entry, uninstaller present
  2 jarvis --version / status / features / doctor         -> build identity, "NOT running", update_check listed
  3 offline Core start + orderly stop through the link    -> CORE_READY / CORE_STOPPED in the log
  4 exported face: loading screen reaches the Core; room + face self-checks (headless) -> "... RESULT: PASS"
  5 upgrade: same installer again over the install         -> still runs
  6 uninstall (silent)                                     -> program folder gone, user data KEPT
  7 install + uninstall /PURGE=1                           -> user data removed only when asked

  pwsh packaging\smoke_windows.ps1 -Installer dist\JARVES-Setup-0.1.0.57.exe [-Version 0.1.0.57] [-RequireFace]
#>
param(
    [Parameter(Mandatory)] [string]$Installer,
    [string]$Version = "",
    [string]$Work = (Join-Path ([IO.Path]::GetTempPath()) "jarves-smoke-$PID"),
    [switch]$RequireFace,
    [switch]$SkipDoctor          # local runs: doctor records 1.5 s from the default microphone (CI has none)
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
function Install([string[]]$extra = @()) {
    $p = Start-Process $Installer -Wait -PassThru -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/SP-", "/TASKS=`"`"", "/DIR=`"$App`"", "/LOG=`"$(Join-Path $Work "install-$(Get-Random).log")`"") + $extra)
    return $p.ExitCode
}
function Uninstall([string[]]$extra = @()) {
    $u = Join-Path $App "unins000.exe"
    if (-not (Test-Path $u)) { return -2 }
    $p = Start-Process $u -Wait -PassThru -ArgumentList (@("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") + $extra)
    for ($i = 0; $i -lt 120 -and (Test-Path $u); $i++) { Start-Sleep -Milliseconds 500 }   # the copy in %TEMP% finishes
    return $p.ExitCode
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

# 1 clean install
Check ((Install) -eq 0) "silent per-user install"
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
# 5 upgrade in place
Check ((Install) -eq 0) "upgrade over the existing install"
$r = Jarvis @("--version")
Check ($r.code -eq 0) "runs after upgrade"
New-Item -ItemType File -Force (Join-Path $Home_ "memory.db") | Out-Null
"OPENAI_API_KEY=sk-smoke-not-real" | Set-Content (Join-Path $Home_ "secrets.env")

# 6 uninstall keeps data
Check ((Uninstall) -eq 0) "silent uninstall"
Check (-not (Test-Path (Join-Path $App "python"))) "program files removed"
Check (-not (Test-Path $menu)) "Start menu entry removed"
Check ((Test-Path (Join-Path $Home_ "memory.db")) -and (Test-Path (Join-Path $Home_ "secrets.env"))) "user data kept"

# 7 purge only when asked
Check ((Install) -eq 0) "reinstall"
Check ((Uninstall @("/PURGE=1")) -eq 0) "uninstall /PURGE=1"
Check (-not (Test-Path $Home_)) "user data removed on request"

Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
if ($failures) { Write-Host "SMOKE RESULT: FAIL ($failures)"; exit 1 }
Write-Host "SMOKE RESULT: PASS"
