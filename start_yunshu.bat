@echo off
setlocal
title Yunshu One-Click Start
REM ============================================================
REM  One-click start for the LOCAL workbench (production build).
REM
REM  What it does:
REM    1) if the backend is already healthy (GET /api/health == 200) it does
REM       nothing to it (never launch a duplicate);
REM    2) otherwise starts app_server.py in its own console window;
REM    3) waits until /api/health really answers;
REM    4) opens the workbench in the default browser.
REM
REM  Workbench URL:  http://127.0.0.1:5678/chat
REM  (the backend serves the built SPA; there is NO Vite dev server in this flow.
REM   The old script opened http://localhost:5173/static/#/workbench, which is
REM   two different stale assumptions: 5173 is not running in production and
REM   /static/ is not a page route - that is why it looked "broken".)
REM
REM  Watchdog: NOT started here. It has its own desktop shortcut
REM  (Yunshu-Watchdog), start it only when you want unattended restarts.
REM ============================================================
cd /d "%~dp0"
set "ROOT=%~dp0"
set "URL=http://127.0.0.1:5678/chat"

echo.
echo  ==========================================================
echo    Yunshu one-click start
echo    Backend  : http://127.0.0.1:5678
echo    Workbench: %URL%
echo    Watchdog : separate desktop shortcut (manual)
echo  ==========================================================
echo.

curl -s -f -o nul --max-time 2 http://127.0.0.1:5678/api/health >nul 2>&1
if not errorlevel 1 goto backend_up

echo  [1/2] Starting backend (cold start usually takes 60-90s)...
start "Yunshu-Backend-5678" /D "%ROOT%" cmd /k "python app_server.py"
goto wait_backend

:backend_up
echo  [1/2] Backend already healthy - not starting a duplicate.

:wait_backend
set /a _tries=0
echo  [2/2] Waiting for http://127.0.0.1:5678/api/health ...
:wait_loop
set /a _tries+=1
if %_tries% gtr 90 (
    echo  [WARN] Backend not ready after ~180s. Check the "Yunshu-Backend-5678" window.
    goto open_browser
)
curl -s -f -o nul --max-time 2 http://127.0.0.1:5678/api/health >nul 2>&1
if errorlevel 1 (
    ping -n 3 127.0.0.1 >nul
    goto wait_loop
)
echo  OK backend is ready.

:open_browser
start "" "%URL%"
echo.
echo  Opened: %URL%
echo  To stop the API, close the "Yunshu-Backend-5678" console window.
echo.
pause
