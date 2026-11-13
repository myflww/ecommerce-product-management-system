[CmdletBinding()]
param([string]$Python = '.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (!(Test-Path -LiteralPath 'models\dinov2-small\model.onnx')) { throw 'Run python tools/download_model.py before building the complete Windows app.' }
$expected = '6266c3cd72db6953cecdcbfeab9422a9f783d96f1a4e296ba70ffbac43b54a18'
if ((Get-FileHash -LiteralPath 'models\dinov2-small\model.onnx' -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Model checksum mismatch.' }
& $Python -m PyInstaller --noconfirm --distpath 'dist\windows' --workpath '.build\windows' Midoo.spec
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }
$iconPath = (Resolve-Path -LiteralPath 'static\assets\midoo.ico').Path
& $Python -m PyInstaller --noconfirm --onefile --windowed --name Uninstall --icon $iconPath --distpath 'dist\windows' --workpath '.build\uninstall' --specpath '.build\specs' desktop_installer.py
if ($LASTEXITCODE -ne 0) { throw 'Uninstaller build failed.' }
& $Python tools/package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
$payloadPath = (Resolve-Path -LiteralPath 'dist\windows-package').Path
& $Python -m PyInstaller --noconfirm --onefile --windowed --name "EcommerceProductManager-Setup-1.6.0" --icon $iconPath --add-data "$payloadPath;payload" --distpath 'dist\release' --workpath '.build\setup' --specpath '.build\specs' desktop_installer.py
if ($LASTEXITCODE -ne 0) { throw 'Setup build failed.' }
& $Python tools/package_release.py --checksums-only
Write-Output 'Built desktop application, native installer/uninstaller, portable archive and verified model archive in dist\release.'
