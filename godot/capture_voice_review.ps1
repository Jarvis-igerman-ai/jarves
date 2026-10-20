param([Parameter(Mandatory=$true)][string]$GodotExe)
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$outDir = Join-Path $project 'previews/voice_review'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$views = @(
 @{name='room'; scene=''; flags=@('--room')},
 @{name='front'; scene=''; flags=@('--no-core','--hide-ui','--view=front')},
 @{name='side'; scene=''; flags=@('--no-core','--hide-ui','--view=side')},
 @{name='rear'; scene=''; flags=@('--no-core','--hide-ui','--view=rear')},
 @{name='three_quarter'; scene=''; flags=@('--no-core','--hide-ui','--view=three_quarter')},
 @{name='internals'; scene=''; flags=@('--no-core','--hide-ui','--view=three_quarter','--hide-shell')},
 @{name='loading'; scene='res://loading.tscn'; flags=@()}
)
foreach ($view in $views) {
 $shot = Join-Path $outDir ($view.name+'.png')
 $arguments = @('--path', ('"'+$project+'"'), '--rendering-method', 'gl_compatibility')
 if ($view.scene) { $arguments += $view.scene }
 $arguments += '--'
 $arguments += $view.flags
 $arguments += $(if ($view.name -eq 'loading') { '--loading-shot='+$shot } else { '--shot='+$shot })
 $capture = Start-Process -FilePath $GodotExe -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
 if ($capture.ExitCode -ne 0 -or !(Test-Path -LiteralPath $shot) -or (Get-Item -LiteralPath $shot).Length -eq 0) { throw ('Capture failed: '+$view.name) }
 Write-Output ($view.name+': '+(Get-Item -LiteralPath $shot).Length+' bytes')
}
$sourceFiles = @('main.gd','loading.gd','face/assistant_ui.gd','face/face.gd','room/room.gd','assets/JARVIS.glb','assets/finishes.json')
$sourceHashes = [ordered]@{}
foreach ($relative in $sourceFiles) { $sourceHashes[$relative] = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes((Get-Content -Raw -LiteralPath (Join-Path $project $relative)).Replace([string][char]13+[char]10,[string][char]10)))) }
$imageHashes = [ordered]@{}
foreach ($view in $views) { $imageHashes[$view.name+'.png'] = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $outDir ($view.name+'.png'))).Hash }
[ordered]@{method='actual Godot GL compatibility, hidden window; no live audio/API'; source_line_endings='LF'; source_sha256=$sourceHashes; image_sha256=$imageHashes} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $outDir 'evidence.json') -Encoding utf8
