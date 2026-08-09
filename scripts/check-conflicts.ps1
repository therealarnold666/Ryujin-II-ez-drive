param([switch]$Fix)
$ErrorActionPreference = 'Stop'
$names = @(
    'aida64','ArmouryCrate','ArmouryCrate.UserSessionHelper',
    'ArmouryHtmlDebugServer','ArmourySocketServer','asus_framework'
)

Write-Host '== Ryujin II device ==' -ForegroundColor Cyan
Get-PnpDevice -PresentOnly | Where-Object InstanceId -Match 'VID_0B05&PID_1988' |
    Format-Table Status,Class,FriendlyName,InstanceId -AutoSize

Write-Host '== Possible LCD controllers ==' -ForegroundColor Cyan
$running = Get-Process -Name $names -ErrorAction SilentlyContinue
if ($running) { $running | Format-Table ProcessName,Id,Path -AutoSize }
else { Write-Host 'No known competing controller is running.' -ForegroundColor Green }

if (-not $Fix) {
    Write-Host 'Read-only check complete. Re-run with -Fix to stop only the listed LCD/UI processes.' -ForegroundColor Yellow
    exit 0
}

Write-Host 'Stopping known LCD/UI controllers. Pump firmware keeps its current safety curve.' -ForegroundColor Yellow
Stop-Service -Name ArmouryCrateService -Force -ErrorAction SilentlyContinue
Get-Process -Name $names -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$left = Get-Process -Name $names -ErrorAction SilentlyContinue
if ($left) {
    $left | Format-Table ProcessName,Id -AutoSize
    throw 'Some controller processes restarted. Disable their auto-start or run this script elevated.'
}
Write-Host 'LCD controller conflicts cleared.' -ForegroundColor Green
Write-Host 'To restore Armoury after stopping ryujin-lcd: Start-Service ArmouryCrateService' -ForegroundColor DarkGray

