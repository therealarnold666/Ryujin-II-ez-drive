param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\RyujinLCD'),
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'Ryujin LCD AutoStart'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task: $taskName"
    exit 0
}

$exe = Join-Path $InstallDir 'RyujinLCD.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "RyujinLCD.exe was not found: $exe"
}

$userId = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction `
    -Execute $exe `
    -Argument '--auto-start --no-browser' `
    -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal `
    -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Start Ryujin II LCD dashboard and LCD driver at user logon.' `
    -Force | Out-Null

Write-Host "Installed scheduled task: $taskName"
