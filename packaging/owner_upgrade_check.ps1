<#
.SYNOPSIS
  Read-only upgrade report for the owner PC. Installs nothing and changes no setting.

.DESCRIPTION
  Prints: installed version (Windows uninstall entry), `jarvis --version`, the latest published version
  (HTTPS GET of latest.json from igalgerman/jarvis-releases), whether an update is available, where the
  rollback backup is (<state>\updates\previous\previous.json and the data backup it names), and a short
  `jarvis doctor` summary (offline checks only, no --live).
  It does not download the installer, does not write files, does not touch the registry or the PATH.
  The signature of latest.json is NOT verified here; the app verifies it before any install.

  .\owner_upgrade_check.ps1 [-ManifestUrl <url>] [-StateHome <state folder>] [-NoDoctor]
#>
[CmdletBinding()]
param(
    [string]$ManifestUrl = 'https://github.com/igalgerman/jarvis-releases/releases/latest/download/latest.json',
    [string]$StateHome = $(if ($env:JARVES_HOME) { $env:JARVES_HOME } else { Join-Path $env:LOCALAPPDATA 'jarves' }),
    [switch]$NoDoctor
)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Continue'

function Say([string]$k, [string]$v) { '{0,-22} {1}' -f $k, $v }
function ToVer([string]$s) {
    $m = [regex]::Match([string]$s, '\d+(\.\d+){0,3}')
    if (-not $m.Success) { return $null }
    $p = $m.Value.Split('.'); while ($p.Count -lt 3) { $p += '0' }
    try { return [version]($p -join '.') } catch { return $null }
}

'JARVES upgrade check (read only)'
'--------------------------------'

# 1. installed version: per-user Inno Setup uninstall entry
$inst = $null
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{24F7B79A-973C-462D-BA26-896D8EA30913}_is1'
try {
    $e = Get-ItemProperty -Path $key -ErrorAction Stop
    $inst = [string]$e.DisplayVersion
    Say 'Installed version' $inst
    Say 'Install folder' ([string]$e.InstallLocation)
} catch { Say 'Installed version' 'not found (JARVES is not installed for this Windows user)' }

# 2. jarvis --version
$line = $null
$cli = Get-Command jarvis -ErrorAction SilentlyContinue
if ($cli) {
    $line = (& jarvis --version 2>&1 | Select-Object -First 1)
    Say 'jarvis --version' ([string]$line)
} else { Say 'jarvis --version' 'the jarvis command is not on PATH' }

# 3. latest published version
$latest = $null
try {
    $r = Invoke-RestMethod -Uri $ManifestUrl -Method Get -TimeoutSec 20 -UseBasicParsing
    $latest = [string]$r.manifest.version
    Say 'Latest published' $latest
    Say 'Latest built' ([string]$r.manifest.built)
} catch { Say 'Latest published' ('could not read latest.json (' + $_.Exception.Message + ')') }

# 4. update available?
if (-not $inst -and $line) { $inst = [string]$line }
$a = ToVer $inst; $b = ToVer $latest
if ($a -and $b) { Say 'Update available' $(if ($b -gt $a) { "YES ($inst -> $latest)" } else { 'no' }) }
else { Say 'Update available' 'unknown' }

# 5. rollback backup
$prev = Join-Path $StateHome 'updates\previous\previous.json'
if (Test-Path -LiteralPath $prev) {
    try {
        $p = Get-Content -LiteralPath $prev -Raw | ConvertFrom-Json
        Say 'Rollback goes to' ([string]$p.version)
        $bak = [string]$p.backup
        Say 'Data backup' $(if ($bak -and (Test-Path -LiteralPath $bak)) { $bak } elseif ($bak) { "$bak (file missing)" } else { 'none recorded' })
        $inst2 = [string]$p.installer
        Say 'Old installer kept' $(if ($inst2 -and (Test-Path -LiteralPath (Join-Path (Split-Path $prev) $inst2))) { 'yes' } else { 'no (downloaded on demand)' })
    } catch { Say 'Rollback' 'previous.json is unreadable' }
} else {
    Say 'Rollback' 'nothing kept yet (no update was installed by the app)'
    $bk = Join-Path $StateHome 'backups'
    if (Test-Path -LiteralPath $bk) {
        $n = @(Get-ChildItem -LiteralPath $bk -Filter *.zip -ErrorAction SilentlyContinue).Count
        Say 'Backups folder' "$bk ($n zip files)"
    }
}

# 6. doctor summary
if (-not $NoDoctor -and $cli) {
    ''
    'jarvis doctor:'
    $out = @(& jarvis doctor 2>&1 | ForEach-Object { [string]$_ })
    $ok = @($out | Where-Object { $_ -match '^\[ OK \]' }).Count
    $warn = @($out | Where-Object { $_ -match '^\[WARN\]' })
    $fail = @($out | Where-Object { $_ -match '^\[FAIL\]' })
    Say 'OK / WARN / FAIL' ("{0} / {1} / {2}" -f $ok, $warn.Count, $fail.Count)
    foreach ($l in ($fail + $warn)) { '  ' + $l }
}
''
'Nothing was installed or changed.'
