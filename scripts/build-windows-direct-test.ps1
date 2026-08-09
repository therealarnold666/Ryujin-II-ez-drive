$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $projectRoot 'native\ryujin_direct_test.cpp'
$output = Join-Path $projectRoot 'native\ryujin_direct_test.exe'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'

if (-not (Test-Path -LiteralPath $vswhere)) {
    throw 'Visual Studio Build Tools with Desktop development with C++ is required.'
}
$install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $install) { throw 'MSVC x64 build tools were not found.' }
$devShell = Join-Path $install 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll'
Import-Module $devShell
Enter-VsDevShell -VsInstallPath $install -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64'

& cl.exe /nologo /std:c++17 /EHsc /O2 /W4 $source /Fe:$output setupapi.lib hid.lib winusb.lib ole32.lib
if ($LASTEXITCODE -ne 0) { throw "MSVC failed with exit code $LASTEXITCODE" }
Write-Host "Built x64 direct Ryujin test: $output" -ForegroundColor Green
