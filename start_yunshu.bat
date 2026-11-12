@echo off
setlocal
title Yunshu One-Click Start
REM ============================================================
REM  One-click start for the LOCAL workbench (production build).
REM
REM  What it does:
REM    1) if the backend is already healthy (GET /api/health == 200) it does
REM       nothing to it (never launch a duplicate);
REM    2) otherwise starts app_server.py as a DETACHED hidden process
REM       (logs\backend_boot.out|err.log) - no console window is created;
REM    3) waits until /api/health really answers;
REM    4) opens the workbench in the default browser;
REM    5) exits by itself (no pause: a hidden launcher must not leave a cmd
REM       window parked behind an invisible console).
REM
REM  Workbench URL:  http://127.0.0.1:5678/chat
REM  (the backend serves the built SPA; there is NO Vite dev server in this flow.
REM   The old script opened http://localhost:5173/static/#/workbench, which is
REM   two different stale assumptions: 5173 is not running in production and
REM   /static/ is not a page route - that is why it looked "broken".)
REM
REM  [2026-11-13 no-window change] The desktop shortcut now points at
REM  scripts\yunshu_launch.vbs (wscript starts this file hidden - see that script).
REM  Double-clicking THIS .bat directly still shows a console window; that is cmd
REM  itself, not a backend child. Started via the shortcut, the whole chain is hidden.
REM  The backend is started with Start-Process -WindowStyle Hidden: no visible
REM  window, and its children (gh / maintenance python CLIs) inherit a HIDDEN
REM  console - together with CREATE_NO_WINDOW in agent/utils/proc.py that is the
REM  double safety net for the periodic jobs.
REM
REM  Watchdog: NOT started here. It has its own desktop shortcut
REM  (Yunshu-Watchdog), start it only when you want unattended restarts.
REM
REM  NOTE: keep this file ASCII-only (cmd reads .bat as ANSI; non-ASCII bytes can
REM  swallow a CR/LF and merge the next line into a REM).
REM ============================================================
cd /d "%~dp0"
set "ROOT=%~dp0"
set "URL=http://127.0.0.1:5678/chat"
set "LOGDIR=%ROOT%logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\launcher_start_yunshu.log"
set "PWSH=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

echo.
echo  ==========================================================
echo    Yunshu one-click start (no console window)
echo    Backend  : http://127.0.0.1:5678
echo    Workbench: %URL%
echo    Log      : %LOG%
echo    Watchdog : separate desktop shortcut (manual)
echo  ==========================================================
echo.

curl -s -f -o nul --max-time 2 http://127.0.0.1:5678/api/health >nul 2>&1
if not errorlevel 1 goto backend_up

echo  [1/2] Starting backend detached+hidden (cold start usually takes 60-90s)...
if not exist "%PWSH%" goto no_powershell
REM  Unique boot log names: a fixed name can still be held by a previous boot
REM  (Start-Process then fails with ERROR_SHARING_VIOLATION).
set "BOOT=%RANDOM%%RANDOM%"
set "BOOTOUT=%LOGDIR%\backend_boot_%BOOT%.out.log"
set "BOOTERR=%LOGDIR%\backend_boot_%BOOT%.err.log"
REM  No ">> %LOG%" here on purpose: the hidden launcher (scripts\yunshu_launch.vbs)
REM  already redirects this whole script into logs\launcher_<mode>.log; opening the
REM  same file a second time inside the batch fails with ERROR_SHARING_VIOLATION
REM  ("the process cannot access the file because it is being used by another
REM  process") and the backend would never start.
"%PWSH%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'python' -ArgumentList 'app_server.py' -WorkingDirectory '%ROOT%' -WindowStyle Hidden -RedirectStandardOutput '%BOOTOUT%' -RedirectStandardError '%BOOTERR%'"
goto wait_backend

:no_powershell
echo  [ERROR] PowerShell not found at %PWSH% - cannot start the backend hidden.
goto open_browser

:backend_up
echo  [1/2] Backend already healthy - not starting a duplicate.

:wait_backend
set /a _tries=0
echo  [2/2] Waiting for http://127.0.0.1:5678/api/health ...
:wait_loop
set /a _tries+=1
if %_tries% gtr 90 (
    echo  [WARN] Backend not ready after ~180s. Check logs\backend_boot_*.err.log .
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
echo  Backend logs: logs\backend_boot_*.out.log / .err.log
echo.
exit /b 0
