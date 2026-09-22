@echo off
setlocal
cd /d "%~dp0"
set "PYW=%~dp0program\python\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0program\python\python.exe"
if not exist "%PYW%" (
  echo Could not find the program files.
  echo Unzip the whole folder, then run this file from inside it.
  pause
  exit /b 1
)
start "" "%PYW%" "%~dp0program\session_ui.py"
