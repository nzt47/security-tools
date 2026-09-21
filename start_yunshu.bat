@echo off
title Yunshu One-Click Start
echo.
echo  ======================================================
echo    Yunshu One-Click Start
echo    Backend API  : http://localhost:5678
echo    Frontend Dev : http://localhost:5173/static/
echo    Close the two popup console windows to stop services
echo  ======================================================
echo.

REM ---- 仓库根 = 本脚本所在目录（可移植） ----
REM 【TASK-02 · W1 仓库卫生】原第 12 行是硬编码的
REM   set ROOT=C:\Users\Administrator\agent
REM 这正是本文件此前被 .gitignore 忽略的原因：机器特定。改用 %~dp0
REM （脚本自身所在目录，含末尾反斜杠）后不再机器特定 ⇒ 已从忽略名单移除，
REM 新克隆者可直接使用同一个入口。子进程的工作目录由 start 的 /D 参数指定，
REM 避免 cmd /k 里嵌套引号的解析坑（路径含空格时依然正确，已实测）。
set "ROOT=%~dp0"

REM ---- 0. If backend is already healthy, do NOT start a duplicate ----
REM     NOTE: app_server.py force-kills (taskkill /F) whatever is on 5678
REM     when a NEW instance starts, so double-launching kills the running
REM     backend and the page shows API errors.
curl -s -f -o nul --max-time 2 http://127.0.0.1:5678/api/health >nul 2>&1
if not errorlevel 1 goto backend_up
echo  [1/3] Starting backend (cold start takes about 60-90s)...
start "Yunshu-Backend-5678" /D "%ROOT%" cmd /k "python app_server.py"
:backend_up

REM ---- 2. Start frontend only if port 5173 is free ----
curl -s -f -o nul --max-time 2 http://127.0.0.1:5173/static/ >nul 2>&1
if not errorlevel 1 goto frontend_up
echo  [2/3] Starting frontend Vite dev server...
start "Yunshu-Frontend-5173" /D "%ROOT%yunshu-ui" cmd /k "npm run dev"
:frontend_up

REM ---- 3. Poll until the backend API is REALLY ready, then open browser ----
echo  [3/3] Waiting for backend API on http://127.0.0.1:5678/api/health ...
set /a _tries=0
:wait_backend
set /a _tries+=1
if %_tries% gtr 60 (
    echo  [WARN] Backend not ready after ~120s. Check the "Yunshu-Backend-5678" window for errors.
    goto open_browser
)
curl -s -f -o nul --max-time 2 http://127.0.0.1:5678/api/health >nul 2>&1
if errorlevel 1 (
    ping -n 3 127.0.0.1 >nul
    goto wait_backend
)
echo  OK Backend API is ready, opening workbench...
:open_browser
echo.
start "" "http://localhost:5173/static/#/workbench"

echo.
echo  Started. Keep the two popup console windows open;
echo  closing the backend window stops the API and the page will error.
echo.
pause
