<# CI-only background build. Windows Start-Process survives the launching workflow step.
   Start before Go setup; write <StateDir>/bridge.ready only after the bridge build succeeds; Wait joins or aborts.
   Status/log files live outside Out (build_windows replaces Out). Never runs an installer.
#>
param(
    [Parameter(Mandatory)] [ValidateSet("Start", "Run", "Wait")] [string]$Action,
    [Parameter(Mandatory)] [string]$StateDir,
    [string]$Version, [string]$Channel = "pr", [string]$Commit, [string]$Tree,
    [string]$Cache, [string]$Out,
    [switch]$Abort,
    [int]$TimeoutS = 1200
)
$ErrorActionPreference = "Stop"
$StateDir = [IO.Path]::GetFullPath($StateDir).TrimEnd('\')
$requestFile = Join-Path $StateDir "request.json"
$pidFile = Join-Path $StateDir "process.json"
$doneFile = Join-Path $StateDir "complete.json"
function Stop-OwnedBuild() {
    if (-not (Test-Path -LiteralPath $pidFile)) { return }
    $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $owned = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($owned) {
        if ($owned.StartTime.ToUniversalTime().Ticks -ne $record.started_ticks) { throw "Build PID was reused; refusing to stop it" }
        Stop-Process -InputObject $owned -Force
    }
}
function Show-Logs() {
    foreach ($name in @("stdout.log", "stderr.log")) {
        $log = Join-Path $StateDir $name
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log | Out-Host }
    }
}
if ($Action -eq "Start") {
    if (Test-Path -LiteralPath $requestFile) { throw "Build state already exists; use a fresh StateDir" }
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    @{ Version=$Version; Channel=$Channel; Commit=$Commit; Tree=$Tree; Cache=$Cache; Out=$Out;
       AlsoSmokeInstaller=$true; BridgeReadyFile=(Join-Path $StateDir "bridge.ready") } |
        ConvertTo-Json | Set-Content -LiteralPath $requestFile -Encoding utf8NoBOM
    # Quotes cannot occur in Windows filenames; protect spaces and a trailing backslash.
    $argv = @("-NoProfile", "-NonInteractive", "-File", $PSCommandPath, "-Action", "Run", "-StateDir", $StateDir)
    $quoted = $argv | ForEach-Object {
        if ($_.Contains('"')) { throw "Unexpected quote in build argument" }
        $part = $_; if ($part.EndsWith('\')) { $part += '\' }
        '"' + $part + '"'
    }
    $child = Start-Process (Get-Process -Id $PID).Path -WindowStyle Hidden -PassThru -ArgumentList $quoted
    @{ pid=$child.Id; started_ticks=$child.StartTime.ToUniversalTime().Ticks } |
        ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8NoBOM
    Write-Host "Bundle worker started (PID $($child.Id)); Go/bridge preparation can run concurrently."
    exit 0
}
if ($Action -eq "Run") {
    $code = 1
    try {
        $options = Get-Content -LiteralPath $requestFile -Raw | ConvertFrom-Json -AsHashtable
        & (Join-Path $PSScriptRoot "build_windows.ps1") @options *> (Join-Path $StateDir "stdout.log")
        if ($LASTEXITCODE -ne 0) { throw "Installer build returned $LASTEXITCODE" }
        $code = 0
    } catch { $_ | Out-String | Set-Content -LiteralPath (Join-Path $StateDir "stderr.log") -Encoding utf8NoBOM }
    finally {
        @{ exit_code=$code } | ConvertTo-Json | Set-Content -LiteralPath "$doneFile.tmp" -Encoding utf8NoBOM
        Move-Item -LiteralPath "$doneFile.tmp" -Destination $doneFile
    }
    exit $code
}
if ($Abort) {
    Stop-OwnedBuild
    Show-Logs
    throw "Bridge preparation failed; bundle worker aborted"
}
$clock = [Diagnostics.Stopwatch]::StartNew()
while (-not (Test-Path -LiteralPath $doneFile)) {
    if ($clock.Elapsed.TotalSeconds -gt $TimeoutS) {
        Stop-OwnedBuild
        Show-Logs
        throw "Installer build timed out after $TimeoutS seconds"
    }
    $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    if (-not (Get-Process -Id $record.pid -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $doneFile)) {
        Show-Logs
        throw "Build worker exited without completion status"
    }
    Start-Sleep -Milliseconds 500
}
Show-Logs
$result = Get-Content -LiteralPath $doneFile -Raw | ConvertFrom-Json
if ($result.exit_code -ne 0) { throw "Installer build failed (see worker logs above)" }
Write-Host "Installer build joined successfully."
