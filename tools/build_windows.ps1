[CmdletBinding()]
param([string]$Python = '.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$appVersion = (Get-Content -LiteralPath 'VERSION' -Raw).Trim()
if ($appVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') { throw 'Invalid release version.' }
$releasePath = Join-Path 'dist\release' $appVersion
$packagePath = Join-Path 'dist\windows-package' $appVersion
if (!(Test-Path -LiteralPath 'models\dinov2-small\model.onnx')) { throw 'Run python tools/download_model.py before building the complete Windows app.' }
$expected = '6266c3cd72db6953cecdcbfeab9422a9f783d96f1a4e296ba70ffbac43b54a18'
if ((Get-FileHash -LiteralPath 'models\dinov2-small\model.onnx' -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Model checksum mismatch.' }
& $Python -m PyInstaller --noconfirm --distpath 'dist\windows' --workpath '.build\windows' Midoo.spec
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }
$iconPath = (Resolve-Path -LiteralPath 'static\assets\midoo.ico').Path
& $Python -m PyInstaller --noconfirm --onefile --windowed --name Uninstall --icon $iconPath --distpath 'dist\windows' --workpath '.build\uninstall' --specpath '.build\specs' desktop_installer.py
if ($LASTEXITCODE -ne 0) { throw 'Uninstaller build failed.' }
& $Python tools/package_release.py --output $releasePath --package $packagePath
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
$payloadPath = (Resolve-Path -LiteralPath $packagePath).Path
& $Python -m PyInstaller --noconfirm --onefile --windowed --name "EcommerceProductManager-Setup-$appVersion" --icon $iconPath --add-data "$payloadPath;payload" --distpath $releasePath --workpath '.build\setup' --specpath '.build\specs' desktop_installer.py
if ($LASTEXITCODE -ne 0) { throw 'Setup build failed.' }
& $Python tools/package_release.py --checksums-only --output $releasePath
if ($LASTEXITCODE -ne 0) { throw 'Final release checksums failed.' }
Write-Output ('Built desktop application, native installer/uninstaller, portable archive and verified model archive in '+$releasePath)
