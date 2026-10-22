<#
Upgrade matrix (SW-V4 L): for every old installer in packaging\upgrade_fixtures.json, download it (SHA256-pinned), then run
packaging\smoke_windows.ps1 -UpgradeFrom <old> -Rollback against the candidate installer: install old -> seed data ->
silent upgrade -> Core start (data migration, backup first) -> all rows present, settings and autostart unchanged ->
`jarvis update --rollback` -> old version and old data back -> uninstall leaves no residue.

  pwsh packaging\upgrade_matrix.ps1 -Candidate dist\JARVES-Setup-3.0.0.9.exe -Version 3.0.0.9 [-Cache $env:RUNNER_TEMP]

Entry: {"series": "0.1.0.x", "version": "0.1.0.57", "url": "https://.../JARVES-Setup-0.1.0.57.exe", "sha256": "<64 hex>"}.
No fixtures = a warning, exit 0 (the matrix did NOT run); a fixture that fails its hash or a failing upgrade = exit 1.
#>
param(
    [Parameter(Mandatory)] [string]$Candidate,
    [string]$Version = "",
    [string]$Cache = (Join-Path ([IO.Path]::GetTempPath()) "jarves-upgrade-fixtures"),
    [string]$MetricsDir = "",
    [switch]$SkipDoctor
)
$ErrorActionPreference = "Stop"
$fx = (Get-Content (Join-Path $PSScriptRoot "upgrade_fixtures.json") -Raw | ConvertFrom-Json).fixtures
if (-not $fx -or $fx.Count -eq 0) {
    Write-Host "::warning::UPGRADE MATRIX: NOT RUN - no old installers are pinned in packaging/upgrade_fixtures.json yet"
    exit 0
}
New-Item -ItemType Directory -Force $Cache | Out-Null
$failed = 0
foreach ($f in $fx) {
    $exe = Join-Path $Cache "JARVES-Setup-$($f.version).exe"
    if (-not (Test-Path $exe) -or (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower() -ne $f.sha256) {
        & curl.exe -sSL --fail --retry 3 -o $exe $f.url
        if ($LASTEXITCODE -ne 0) { throw "download failed: $($f.url)" }
        $h = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
        if ($h -ne $f.sha256) { Remove-Item $exe; throw "SHA256 mismatch for fixture $($f.version): $h" }
    }
    Write-Host "==> upgrade matrix: $($f.series) ($($f.version)) -> $Version"
    $extra = @{}; if ($SkipDoctor) { $extra.SkipDoctor = $true }
    if ($MetricsDir) {
        New-Item -ItemType Directory -Force $MetricsDir | Out-Null
        $extra.MetricsOut = Join-Path $MetricsDir "upgrade-$($f.version)-metrics.json"
    }
    & (Join-Path $PSScriptRoot "smoke_windows.ps1") -Installer $Candidate -Version $Version -UpgradeFrom $exe -Rollback @extra
    if ($LASTEXITCODE -ne 0) { $failed++; Write-Host "UPGRADE MATRIX: $($f.version) FAILED" }
}
if ($failed) { Write-Host "UPGRADE MATRIX RESULT: FAIL ($failed)"; exit 1 }
Write-Host "UPGRADE MATRIX RESULT: PASS ($($fx.Count) fixture(s))"
