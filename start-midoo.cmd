@echo off
cd /d "%~dp0"
if exist "Midoo.exe" (
  start "" "%~dp0Midoo.exe"
  exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0desktop.py"
  exit /b
)
echo Please run install.cmd first, or use the packaged Midoo.exe.
pause
