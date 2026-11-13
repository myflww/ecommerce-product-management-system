@echo off
setlocal
powershell.exe -NoLogo -NoProfile -File "%~dp0tools\Uninstall-Desktop.ps1" -InstallDirectory "%~dp0." %*
if errorlevel 1 (
  echo Uninstall could not finish. Please close the application and try again.
  pause
  exit /b 1
)
echo Application files and shortcuts removed. Product data has been preserved.
pause
