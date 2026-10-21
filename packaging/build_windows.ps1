<#
Build the JARVES Windows installer (SW-V2 M6). Same script locally and in CI (.github/workflows/jarves-checks.yml).

Layout of the installed program folder (per user, %LOCALAPPDATA%\Programs\JARVES, no admin):
  jarvis.cmd            launcher (python -I -m jarves), sets JARVES_FACE_EXE
  python\               python-build-standalone CPython + the locked wheels from core/uv.lock + the jarves wheel
  face\                 Godot face/room exported with the official 4.7.2 release template (+ raw GLB the room loads)
User data is NOT here: it stays in %LOCALAPPDATA%\jarves (state_dir), so upgrades/uninstall keep it.

Every download is pinned by URL + SHA256 (checked 2026-10-19 via the GitHub releases API). Nothing from the
developer's machine is copied except the repository sources; packaging/payload_check.py fails the build if a
secret, token, database or log file is staged.

  pwsh packaging\build_windows.ps1 [-Version 0.1.0.57] [-Channel main|pr|dev] [-Commit <sha>] [-Out dist]
#>
param(
    [string]$Version = "",
    [string]$Channel = "dev",
    [string]$Commit = "",
    [string]$Cache = (Join-Path ([IO.Path]::GetTempPath()) "jarves-build-cache"),
    [string]$Out = "",
    [string]$GoogleOAuthClientFile = "",  # explicitly supplied app-owned Desktop client; never private user state
    [switch]$SkipFace,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Repo = Split-Path $PSScriptRoot -Parent
if (-not $Out) { $Out = Join-Path $Repo "dist" }
$Out = [IO.Path]::GetFullPath($Out)

$GH = "https://github.com"
$Pins = @{
    python    = @{ file = "cpython-3.12.15+20261013-x86_64-pc-windows-msvc-install_only.tar.gz"
                   url = "$GH/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-x86_64-pc-windows-msvc-install_only.tar.gz"
                   sha256 = "bbc902d4d187ac4914915bf538196a064b49109a33a104965b5164ced5b80a1e" }
    godot     = @{ file = "Godot_v4.7.2-stable_win64.exe.zip"
                   url = "$GH/godotengine/godot/releases/download/4.7.2-stable/Godot_v4.7.2-stable_win64.exe.zip"
                   sha256 = "731980f9608d61333e5baf54a2ef17210acc7a538446c0cb9969f002aca1e953" }
    templates = @{ file = "Godot_v4.7.2-stable_export_templates.tpz"
                   url = "$GH/godotengine/godot/releases/download/4.7.2-stable/Godot_v4.7.2-stable_export_templates.tpz"
                   sha256 = "f298490b8d44d934be425a5a65a51bf15f422428b229a06a6e11d9ffea248011" }
    inno      = @{ file = "innosetup-7.1.0-x64.exe"
                   url = "$GH/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe"
                   sha256 = "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f" }
}

function Step($m) { Write-Host "==> $m" }
function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "$exe failed with exit code $LASTEXITCODE" }
}
function Get-Pinned([string]$name) {
    $p = $Pins[$name]
    $dir = Join-Path $Cache "dl"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $f = Join-Path $dir $p.file
    if (-not (Test-Path $f) -or (Get-FileHash $f -Algorithm SHA256).Hash.ToLower() -ne $p.sha256) {
        Step "download $($p.file)"
        & curl.exe -sSL --fail --retry 3 -o $f $p.url
        if ($LASTEXITCODE -ne 0) { throw "download failed: $($p.url)" }
        $h = (Get-FileHash $f -Algorithm SHA256).Hash.ToLower()
        if ($h -ne $p.sha256) { Remove-Item $f; throw "SHA256 mismatch for $($p.file): $h" }
    }
    return $f
}

# ---------------------------------------------------------------- identity
$pyproject = Get-Content (Join-Path $Repo "core\pyproject.toml") -Raw
$base = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $Version) { $Version = "$base.0" }
if ($Version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw "Version must be four numbers (got $Version)" }
if (-not $Commit) { $Commit = (& git -C $Repo rev-parse HEAD).Trim() }
$BuildClock = [Diagnostics.Stopwatch]::StartNew()      # SW-V4 L: build_s for the installer budget
$Built = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
Step "JARVES $Version ($Channel, $Commit)"

$Stage = Join-Path $Out "stage"
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force $Stage | Out-Null

# ---------------------------------------------------------------- Python + locked dependencies
Step "python-build-standalone"
Run "tar.exe" @("-xzf", (Get-Pinned "python"), "-C", $Stage)          # -> $Stage\python\python.exe
$Py = Join-Path $Stage "python\python.exe"
$req = Join-Path $Out "requirements.txt"
Push-Location (Join-Path $Repo "core")
try {
    Step "locked wheels (uv.lock, extras voice + camera + tray + browser, no dev)"
    Run "uv" @("export", "--frozen", "--no-dev", "--extra", "voice", "--extra", "camera", "--extra", "tray", "--extra", "browser", "--no-emit-project",
               "--format", "requirements-txt", "-o", $req)
    Run "uv" @("pip", "install", "--python", $Py, "--break-system-packages", "--require-hashes", "--compile-bytecode",
               "-r", $req)
    Step "log-code index for root-cause hints (SW-V4 O: git blame at build time, never at runtime)"
    Run "uv" @("run", "--frozen", "--no-dev", "python", "tools/build_log_index.py")
    Step "jarves wheel"
    Run "uv" @("build", "--wheel", "--out-dir", (Join-Path $Out "wheel"))
    $whl = Get-ChildItem (Join-Path $Out "wheel") -Filter "jarves-*.whl" | Select-Object -First 1
    Run "uv" @("pip", "install", "--python", $Py, "--break-system-packages", "--no-deps", "--compile-bytecode", $whl.FullName)
} finally { Pop-Location }
# SW-V4 P (installer size): the stdlib's own test suite, IDLE, the turtle demos and ensurepip are never used by JARVES
# (the helper Skill uses the PC's Python, not this one); tkinter and its tcl/tk stay (the approval popup).
Step "prune unused stdlib parts"
foreach ($d in "Lib\test", "Lib\idlelib", "Lib\turtledemo", "Lib\ensurepip", "Lib\lib2to3") {
    $full = Join-Path $Stage ("python\" + $d)
    if (Test-Path $full) { Remove-Item -Recurse -Force $full }
}
$info = [ordered]@{ version = $Version; commit = $Commit; built = $Built; channel = $Channel; platform = "windows-x86_64"
                    python = "3.12.15+20261013"; godot = "4.7.2-stable" }
# WP-O: default update source baked into the build (repo variable JARVES_UPDATE_BASE_URL, public, not a secret);
# a PC's config.toml [update] base_url still wins. Empty = GitHub releases.
if ($env:JARVES_UPDATE_BASE_URL) { $info["update_base_url"] = $env:JARVES_UPDATE_BASE_URL.Trim().TrimEnd("/") }
$info | ConvertTo-Json | Set-Content -Encoding utf8NoBOM (Join-Path $Stage "python\Lib\site-packages\jarves\_build_info.json")
if ($GoogleOAuthClientFile) {
    Step "app-owned Google Desktop OAuth client (public-client configuration, no user tokens)"
    $googleConfig = Get-Content -LiteralPath $GoogleOAuthClientFile -Raw | ConvertFrom-Json
    $googleClient = $googleConfig.installed
    if (-not $googleClient -or $googleClient.client_id -isnot [string] -or
        $googleClient.client_id -notmatch '^[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$' -or
        ($googleClient.client_secret -and $googleClient.client_secret -isnot [string])) {
        throw "GoogleOAuthClientFile must be an app-owned Desktop OAuth client JSON, not a web/service account client"
    }
    # Desktop apps are public clients (Google native-app OAuth docs). Ship only these registration values;
    # never copy arbitrary JSON, tokens, accounts or the developer's private state directory into a release.
    @{ installed = @{ client_id = $googleClient.client_id; client_secret = [string]$googleClient.client_secret } } |
        ConvertTo-Json | Set-Content -Encoding utf8NoBOM (Join-Path $Stage "python\Lib\site-packages\jarves\_google_app_client.json")
}
Run $Py @("-I", "-c", "import jarves.cli, jarves.updates; print(jarves.updates.version_line())")

# ---------------------------------------------------------------- Godot face (headless export, official template)
if (-not $SkipFace) {
    $gdir = Join-Path $Cache "godot-4.7.2"
    $editor = Join-Path $gdir "Godot_v4.7.2-stable_win64_console.exe"
    if (-not (Test-Path $editor)) {
        Step "Godot editor"
        Expand-Archive (Get-Pinned "godot") -DestinationPath $gdir -Force
    }
    $tdir = Join-Path $Cache "godot-templates-4.7.2"
    $tpl = Join-Path $tdir "windows_release_x86_64.exe"
    if (-not (Test-Path $tpl)) {
        Step "Godot export template (only the Windows release binaries are kept from the .tpz)"
        New-Item -ItemType Directory -Force $tdir | Out-Null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead((Get-Pinned "templates"))
        try {
            foreach ($n in @("windows_release_x86_64.exe", "windows_release_x86_64_console.exe")) {
                $e = $zip.Entries | Where-Object { $_.FullName -eq "templates/$n" }
                if (-not $e) { throw "template $n not in the .tpz" }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($e, (Join-Path $tdir $n), $true)
            }
        } finally { $zip.Dispose() }
        Remove-Item (Join-Path $Cache "dl\$($Pins.templates.file)") -ErrorAction SilentlyContinue   # 1.3 GB
    }
    $godotProj = Join-Path $Repo "godot"
    $presets = Join-Path $godotProj "export_presets.cfg"                 # gitignored; generated per build
    (Get-Content (Join-Path $PSScriptRoot "windows\face_export_presets.cfg") -Raw).
        Replace("@TEMPLATE@", ($tpl -replace '\\', '/')).Replace("@VERSION@", $Version) |
        Set-Content -Encoding utf8NoBOM $presets
    $faceDir = Join-Path $Stage "face"
    New-Item -ItemType Directory -Force $faceDir | Out-Null
    try {
        Step "Godot import + export"
        & $editor --headless --path $godotProj --import 2>&1 | Out-Host
        & $editor --headless --path $godotProj --export-release "Windows Desktop" (Join-Path $faceDir "JARVES Face.exe") 2>&1 | Out-Host
    } finally { Remove-Item $presets -ErrorAction SilentlyContinue }
    foreach ($f in @("JARVES Face.exe", "JARVES Face.pck")) {
        if (-not (Test-Path (Join-Path $faceDir $f))) { throw "Godot export did not produce $f" }
    }
    # main.gd loads the room model with ProjectSettings.globalize_path(), which in an exported build points next
    # to the executable, so the raw GLB ships there.
    # Release templates refuse a scene path on the command line ("compiled without support for path overrides"),
    # and project.godot starts the review viewer. override.cfg next to the .exe (read by exported builds) makes the
    # installed face start at the loading screen, like the developer `jarvis` (res://loading.tscn).
    "[application]`n`nrun/main_scene=`"res://loading.tscn`"`n" | Set-Content -Encoding utf8NoBOM (Join-Path $faceDir "override.cfg")
    New-Item -ItemType Directory -Force (Join-Path $faceDir "assets") | Out-Null
    Copy-Item (Join-Path $godotProj "assets\JARVIS.glb") (Join-Path $faceDir "assets\JARVIS.glb")
}

# ---------------------------------------------------------------- WhatsApp bridge (Go), only when the repo pins one
# packaging\bridge_pin.json = {"path": "bridge/jarves-whatsapp-bridge.exe", "sha256": "<64 hex>"} appears with the bridge
# (package F). CI builds the binary (go build, Windows amd64) and passes it in JARVES_BRIDGE_EXE; payload_check.py below
# fails the build unless the staged file has exactly the pinned SHA256. No pin file = the bridge is not part of this build.
$bridgePin = Join-Path $PSScriptRoot "bridge_pin.json"
if (Test-Path $bridgePin) {
    $pin = Get-Content $bridgePin -Raw | ConvertFrom-Json
    if (-not $env:JARVES_BRIDGE_EXE -or -not (Test-Path $env:JARVES_BRIDGE_EXE)) {
        throw "packaging\bridge_pin.json exists but JARVES_BRIDGE_EXE (the CI-built Go binary) is not set or missing"
    }
    $dst = Join-Path $Stage ($pin.path -replace '/', '\')
    New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
    Copy-Item $env:JARVES_BRIDGE_EXE $dst
    Step "WhatsApp bridge staged: $($pin.path)"
}

# ---------------------------------------------------------------- launcher, notices, payload check
Copy-Item (Join-Path $PSScriptRoot "windows\jarvis.cmd") $Stage
Copy-Item (Join-Path $PSScriptRoot "windows\THIRD_PARTY_NOTICES.txt") $Stage
# SW-V4 F: the WhatsApp bridge binary (built by scripts/build_whatsapp_bridge.ps1) ships next to the Core when it was
# built; payload_check.py verifies its .sha256. Its state (tokens, store) lives in the user's data dir, never here.
$bridgeExe = Join-Path $Repo "bridge\whatsapp\dist\whatsapp_bridge.exe"
if ((Test-Path $bridgeExe) -and (Test-Path "$bridgeExe.sha256")) {
    Step "WhatsApp bridge binary -> bridge\"
    New-Item -ItemType Directory -Force (Join-Path $Stage "bridge") | Out-Null
    Copy-Item $bridgeExe, "$bridgeExe.sha256" (Join-Path $Stage "bridge")
} else {
    Step "WhatsApp bridge not built (run scripts/build_whatsapp_bridge.ps1 first to include it); skipping"
}
Step "payload check (no secrets, tokens, personal databases or logs)"
Run $Py @("-I", (Join-Path $PSScriptRoot "payload_check.py"), $Stage)

# ---------------------------------------------------------------- installer
if (-not $SkipInstaller) {
    $iscc = Join-Path $Cache "inno-7.1.0\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        Step "Inno Setup 7.1.0 (portable, current user)"
        $p = Start-Process (Get-Pinned "inno") -Wait -PassThru -ArgumentList @(
            "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/CURRENTUSER", "/PORTABLE=1",
            "/DIR=`"$(Join-Path $Cache 'inno-7.1.0')`"")
        if ($p.ExitCode -ne 0 -or -not (Test-Path $iscc)) { throw "Inno Setup install failed ($($p.ExitCode))" }
    }
    Step "ISCC"
    Run $iscc @("-q", "/DAppVersion=$Version", "/DStage=$Stage", "/DOutDir=$Out", "/DCommit=$Commit",
                (Join-Path $PSScriptRoot "windows\jarves.iss"))
    $exe = Join-Path $Out "JARVES-Setup-$Version.exe"
    if (-not (Test-Path $exe)) { throw "ISCC produced no $exe" }
    $sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
    "$sha  JARVES-Setup-$Version.exe" | Set-Content -Encoding ascii "$exe.sha256"
    $info["installer"] = "JARVES-Setup-$Version.exe"
    $info["sha256"] = $sha
    $info["size"] = (Get-Item $exe).Length
    $info["build_s"] = [math]::Round($BuildClock.Elapsed.TotalSeconds, 1)
    $info | ConvertTo-Json | Set-Content -Encoding utf8NoBOM (Join-Path $Out "build-info.json")
    Step "done: $exe ($([math]::Round($info.size / 1MB)) MB, sha256 $sha)"
}
