$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "SCUNETAutologin"
$AppDir = Join-Path $env:APPDATA "SCUNETAutologin"
$ScriptTarget = Join-Path $AppDir "scunet_autologin.py"
$ConfigTarget = Join-Path $AppDir "config.json"

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    $PythonCommand = Get-Command py -ErrorAction SilentlyContinue
}

if (-not $PythonCommand) {
    throw "python or py was not found in PATH."
}

New-Item -ItemType Directory -Path $AppDir -Force | Out-Null
Copy-Item (Join-Path $ScriptDir "scunet_autologin.py") $ScriptTarget -Force

if (-not (Test-Path $ConfigTarget)) {
    Copy-Item (Join-Path $ScriptDir "config.example.json") $ConfigTarget -Force
    Write-Host "Created config template at:"
    Write-Host "  $ConfigTarget"
}

$PythonExe = $PythonCommand.Source
$Arguments = "`"$ScriptTarget`" --config `"$ConfigTarget`""
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "SCUNET auto-login helper" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Windows logon task installed."
Write-Host "Config file:"
Write-Host "  $ConfigTarget"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit $ConfigTarget"
Write-Host "  2. Set password_source to env, file, or command"
Write-Host "  3. Check the task in Task Scheduler with name $TaskName"
