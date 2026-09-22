@echo off
setlocal
cd /d "%~dp0"
set "PYW=%~dp0python\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0python\python.exe"
if not exist "%PYW%" (
  echo Could not find bundled Python.
  echo Re-download Skate Clip Cutter from GitHub Releases.
  pause
  exit /b 1
)
start "" "%PYW%" "%~dp0session_ui.py"
