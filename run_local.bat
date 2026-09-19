@echo off
REM Double-click to start the traffic collector.
REM Leave the window open while it runs; Ctrl+C stops it cleanly.

cd /d "%~dp0"
title Traffic collector

echo Checking GitHub for updates...
git pull --ff-only
if errorlevel 1 (
  echo.
  echo Could not update ^(no network, or this folder is not a git clone^).
  echo Running the version already in this folder.
)

echo.
python run_local.py

echo.
echo ---------------------------------------------
echo Collector stopped. Press any key to close.
pause >nul
