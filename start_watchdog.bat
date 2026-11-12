@echo off
setlocal
title Yunshu Watchdog (manual)
REM ============================================================
REM  Manual watchdog launcher. NOT a scheduled task and NOT started
REM  by the one-click script - run it only when you want unattended
REM  restarts.
REM
REM  - probes http://127.0.0.1:5678/api/health every 30s
REM  - 3 consecutive failures (about 90s) => kills the stale listener
REM    and launches app_server.py again, waiting for readiness
REM  - appends one JSON line per check to logs\watchdog_yunshu.jsonl
REM  - single instance: scripts\watchdog_yunshu.py checks the lock file
REM    and exits if a live watchdog PID already holds it (a lock left
REM    behind by a reboot is detected via PID liveness and taken over)
REM  - keep this window open; closing it stops the watchdog
REM ============================================================
cd /d "%~dp0"

echo.
echo  Yunshu watchdog (manual)
echo   health : http://127.0.0.1:5678/api/health every 30s
echo   trace  : logs\watchdog_yunshu.jsonl
echo   close this window to stop the watchdog
echo.

python scripts\watchdog_yunshu.py
set "RC=%ERRORLEVEL%"
echo.
echo  [watchdog] exited (code %RC%). Press any key to close.
pause >nul
