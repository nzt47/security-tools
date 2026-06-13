@echo off
title 云枢 - Digital Life
cd /d "C:\Users\Administrator\agent"

if not exist logs mkdir logs

set LOG_FILE=logs\server_%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%.log

echo ========================================
echo      Yunshu - Digital Life Web UI
echo ========================================

:: ── 查杀占用 5678 端口的残留进程 ──
echo.
echo [检查] 扫描端口 5678...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5678" ^| findstr "LISTENING"') do (
    echo [清理] 发现残留进程 PID=%%a，正在终止...
    taskkill /F /PID %%a >nul 2>&1 && echo [OK] 已终止进程 %%a || echo [警告] 无法终止进程 %%a
)
timeout /t 1 /nobreak >nul

:: ── 启动云枢服务 ──
echo.
echo Starting server at http://127.0.0.1:5678
echo Log: %LOG_FILE%
echo.
python app_server.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start. Make sure Python is installed.
    pause
)
