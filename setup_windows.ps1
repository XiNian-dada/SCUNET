$ErrorActionPreference = "Stop"

param(
    [Parameter(Mandatory = $true)]
    [string]$Username,
    [string]$Service = "EDUNET",
    [string]$Interface = "Wi-Fi"
)

function Normalize-Service {
    param([string]$Name)

    switch ($Name.ToLower()) {
        { $_ -in @("edunet", "campus", "campusnet", "scunet", "校园网") } { return "EDUNET" }
        { $_ -in @("telecom", "chinatelecom", "dianxin", "电信") } { return "CHINATELECOM" }
        { $_ -in @("mobile", "chinamobile", "yidong", "移动") } { return "CHINAMOBILE" }
        { $_ -in @("unicom", "chinaunicom", "liantong", "联通") } { return "CHINAUNICOM" }
        default { throw "Unsupported service: $Name" }
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $env:APPDATA "SCUNETAutologin"
$ConfigTarget = Join-Path $AppDir "config.json"
$PasswordTarget = Join-Path $AppDir "password.txt"
$NormalizedService = Normalize-Service $Service

New-Item -ItemType Directory -Path $AppDir -Force | Out-Null

$SecurePassword = Read-Host "SCUNET password" -AsSecureString
$PasswordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
try {
    $PlainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($PasswordPtr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPtr)
}

if ([string]::IsNullOrWhiteSpace($PlainPassword)) {
    throw "Password cannot be empty."
}

Set-Content -Path $PasswordTarget -Value $PlainPassword -NoNewline -Encoding UTF8

$Config = @{
    username        = $Username
    service         = $NormalizedService
    interface       = $Interface
    password_source = "file"
    password_file   = $PasswordTarget
}

$Config | ConvertTo-Json | Set-Content -Path $ConfigTarget -Encoding UTF8

Write-Host "Wrote Windows config:"
Write-Host "  $ConfigTarget"
Write-Host "Using service:"
Write-Host "  $NormalizedService"
Write-Host "Using interface:"
Write-Host "  $Interface"
Write-Host "Password file:"
Write-Host "  $PasswordTarget"
Write-Host ""

& (Join-Path $ScriptDir "install_windows_task.ps1")

Write-Host ""
Write-Host "SCUNET autologin is configured for Windows."

