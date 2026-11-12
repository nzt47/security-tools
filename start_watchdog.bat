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
REM
REM  [2026-11-13 no-window change] The desktop shortcut now points at
REM  scripts\yunshu_launch.vbs watchdog: wscript starts this file with a hidden
REM  window, so the 30s probe loop no longer parks a console on the desktop.
REM  stdout/stderr go to logs\watchdog_console.log (and the launcher log) -
REM  pythonw is deliberately NOT used, so no log line is swallowed. The
REM  structured trace still lands in logs\watchdog_yunshu.jsonl.
REM
REM  NOTE: keep this file ASCII-only (cmd reads .bat as ANSI).
REM ============================================================
cd /d "%~dp0"
set "LOGDIR=%~dp0logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\watchdog_console.log"

echo.
echo  Yunshu watchdog (manual)
echo   health : http://127.0.0.1:5678/api/health every 30s
echo   trace  : logs\watchdog_yunshu.jsonl
echo   console: %LOG%
echo.

python scripts\watchdog_yunshu.py >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo  [watchdog] exited (code %RC%). >> "%LOG%"
exit /b %RC%
