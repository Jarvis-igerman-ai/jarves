param(
    [switch]$Install,
    [switch]$Remove,
    [string]$PythonExe = "python",
    [string]$WorkflowHome = "$env:LOCALAPPDATA\jarves\workflow",
    [string]$ProfileHome = "$env:LOCALAPPDATA\jarves",
    [int]$Port = 8188
)
$ErrorActionPreference = "Stop"
$taskName = "JARVES Workflow Observer"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed workflow startup task; private data and worker sessions were kept."
    exit 0
}
$python = (Get-Command $PythonExe -ErrorAction Stop).Source
if ($python -like "*\WindowsApps\*") {
    throw "Resolved interpreter '$python' is the Windows App Execution Alias (a launcher stub), not a real python.exe. Pass -PythonExe with the full path to a real python.exe, e.g. the output of: py -3.12 -c 'import sys;print(sys.executable)'"
}
$entry = Join-Path $PSScriptRoot "workflow_console.py"
if (-not (Test-Path $entry)) { throw "Workflow entry point not found." }
if ($Port -lt 1 -or $Port -gt 65535) { throw "A fixed port is required." }
if ($Install) {
    # Values are local installation paths, never prompts, answers or secrets.
    foreach ($value in @($entry, $WorkflowHome, $ProfileHome)) {
        if ($value.Contains('"')) { throw "Installation paths may not contain a quote." }
    }
    $arguments = '"' + $entry + '" supervise --home "' + $WorkflowHome + '" --profile-home "' + $ProfileHome + '" --port ' + $Port
    # Use pythonw.exe beside python.exe so logon starts no console window.
    $pythonw = Join-Path (Split-Path $python) "pythonw.exe"
    if (Test-Path $pythonw) { $python = $pythonw }
    # Restart layer (#754): a 5-minute repeat re-launches a dead supervisor; IgnoreNew keeps a healthy one untouched.
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($trigger, $repeat) -Settings $settings -Description "External JARVES workflow observer; no approval/merge/release authority" -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output "Installed per-user external observer. Run python core/tools/workflow_console.py open to open the private view."
} else {
    & $python $entry supervise --home $WorkflowHome --profile-home $ProfileHome --port $Port
    exit $LASTEXITCODE
}
