#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path $env:LOCALAPPDATA "RyujinLCD\backups\armoury-module-removal-$stamp"
$resultPath = Join-Path $env:LOCALAPPDATA 'RyujinLCD\last-module-removal.json'
$deviceSetup = 'C:\Program Files (x86)\InstallShield Installation Information\{13c0dd93-7394-42ce-be75-11bc39c5641b}\Setup.exe'
$halBundle = 'C:\ProgramData\Package Cache\{09004552-978f-4fc2-8460-c8daa6e5c73c}\AacAIOFanSetup.exe'
$halProduct = '{EAE80DED-1A39-41C5-9F60-87CC947F6454}'

function Copy-BackupItem([string]$Path, [string]$Name) {
    if (Test-Path -LiteralPath $Path) {
        Copy-Item -LiteralPath $Path -Destination (Join-Path $backupRoot $Name) -Recurse -Force
    }
}

function Wait-Installer([System.Diagnostics.Process]$Process, [int]$Seconds, [string]$Name) {
    if (-not $Process.WaitForExit($Seconds * 1000)) {
        $Process.Kill()
        throw "$Name did not finish within $Seconds seconds."
    }
    if ($Process.ExitCode -notin @(0, 1605, 1614, 3010)) {
        throw "$Name failed with exit code $($Process.ExitCode)."
    }
}

$result = [ordered]@{
    startedAt = (Get-Date).ToString('o')
    success = $false
    backup = $backupRoot
    deviceModuleRemoved = $false
    halRemoved = $false
    error = $null
}

try {
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Copy-BackupItem 'C:\Program Files (x86)\ASUS\ArmouryDevice\View\6536' 'View-6536'
    Copy-BackupItem 'C:\Program Files\ASUS\Aac_AIOFan' 'Aac_AIOFan'
    Copy-BackupItem 'C:\ProgramData\ASUS\ArmourySDK\AIOFan\RYUJIN2' 'RYUJIN2-profile'
    Copy-BackupItem 'C:\ProgramData\ASUS\ROG Live Service\deviceinfo.ini' 'deviceinfo.ini'
    Copy-BackupItem 'C:\ProgramData\ASUS\ROG Live Service\DeviceDependentVersion\ROG RYUJIN II' 'DeviceDependentVersion'

    Stop-Service -Name ArmouryCrateService -Force -ErrorAction SilentlyContinue
    Get-Process -Name aida64,RyujinLCD,ArmouryCrate,ArmouryCrate.UserSessionHelper,ArmouryHtmlDebugServer,ArmourySocketServer,ArmouryCrateControlInterface,asus_framework -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    if (Test-Path -LiteralPath $deviceSetup) {
        $deviceProcess = Start-Process -FilePath $deviceSetup -ArgumentList '-uninstall','-s' -PassThru
        Wait-Installer $deviceProcess 120 'ROG RYUJIN II device module uninstaller'
        Start-Sleep -Seconds 3
    }

    if (Test-Path -LiteralPath $halBundle) {
        $halProcess = Start-Process -FilePath $halBundle -ArgumentList '/uninstall','/quiet','/norestart' -PassThru
        Wait-Installer $halProcess 120 'ASUS AIOFan HAL bundle uninstaller'
        Start-Sleep -Seconds 3
    }

    $halKey = "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$halProduct"
    if (Test-Path -LiteralPath $halKey) {
        $msiProcess = Start-Process -FilePath 'msiexec.exe' -ArgumentList '/x',$halProduct,'/qn','/norestart' -PassThru
        Wait-Installer $msiProcess 120 'ASUS AIOFan HAL MSI uninstaller'
        Start-Sleep -Seconds 2
    }

    $deviceKey = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{13c0dd93-7394-42ce-be75-11bc39c5641b}'
    $result.deviceModuleRemoved = -not (Test-Path -LiteralPath $deviceKey) -and -not (Test-Path -LiteralPath 'C:\Program Files (x86)\ASUS\ArmouryDevice\View\6536')
    $result.halRemoved = -not (Test-Path -LiteralPath $halKey) -and -not (Test-Path -LiteralPath 'C:\Program Files\ASUS\Aac_AIOFan\AacAIOFanHal_x64.dll')
    if (-not $result.deviceModuleRemoved -or -not $result.halRemoved) {
        throw "Uninstall verification failed. deviceModuleRemoved=$($result.deviceModuleRemoved), halRemoved=$($result.halRemoved)"
    }
    $result.success = $true
}
catch {
    $result.error = $_.Exception.Message
}
finally {
    Start-Service -Name ArmouryCrateService -ErrorAction SilentlyContinue
    $result.completedAt = (Get-Date).ToString('o')
    New-Item -ItemType Directory -Path (Split-Path -Parent $resultPath) -Force | Out-Null
    $result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $resultPath -Encoding UTF8
}

if (-not $result.success) { throw $result.error }
Start-Process 'asusac:'
