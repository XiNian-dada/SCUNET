$ErrorActionPreference = "Stop"

$TaskName = "SCUNETAutologin"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Windows scheduled task removed."
}
else {
    Write-Host "Windows scheduled task not found."
}

Write-Host "Config and password were left in:"
Write-Host "  $env:APPDATA\SCUNETAutologin"

