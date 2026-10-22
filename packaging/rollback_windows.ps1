<# Offline rollback of the program snapshot kept by Setup. Data is restored with the app backup tool first.
   Stop JARVES and run: powershell -NoProfile -File "<installed app>\rollback_windows.ps1"
   The installer must have retained rollback/python and install-state.json. No downloads or administrator access.
#>
param([string]$App = $PSScriptRoot)
$ErrorActionPreference = "Stop"
$App = [IO.Path]::GetFullPath($App).TrimEnd('\')
$state = Join-Path $App "install-state.json"
$previous = Join-Path $App "rollback"
if (-not (Test-Path -LiteralPath $state) -or -not (Test-Path -LiteralPath "$previous\python\python.exe") -or
    -not (Test-Path -LiteralPath "$previous\jarvis.cmd")) { throw "No previous program snapshot is available" }
foreach ($path in @($App, $previous, "$App\python", "$previous\python")) {
    if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked program directories are not supported" }
}
$record = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
$stop = Start-Process cmd.exe -WindowStyle Hidden -Wait -PassThru -ArgumentList @('/c', '""' + $App + '\jarvis.cmd" stop"')
& "$App\python\python.exe" -I "$App\installer_state.py" restore-data --home $record.home --state $state
if ($LASTEXITCODE -ne 0) { throw "Backup verification/restore failed; program was not changed" }
$failed = Join-Path $App ("failed-upgrade-" + [DateTime]::UtcNow.Ticks)
New-Item -ItemType Directory -Path $failed | Out-Null
foreach ($part in @("python", "face", "bridge", "jarvis.cmd")) {
    $source = [IO.Path]::GetFullPath((Join-Path $previous $part))
    $target = [IO.Path]::GetFullPath((Join-Path $App $part))
    if (-not $source.StartsWith($App + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not $target.StartsWith($App + '\', [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe program path" }
    if (Test-Path -LiteralPath $source) {
        if (Test-Path -LiteralPath $target) { Move-Item -LiteralPath $target -Destination (Join-Path $failed $part) }
        Move-Item -LiteralPath $source -Destination $target
    }
}
Write-Host "Previous program and verified data restored. Newer program retained in $failed."
