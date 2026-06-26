@echo off
REM Double-click this file to start the WSTI Quiz on Windows.
cd /d "%~dp0"
cls
echo Starting WSTI Quiz...
echo.
python WSTI_Quiz.py 2>nul || py WSTI_Quiz.py 2>nul || (
  echo.
  echo Python is not installed. Get it free from https://www.python.org/downloads/
  echo During install, TICK the box "Add Python to PATH", then run this file again.
)
echo.
pause
