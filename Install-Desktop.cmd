@echo off
setlocal
powershell.exe -NoLogo -NoProfile -File "%~dp0tools\Install-Desktop.ps1" -PackageDirectory "%~dp0" %*
if errorlevel 1 (
  echo Installation failed. Please read the message above.
  pause
  exit /b 1
)
echo Installation complete. Use the desktop shortcut to start the application.
pause
