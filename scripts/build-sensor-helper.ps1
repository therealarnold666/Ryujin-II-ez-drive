$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'native\sensor_helper.cs'
$sensorDir = Join-Path $root 'native\sensors'
$output = Join-Path $sensorDir 'ryujin_sensor_helper.exe'
$reference = "/reference:$(Join-Path $sensorDir 'LibreHardwareMonitorLib.dll')"
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) { throw '.NET Framework 4.x C# compiler was not found.' }
& $csc /nologo /optimize+ /target:exe /platform:x64 "/out:$output" $reference $source
if ($LASTEXITCODE -ne 0) { throw "Sensor helper build failed with exit code $LASTEXITCODE" }
Write-Host "Built: $output" -ForegroundColor Green
