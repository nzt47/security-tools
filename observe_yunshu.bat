@echo off
title Yunshu Observer
REM ============================================================
REM  Visible observer window (read-only). Closing it does NOT stop
REM  the backend - it only shows /api/health, /api/version, units,
REM  coverage and the last few backend ERROR lines.
REM
REM  Why this window exists: the one-click launcher hides the backend
REM  console (so scheduled jobs cannot flash black windows), but then
REM  "nothing happens on screen" is indistinguishable from "it failed".
REM  This is the ONE deliberately visible console.
REM
REM  NOTE: keep this file ASCII-only (cmd reads .bat as ANSI).
REM ============================================================
cd /d "%~dp0"
python "%~dp0scripts\yunshu_observe.py"
if errorlevel 1 pause
exit /b 0
