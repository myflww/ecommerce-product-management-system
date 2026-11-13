@echo off
cd /d "%~dp0"
py -3.13 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Installation completed. Double-click start-midoo.cmd.
pause
exit /b
:fail
echo Installation failed. Python 3.13 and internet access are required for source installation.
pause
