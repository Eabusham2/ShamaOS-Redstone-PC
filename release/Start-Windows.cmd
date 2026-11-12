@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>nul
if errorlevel 1 (
  echo Please install Python 3.11 or newer from https://www.python.org/downloads/ and try again.
  pause
  exit /b 1
)
if not exist ".installer-env\Scripts\python.exe" py -3 -m venv .installer-env
if errorlevel 1 goto failed
.installer-env\Scripts\python.exe -m pip install "nbtlib>=2.0,<3"
if errorlevel 1 goto failed
.installer-env\Scripts\python.exe Install-ShamaOS.py %*
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Installation did not finish. Read the message above. Your original save is unchanged.
pause
exit /b 1
