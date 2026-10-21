<#
.SYNOPSIS
  Build the JARVES WhatsApp bridge (bridge/whatsapp) reproducibly: Windows amd64, pure Go (CGO_ENABLED=0).

.DESCRIPTION
  1. checks that `go version` is exactly the pinned toolchain (go1.27.0),
  2. go mod verify, go vet ./..., go test ./... (offline; no WhatsApp account is ever contacted),
  3. go build -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X main.version=<v>"
     -> bridge/whatsapp/dist/whatsapp_bridge.exe (+ .sha256 and build-info.json).
  -Verify <sha256 file> rebuilds into a temporary directory (no vet/test) and fails unless the SHA-256 is identical
  (reproducibility check).

.EXAMPLE
  ./scripts/build_whatsapp_bridge.ps1 -Version 0.1.0
  ./scripts/build_whatsapp_bridge.ps1 -Version 0.1.0 -Verify bridge/whatsapp/dist/whatsapp_bridge.exe.sha256
#>
param(
  [string]$Version = "0.1.0-dev",
  [string]$Verify = ""
)
$ErrorActionPreference = "Stop"
$PinnedGo = "go1.27.0"
$Root = Split-Path -Parent $PSScriptRoot
$Mod = Join-Path $Root "bridge/whatsapp"
$Dist = Join-Path $Mod "dist"

function Fail([string]$msg) { Write-Host "BUILD FAILED: $msg" -ForegroundColor Red; exit 1 }
if ($Verify -and -not [System.IO.Path]::IsPathRooted($Verify)) { $Verify = Join-Path (Get-Location).Path $Verify }

# Reproducible, offline-friendly environment: never auto-download another toolchain, never use cgo.
$env:GOTOOLCHAIN = "local"
$env:CGO_ENABLED = "0"
$env:GOOS = "windows"
$env:GOARCH = "amd64"
$env:GOFLAGS = "-mod=readonly"
$env:GOAMD64 = "v1"

$goCmd = Get-Command go -ErrorAction SilentlyContinue
if (-not $goCmd) {
  Fail "Go is not installed. Next action: install $PinnedGo from https://go.dev/dl/ (go1.27.0.windows-amd64.msi) and re-run."
}
$gv = (& go version) -join " "
if ($gv -notmatch "^go version (go[0-9.]+(rc[0-9]+)?) ") { Fail "cannot parse '$gv'." }
if ($Matches[1] -ne $PinnedGo) {
  Fail "found $($Matches[1]), the bridge is pinned to $PinnedGo. Next action: install $PinnedGo from https://go.dev/dl/ (or put it first on PATH) and re-run."
}

Push-Location $Mod
try {
  $whatsmeow = (& go list -m -f "{{.Version}}" go.mau.fi/whatsmeow)
  if ($LASTEXITCODE -ne 0) { Fail "go list -m failed." }
  $commit = ""
  try { $commit = (& git -C $Root rev-parse HEAD 2>$null) } catch { }
  if (-not $commit) { $commit = "unknown" }

  if (-not $Verify) {
    Write-Host "== go mod verify"; & go mod verify; if ($LASTEXITCODE -ne 0) { Fail "go mod verify" }
    Write-Host "== go vet ./...";  & go vet ./...;  if ($LASTEXITCODE -ne 0) { Fail "go vet" }
    Write-Host "== go test ./... (serial packages, offline)"
    & go test -p 1 -count=1 ./...; if ($LASTEXITCODE -ne 0) { Fail "go test" }
    $out = $Dist
  } else {
    $out = Join-Path ([System.IO.Path]::GetTempPath()) ("wab-verify-" + [guid]::NewGuid().ToString("N"))
  }
  New-Item -ItemType Directory -Force $out | Out-Null
  $exe = Join-Path $out "whatsapp_bridge.exe"
  Write-Host "== go build ($PinnedGo, whatsmeow $whatsmeow, version $Version)"
  & go build -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X main.version=$Version" -o $exe ./cmd/whatsapp_bridge
  if ($LASTEXITCODE -ne 0) { Fail "go build" }
  $sha = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLowerInvariant()

  if ($Verify) {
    if (-not (Test-Path $Verify)) { Fail "no such sha256 file: $Verify" }
    $want = ((Get-Content -Raw $Verify).Trim() -split "\s+")[0].ToLowerInvariant()
    Remove-Item -Recurse -Force $out
    if ($want -ne $sha) { Fail "NOT reproducible: rebuilt $sha, expected $want." }
    Write-Host "REPRODUCIBLE: $sha"
    exit 0
  }

  Set-Content -NoNewline -Encoding ascii -Path "$exe.sha256" -Value "$sha  whatsapp_bridge.exe`n"
  $info = [ordered]@{
    name = "whatsapp_bridge"; version = $Version; go = $PinnedGo; whatsmeow = $whatsmeow; commit = $commit
    goos = "windows"; goarch = "amd64"; cgo = 0; sha256 = $sha
    flags = "-trimpath -buildvcs=false -ldflags '-s -w -buildid= -X main.version=$Version'"
  }
  $info | ConvertTo-Json | Set-Content -Encoding utf8 -Path (Join-Path $out "build-info.json")
  Write-Host "OK: $exe"
  Write-Host "SHA256: $sha"
} finally {
  Pop-Location
}
